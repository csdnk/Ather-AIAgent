"""Metadata-only caller execution state; loading it never executes a story."""

from dataclasses import dataclass
from typing import Literal, Self

import rfc8785
from pydantic import BaseModel, ConfigDict, Field

from aether_agent_memory.remember.contracts.models import (
    DocumentInput,
    MemoryRef,
    RememberReceipt,
    SourceRef,
)
from aether_agent_memory.runtime.contracts.client_runs import ClientOperation, ClientRunRecord
from aether_agent_memory.runtime.contracts.client_states import ClientExecutionState
from aether_agent_memory.runtime.contracts.models import ContractModel, Digest, Identifier, Scope
from aether_agent_memory.runtime.foundation.common import fingerprint

from .definition import RunDefinition


@dataclass(frozen=True)
class RestoredExecution:
    record: ClientRunRecord
    definition: RunDefinition
    envelope: ClientExecutionState
    data: "ExecutionData"
    journal_matches: bool


class RecallHandle(ContractModel):
    recall_id: Identifier
    scope: Scope


class ResolvedResult(ContractModel):
    operation_id: Identifier
    result_type: Literal[
        "RememberReceipt",
        "ContextPack",
        "ConsolidateData",
        "TaskData",
        "ObjectData",
        "MemorySnapshot",
        "DeleteReceipt",
        "DocumentInput",
    ]
    parsed_hash: Digest
    basis: Literal["typed_response", "metadata_without_content"]
    consumed: bool = False


class ExecutionData(BaseModel):
    """Mutable while executing; every saved copy is validated independently."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1] = 1
    step: int = Field(default=0, ge=0, le=32)
    phase: Literal[
        "initial",
        "step_started",
        "before_effect",
        "http_observed",
        "parsed",
        "consumed",
        "step_complete",
    ] = "initial"
    operation_id: Identifier | None = None
    completed_steps: list[int] = Field(default_factory=list, max_length=32)
    scope: Scope | None = None
    refs: dict[Identifier, MemoryRef] = Field(default_factory=dict)
    sources: dict[Identifier, SourceRef] = Field(default_factory=dict)
    receipts: dict[Identifier, RememberReceipt] = Field(default_factory=dict)
    episodes: dict[Identifier, tuple[MemoryRef, ...]] = Field(default_factory=dict)
    recalls: dict[Identifier, RecallHandle] = Field(default_factory=dict)
    documents: dict[Identifier, DocumentInput] = Field(default_factory=dict)
    baselines: dict[Identifier, dict[Identifier, MemoryRef]] = Field(default_factory=dict)
    task_ids: set[Identifier] = Field(default_factory=set)
    task_slots: dict[Identifier, Identifier] = Field(default_factory=dict)
    task_groups: dict[Identifier, list[Identifier]] = Field(default_factory=dict)
    resolved: dict[Identifier, ResolvedResult] = Field(default_factory=dict)

    def parsed(self, operation_id: str, result: BaseModel) -> None:
        self.resolved[operation_id] = describe_result(operation_id, result)
        self.operation_id, self.phase = operation_id, "parsed"

    def consume(self, operation_id: str) -> None:
        original = self.resolved[operation_id]
        self.resolved[operation_id] = original.model_copy(update={"consumed": True})
        self.operation_id, self.phase = operation_id, "consumed"

    def snapshot(self) -> Self:
        return type(self).model_validate(self.model_dump(mode="json"))

    def validate_for(self, record: ClientRunRecord) -> None:
        scope_id = record.scope_id

        def scope_matches(scope: Scope, *, recall: bool = False) -> bool:
            if scope.task_id != scope_id or scope.session_id not in (
                (None, scope_id + "_session") if recall else (scope_id + "_session",)
            ):
                return False
            return self.scope is None or all(
                getattr(scope, key) == getattr(self.scope, key)
                for key in ("tenant_id", "application_id", "user_id", "agent_id")
            )

        if self.scope is not None and not scope_matches(self.scope):
            raise ValueError("execution home scope differs from original run")
        if any(
            key != ref.memory_id or not scope_matches(ref.scope) for key, ref in self.refs.items()
        ):
            raise ValueError("execution memory binding changed")
        if self.refs and self.scope is None:
            raise ValueError("execution memories require their original scope")
        if any(key != source.source_id for key, source in self.sources.items()):
            raise ValueError("execution source binding changed")

        def owned(ref: MemoryRef) -> bool:
            current = self.refs.get(ref.memory_id)
            return (
                current is not None
                and current.scope == ref.scope
                and current.version >= ref.version
            )

        for receipt in self.receipts.values():
            if (
                self.sources.get(receipt.source.source_id) != receipt.source
                or any(not owned(ref) for ref in receipt.memories)
                or not set(receipt.task_ids) <= self.task_ids
            ):
                raise ValueError("original receipt references are incomplete")
        if any(not owned(ref) for group in self.episodes.values() for ref in group):
            raise ValueError("episode group references are incomplete")
        if any(
            key != ref.memory_id or not scope_matches(ref.scope)
            for baseline in self.baselines.values()
            for key, ref in baseline.items()
        ):
            raise ValueError("original catalog baseline scope changed")
        if any(not scope_matches(value.scope, recall=True) for value in self.recalls.values()):
            raise ValueError("original recall scope changed")
        if any(
            not (value.document_id == scope_id or value.document_id.startswith(scope_id + "_"))
            for value in self.documents.values()
        ):
            raise ValueError("original document scope changed")
        tasks = set(self.task_slots.values()) | {
            value for group in self.task_groups.values() for value in group
        }
        if not tasks <= self.task_ids:
            raise ValueError("execution task slots are not owned")
        raw = record.snapshot.get("operations", [])
        assert isinstance(raw, list)
        operations = {item.operation_id: item for item in map(ClientOperation.model_validate, raw)}
        if self.operation_id is not None and self.operation_id not in operations:
            raise ValueError("execution cursor operation was not registered")
        for key, result in self.resolved.items():
            if (
                key != result.operation_id
                or key not in operations
                or operations[key].phase != "observed"
            ):
                raise ValueError("execution result has no original HTTP observation")
        if self.completed_steps != list(range(1, len(self.completed_steps) + 1)) or any(
            step > self.step for step in self.completed_steps
        ):
            raise ValueError("completed execution steps are not an ordered prefix")

    def envelope(self, record: ClientRunRecord) -> ClientExecutionState:
        validated = self.snapshot()
        validated.validate_for(record)
        if record.definition is None or record.state_policy != "p4_state_v1":
            raise ValueError("original execution-state policy is missing")
        raw = record.snapshot.get("operations", [])
        assert isinstance(raw, list)
        head = record.execution_state
        data = validated.model_dump(mode="json")
        data["task_ids"] = sorted(validated.task_ids)
        return ClientExecutionState(
            format_id="p4_state_v1",
            run_id=record.run_id,
            scenario_id=record.scenario_id,
            scope_id=record.scope_id,
            definition_hash=record.definition.content_hash,
            sequence=1 if head is None else head.sequence + 1,
            parent_hash=None if head is None else head.content_hash,
            stream_id=record.state_stream_id,
            parent_stream_id=None if head is None else head.stream_id,
            operations=[ClientOperation.model_validate(value) for value in raw],
            data=data,
        )

    @classmethod
    def from_envelope(cls, state: ClientExecutionState, record: ClientRunRecord) -> Self:
        if (
            record.definition is None
            or record.state_policy != "p4_state_v1"
            or state.run_id != record.run_id
            or state.scenario_id != record.scenario_id
            or state.scope_id != record.scope_id
            or state.definition_hash != record.definition.content_hash
        ):
            raise ValueError("execution state belongs to another definition or run")
        raw = record.snapshot.get("operations", [])
        assert isinstance(raw, list)
        current = [ClientOperation.model_validate(value) for value in raw]
        if len(current) < len(state.operations) or any(
            not original.same_request(present)
            or (original.phase == "observed" and original != present)
            for original, present in zip(state.operations, current, strict=False)
        ):
            raise ValueError("original execution-state journal was changed")
        data = cls.model_validate(state.data)
        data.validate_for(
            record.model_copy(
                update={
                    "snapshot": {
                        **record.snapshot,
                        "operations": [value.model_dump(mode="json") for value in state.operations],
                    }
                }
            )
        )
        return data


def encode_state(state: ClientExecutionState) -> bytes:
    return rfc8785.dumps(state.model_dump(mode="json"))


def describe_result(operation_id: str, result: BaseModel) -> ResolvedResult:
    """Hash a parsed representation without adopting it or retaining its body."""
    payload = result.model_dump(mode="json")
    basis = "typed_response"
    if type(result).__name__ == "MemorySnapshot":
        payload = {key: value for key, value in payload.items() if key != "content"}
        basis = "metadata_without_content"
    return ResolvedResult.model_validate(
        {
            "operation_id": operation_id,
            "result_type": type(result).__name__,
            "parsed_hash": fingerprint(payload),
            "basis": basis,
        }
    )
