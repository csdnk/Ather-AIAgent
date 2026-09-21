"""Execution skeleton. Later stage handlers and finalization are not implemented here."""

from __future__ import annotations

from datetime import timedelta
from typing import get_args

from aether_agent_memory.recall.admission import RecallError
from aether_agent_memory.recall.models import RecallExecution
from aether_agent_memory.recall.ports import ExecutionGuard, RecallExecutionStorePort
from aether_agent_memory.runtime.contract_types import ExecutionState, Stage, TerminalState, utcnow

TRANSITIONS: dict[str, frozenset[str]] = {
    "CREATED": frozenset({"RUNNING_REQUEST_VALIDATION"}),
    "RUNNING_REQUEST_VALIDATION": frozenset({"RUNNING_QUERY_EMBEDDING"}),
    "RUNNING_QUERY_EMBEDDING": frozenset({"RUNNING_VECTOR_SEARCH"}),
    "RUNNING_VECTOR_SEARCH": frozenset(
        {
            "RUNNING_CANDIDATE_VALIDATION",
            "RUNNING_CONTEXT_ASSEMBLY",
        }
    ),
    "RUNNING_CANDIDATE_VALIDATION": frozenset(
        {
            "RUNNING_CANONICAL_LOAD",
            "RUNNING_CONTEXT_ASSEMBLY",
        }
    ),
    "RUNNING_CANONICAL_LOAD": frozenset({"RUNNING_RANKING"}),
    "RUNNING_RANKING": frozenset({"RUNNING_CONTEXT_ASSEMBLY"}),
    "RUNNING_CONTEXT_ASSEMBLY": frozenset({"RUNNING_TRACE_FINALIZATION"}),
    "RUNNING_TRACE_FINALIZATION": frozenset(get_args(TerminalState)),
}
for _stage in get_args(Stage):
    if _stage != "RUNNING_TRACE_FINALIZATION":
        TRANSITIONS[_stage] = TRANSITIONS[_stage] | {"RUNNING_TRACE_FINALIZATION"}


def validate_transition(current: ExecutionState, target: ExecutionState) -> None:
    if target not in TRANSITIONS.get(current, frozenset()):
        raise RecallError("INVARIANT_VIOLATION")


def guard_for(execution: RecallExecution) -> ExecutionGuard:
    if execution.lease_token is None:
        raise RecallError("INVARIANT_VIOLATION")
    return ExecutionGuard(
        tenant_id=execution.tenant_id,
        recall_id=execution.recall_id,
        state_version=execution.state_version,
        lease_token=execution.lease_token,
    )


class RecallExecutionService:
    def __init__(self, store: RecallExecutionStorePort) -> None:
        self.store = store

    def start(self, tenant_id: str, recall_id: str, *, owner: str) -> RecallExecution:
        """Claim the accepted execution and stop at request revalidation.

        A later handler must supply verified authorization output/checkpoint
        before advancing to Query. No embedding or Context is produced here.
        """
        admission = self.store.get(tenant_id, recall_id)
        if admission is None:
            raise RecallError("INVARIANT_VIOLATION")
        execution = self.store.claim(
            tenant_id,
            recall_id,
            admission.execution.state_version,
            owner,
            min(utcnow() + timedelta(seconds=1), admission.request.deadline_at),
        )
        if execution.state == "CREATED":
            return self.store.advance(guard_for(execution), "RUNNING_REQUEST_VALIDATION")
        return execution
