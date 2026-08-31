from __future__ import annotations

from typing import Protocol

from aether_agent_memory.context import ContextRequest
from aether_agent_memory.context_store.access import uri_is_visible_to_scope
from aether_agent_memory.context_store.models import ContextItemKind
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.memory.retrieval.models import RecallCandidate
from aether_agent_memory.runtime.request_context import RequestContext


class RecallScoreNormalizer(Protocol):
    def normalize(
        self,
        candidate: RecallCandidate,
        request: ContextRequest,
        context: RequestContext,
    ) -> RecallCandidate: ...


class RecallCandidatePolicy(Protocol):
    def allow(
        self,
        candidate: RecallCandidate,
        request: ContextRequest,
        context: RequestContext,
    ) -> bool: ...


class RecallRanker(Protocol):
    def rank(self, candidates: list[RecallCandidate]) -> list[RecallCandidate]: ...


class RecallCandidateFuser(Protocol):
    def fuse(self, candidates: list[RecallCandidate]) -> list[RecallCandidate]: ...


class IdentityScoreNormalizer:
    """Keep provider scores unchanged until a calibrated scorer is configured."""

    def normalize(
        self,
        candidate: RecallCandidate,
        request: ContextRequest,
        context: RequestContext,
    ) -> RecallCandidate:
        return candidate


class AllowAllRecallPolicy:
    """Compatibility policy; source-specific filtering remains in each source."""

    def allow(
        self,
        candidate: RecallCandidate,
        request: ContextRequest,
        context: RequestContext,
    ) -> bool:
        return True


class ScopeRecallPolicy:
    """Fail closed when a production-scoped recall source returns foreign data."""

    def allow(
        self,
        candidate: RecallCandidate,
        request: ContextRequest,
        context: RequestContext,
    ) -> bool:
        del request
        expected = context.scope
        fields = ("tenant_id", "user_id", "agent_id")
        if not all(getattr(expected, field) for field in fields):
            # Compatibility for direct legacy retrieval calls. The formal
            # BuildContext use case requires a complete agent scope.
            return True

        if candidate.memory is not None:
            memory = candidate.memory
            if any(
                getattr(memory, field) != getattr(expected, field) for field in fields
            ):
                return False
            return not (
                memory.type == MemoryType.WORKING
                and expected.session_id is not None
                and memory.session_id != expected.session_id
            )

        if candidate.context_uri is not None:
            return uri_is_visible_to_scope(candidate.context_uri, expected)

        if candidate.scope is not None:
            if any(
                getattr(candidate.scope, field) != getattr(expected, field)
                for field in fields
            ):
                return False
            return not (
                candidate.context_kind == ContextItemKind.SESSION
                and expected.session_id is not None
                and candidate.scope.session_id != expected.session_id
            )

        metadata_scope = tuple(candidate.trace_metadata.get(field) for field in fields)
        if all(metadata_scope):
            return metadata_scope == tuple(getattr(expected, field) for field in fields)
        return False


class ScoreRecallRanker:
    """Stable score-descending ranker matching the pre-pipeline behavior."""

    def rank(self, candidates: list[RecallCandidate]) -> list[RecallCandidate]:
        return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)


class HighestScoreRecallFuser:
    """Collapse duplicate logical context while retaining the strongest hit."""

    def fuse(self, candidates: list[RecallCandidate]) -> list[RecallCandidate]:
        fused: dict[tuple[str, ...], RecallCandidate] = {}
        for candidate in candidates:
            key = _candidate_identity(candidate)
            current = fused.get(key)
            if current is None or candidate.score > current.score:
                fused[key] = candidate
        return list(fused.values())


class RecallPipeline:
    """Provider-neutral stages between recall sources and ContextPack assembly."""

    def __init__(
        self,
        *,
        normalizer: RecallScoreNormalizer | None = None,
        policy: RecallCandidatePolicy | None = None,
        fuser: RecallCandidateFuser | None = None,
        ranker: RecallRanker | None = None,
    ) -> None:
        self._normalizer = normalizer or IdentityScoreNormalizer()
        self._policy = policy or ScopeRecallPolicy()
        self._fuser = fuser or HighestScoreRecallFuser()
        self._ranker = ranker or ScoreRecallRanker()

    def process(
        self,
        candidates: list[RecallCandidate],
        request: ContextRequest,
        context: RequestContext,
    ) -> list[RecallCandidate]:
        normalized = [
            self._normalizer.normalize(candidate, request, context)
            for candidate in candidates
        ]
        filtered = [
            candidate
            for candidate in normalized
            if self._policy.allow(candidate, request, context)
        ]
        return self._ranker.rank(self._fuser.fuse(filtered))[: request.max_candidates]


def _candidate_identity(candidate: RecallCandidate) -> tuple[str, ...]:
    if candidate.context_uri is not None:
        return ("uri", str(candidate.context_uri))
    if candidate.memory is not None:
        return ("memory", candidate.memory.id)
    if candidate.content_ref is not None:
        return ("content_ref", candidate.content_ref)
    return ("source", candidate.source, candidate.memory_id)
