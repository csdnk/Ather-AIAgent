from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator

from aether_agent_memory.application.scope import require_agent_scope
from aether_agent_memory.context_store import (
    AetherUri,
    ContextDeleteResult,
    ContextItemKind,
    ContextLayer,
    ContextProjectionWorkItem,
    skill_uri,
)
from aether_agent_memory.context_store.errors import ContextRevisionConflictError
from aether_agent_memory.context_store.ports import (
    ContextProjectionQueuePort,
    SemanticIndexPort,
)
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.runtime.errors import ConflictError
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.skill.models import SkillRecord
from aether_agent_memory.skill.ports import SkillStorePort


class SkillRegistration(BaseModel):
    """P3-owned input for registering an agent capability descriptor."""

    skill_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    instructions: str = ""
    scope: Scope
    version: str = Field(default="1", min_length=1)
    enabled: bool = True
    source: str = "api"
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _requires_agent_scope(self) -> SkillRegistration:
        if any(
            getattr(self.scope, field) is None
            for field in ("tenant_id", "user_id", "agent_id")
        ):
            raise ValueError("skill requires tenant/user/agent scope")
        return self


class RegisterSkillUseCase:
    """Persist a versioned Skill descriptor without coupling to its executor."""

    def __init__(
        self,
        *,
        skills: SkillStorePort,
        context_projection_queue: ContextProjectionQueuePort | None = None,
    ) -> None:
        self._skills = skills
        self._context_projection_queue = context_projection_queue

    async def execute(
        self,
        registration: SkillRegistration,
        context: RequestContext,
    ) -> SkillRecord:
        scope = require_agent_scope(
            context,
            expected=registration.scope,
            operation="skill registration",
        )
        existing = await self._skills.get(scope, registration.skill_id)
        revision = await self._skills.reserve_revision(
            scope,
            registration.skill_id,
            minimum=existing.revision if existing is not None else 0,
        )
        record = SkillRecord(
            skill_id=registration.skill_id,
            name=registration.name,
            description=registration.description,
            instructions=registration.instructions,
            scope=scope,
            version=registration.version,
            enabled=registration.enabled,
            source=registration.source,
            revision=revision,
            metadata={
                **registration.metadata,
                "registered_by_request": context.request_id,
            },
            created_at=existing.created_at if existing is not None else datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        try:
            await self._skills.upsert(record)
        except ContextRevisionConflictError as exc:
            raise ConflictError(str(exc), trace_id=context.trace_id) from exc
        if self._context_projection_queue is not None:
            try:
                await self._context_projection_queue.enqueue(
                    ContextProjectionWorkItem(
                        uri=skill_uri(scope, record.skill_id),
                        source_revision=record.revision,
                        scope=scope,
                        layers=[ContextLayer.ABSTRACT, ContextLayer.OVERVIEW],
                    )
                )
            except Exception:
                return record.model_copy(
                    update={
                        "metadata": {
                            **record.metadata,
                            "context_projection_status": "queue_failed",
                        }
                    }
                )
            return record.model_copy(
                update={
                    "metadata": {
                        **record.metadata,
                        "context_projection_status": "pending",
                    }
                }
            )
        return record


class DeleteSkillUseCase:
    """Delete a P3 Skill descriptor and invalidate its derived index entry."""

    def __init__(
        self,
        *,
        skills: SkillStorePort,
        semantic_index: SemanticIndexPort | None = None,
    ) -> None:
        self._skills = skills
        self._semantic_index = semantic_index

    async def execute(
        self,
        skill_id: str,
        context: RequestContext,
    ) -> ContextDeleteResult:
        scope = require_agent_scope(context, operation="skill deletion")
        uri = skill_uri(scope, skill_id)
        existing = await self._skills.get(scope, skill_id)
        if existing is None:
            return ContextDeleteResult(
                uri=uri,
                kind=ContextItemKind.SKILL,
                deleted=False,
            )
        deleted = await self._skills.delete(scope, skill_id)
        invalidated = (
            await _invalidate_index(self._semantic_index, uri) if deleted else False
        )
        return ContextDeleteResult(
            uri=uri,
            kind=ContextItemKind.SKILL,
            deleted=deleted,
            index_invalidated=invalidated,
        )


async def _invalidate_index(
    semantic_index: SemanticIndexPort | None,
    uri: AetherUri,
) -> bool:
    if semantic_index is None:
        return False
    try:
        await semantic_index.remove(uri)
    except Exception:
        return False
    return True
