"""A-owned logical contracts; RF supplies the atomic storage guarantees.

Mutations must check identity, deadline, lease and state version at commit time.
An ambiguous write raises RecallWriteUnknownError, never a fabricated rollback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from aether_agent_memory.runtime.contract_types import (
    ContractModel,
    ExecutionState,
    Identifier,
    Timestamp,
    UInt,
)

if TYPE_CHECKING:
    from aether_agent_memory.recall.admission import RecallAdmission, RecallPolicy
    from aether_agent_memory.recall.models import RecallCheckpoint, RecallExecution


class RecallWriteUnknownError(RuntimeError):
    """RF cannot yet prove whether a mutation committed. Query the original key."""


class RecallAdmissionUnconfirmedError(RuntimeError):
    """Transport-level unavailability, not a Recall business terminal state.

    Retry only with the original request key. No execution or Context is asserted.
    """


class AdmissionSnapshot(ContractModel):
    active_executions: UInt
    reserved_events: UInt


class RecallAdmissionPort(Protocol):
    def check(self, snapshot: AdmissionSnapshot, policy: RecallPolicy) -> None:
        """Synchronous, side-effect-free gate evaluated within atomic admission."""
        ...


class ExecutionGuard(ContractModel):
    tenant_id: Identifier
    recall_id: Identifier
    state_version: UInt
    lease_token: Identifier


class RecallExecutionStorePort(Protocol):
    def find(self, tenant_id: str, request_key: str) -> RecallAdmission | None:
        """None means no bound/in-flight admission; unresolved intent raises unknown.

        RF must preserve pending identity across client restarts and fence late
        admission writes. A stale replica's not-found is not authoritative absence.
        """
        ...

    def get(self, tenant_id: str, recall_id: str) -> RecallAdmission | None: ...

    def admit(
        self,
        request_key: str,
        candidate: RecallAdmission,
        gate: RecallAdmissionPort,
        authorization_valid_until: Timestamp,
    ) -> RecallAdmission:
        """Return existing binding, or atomically reserve and save all initial records.

        Existing binding wins even when capacity is full. Conflicting semantics
        must never overwrite it. Competing callers reserve capacity only once.
        """
        ...

    def claim(
        self,
        tenant_id: str,
        recall_id: str,
        expected_version: int,
        owner: str,
        lease_until: Timestamp,
    ) -> RecallExecution: ...

    def save_checkpoint(
        self,
        guard: ExecutionGuard,
        checkpoint: RecallCheckpoint,
        output_json: str,
    ) -> RecallExecution:
        """Atomically save immutable output/checkpoint and its execution reference."""
        ...

    def advance(self, guard: ExecutionGuard, target: ExecutionState) -> RecallExecution:
        """CAS only after a verified current-stage checkpoint. No terminal commit."""
        ...


class RecallReplayPort(Protocol):
    async def revalidate(self, tenant_id: str, recall_id: str, principal_ref: str) -> None:
        """Future read-only authorization + B-fact check; no body replay in batch one."""
        ...
