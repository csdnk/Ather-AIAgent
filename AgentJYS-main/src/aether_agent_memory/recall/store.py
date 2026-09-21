"""Logical RF adapter over the existing atomic-record Port, not a database driver.

The production transaction/lease durability contract is owned by RF. Tests can
use an in-memory AtomicRecordStore; this adapter does not select SQLite or Redis.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast, get_args
from uuid import uuid4

from aether_agent_memory.recall.admission import RecallAdmission, RecallError, RecallPolicy
from aether_agent_memory.recall.execution import validate_transition
from aether_agent_memory.recall.models import RecallCheckpoint, RecallExecution
from aether_agent_memory.recall.ports import (
    AdmissionSnapshot,
    ExecutionGuard,
    RecallAdmissionPort,
)
from aether_agent_memory.runtime.capability_store import AtomicRecordStore, RecordTransaction
from aether_agent_memory.runtime.contract_types import (
    ExecutionState,
    Stage,
    TerminalState,
    Timestamp,
    hash_json,
    utcnow,
)


def request_index_key(admission: RecallAdmission) -> str:
    index = admission.index
    return hash_json(
        [
            index.principal_ref,
            index.scope_digest.model_dump(),
            index.idempotency_key,
        ]
    ).value


class BoundedRecallAdmission:
    def check(self, snapshot: AdmissionSnapshot, policy: RecallPolicy) -> None:
        reserve = 6 * policy.max_candidates_total + 8
        if (
            snapshot.active_executions >= policy.max_inflight_requests + policy.max_queued_requests
            or snapshot.reserved_events + reserve > policy.outbox_max_events
        ):
            raise RecallError("ADMISSION_BUSY")


class RecordRecallExecutionStore:
    def __init__(
        self,
        records: AtomicRecordStore,
        *,
        clock: Callable[[], Timestamp] = utcnow,
    ) -> None:
        self.records, self.clock = records, clock

    def find(self, tenant_id: str, request_key: str) -> RecallAdmission | None:
        with self.records.transaction() as tx:
            value = tx.get("recall-admission", tenant_id, request_key)
            return None if value is None else RecallAdmission.model_validate_json(value)

    def get(self, tenant_id: str, recall_id: str) -> RecallAdmission | None:
        with self.records.transaction() as tx:
            key = tx.get("recall-locator", tenant_id, recall_id)
            if key is None:
                return None
            return self._load(tx, tenant_id, recall_id)[1]

    @staticmethod
    def _load(tx: RecordTransaction, tenant_id: str, recall_id: str) -> tuple[str, RecallAdmission]:
        key = tx.get("recall-locator", tenant_id, recall_id)
        value = None if key is None else tx.get("recall-admission", tenant_id, key)
        if key is None or value is None:
            raise RecallError("INVARIANT_VIOLATION")
        admission = RecallAdmission.model_validate_json(value)
        if admission.request.tenant_id != tenant_id or admission.execution.recall_id != recall_id:
            raise RecallError("INVARIANT_VIOLATION")
        return key, admission

    def admit(
        self,
        request_key: str,
        candidate: RecallAdmission,
        gate: RecallAdmissionPort,
        authorization_valid_until: Timestamp,
    ) -> RecallAdmission:
        candidate = RecallAdmission.model_validate_json(candidate.model_dump_json())
        if request_key != request_index_key(candidate):
            raise RecallError("INVARIANT_VIOLATION")
        tenant = candidate.request.tenant_id
        with self.records.transaction() as tx:
            if self.clock() >= authorization_valid_until:
                raise RecallError("AUTHORITY_UNAVAILABLE")
            old = tx.get("recall-admission", tenant, request_key)
            if old is not None:
                return RecallAdmission.model_validate_json(old)
            if self.clock() >= candidate.request.deadline_at:
                raise RecallError("DEADLINE_EXCEEDED")
            active = [
                RecallAdmission.model_validate_json(v) for _, _, v in tx.scan("recall-admission")
            ]
            active = [a for a in active if a.execution.state not in get_args(TerminalState)]
            gate.check(
                AdmissionSnapshot(
                    active_executions=len(active),
                    reserved_events=sum(a.reserved_events for a in active),
                ),
                candidate.policy,
            )
            # Unknown finalizations remain active; terminal-but-unacked events
            # need explicit occupancy as well, not a released reservation guess.
            pending_events = len(tx.scan("recall-outbox"))
            if sum(
                a.reserved_events for a in active
            ) + candidate.reserved_events + pending_events > (candidate.policy.outbox_max_events):
                raise RecallError("ADMISSION_BUSY")
            tx.put("recall-admission", tenant, request_key, candidate.model_dump_json())
            tx.put("recall-locator", tenant, candidate.request.recall_id, request_key)
            return candidate

    def claim(
        self,
        tenant_id: str,
        recall_id: str,
        expected_version: int,
        owner: str,
        lease_until: Timestamp,
    ) -> RecallExecution:
        with self.records.transaction() as tx:
            key, admission = self._load(tx, tenant_id, recall_id)
            execution = admission.execution
            now = self.clock()
            if (
                execution.state in get_args(TerminalState)
                or execution.state_version != expected_version
            ):
                raise RecallError("INVARIANT_VIOLATION")
            if now >= execution.execution_deadline_at or lease_until <= now:
                raise RecallError("DEADLINE_EXCEEDED")
            if execution.lease_until is not None and execution.lease_until > now:
                raise RecallError("ADMISSION_BUSY")
            active_leases = [
                RecallAdmission.model_validate_json(v).execution
                for _, _, v in tx.scan("recall-admission")
            ]
            if (
                sum(
                    e.lease_until is not None
                    and e.lease_until > now
                    and e.state not in get_args(TerminalState)
                    for e in active_leases
                )
                >= admission.policy.max_inflight_requests
            ):
                raise RecallError("ADMISSION_BUSY")
            execution = execution.replaced(
                lease_owner=owner,
                lease_token=uuid4().hex,
                lease_until=min(lease_until, execution.execution_deadline_at),
                state_version=execution.state_version + 1,
            )
            tx.put(
                "recall-admission",
                tenant_id,
                key,
                admission.replaced(execution=execution).model_dump_json(),
            )
            return execution

    def _guard(self, execution: RecallExecution, guard: ExecutionGuard) -> None:
        now = self.clock()
        if now >= execution.execution_deadline_at:
            raise RecallError("DEADLINE_EXCEEDED")
        if (
            execution.state in get_args(TerminalState)
            or execution.state_version != guard.state_version
            or execution.lease_token != guard.lease_token
            or execution.lease_until is None
            or now >= execution.lease_until
        ):
            raise RecallError("INVARIANT_VIOLATION")

    @staticmethod
    def _check_output(
        admission: RecallAdmission,
        checkpoint: RecallCheckpoint,
        output_json: str,
    ) -> None:
        request = admission.request
        if (
            checkpoint.tenant_id != request.tenant_id
            or checkpoint.recall_id != request.recall_id
            or checkpoint.request_id != request.request_id
            or checkpoint.trace_id != request.trace_id
            or checkpoint.policy_version != request.policy_version
            or (
                checkpoint.stage == "RUNNING_REQUEST_VALIDATION"
                and checkpoint.input_digest != request.request_fingerprint
            )
            or checkpoint.attempt < 1
            or checkpoint.output_digest != hash_json(json.loads(output_json))
        ):
            raise RecallError("INVARIANT_VIOLATION")

    def save_checkpoint(
        self,
        guard: ExecutionGuard,
        checkpoint: RecallCheckpoint,
        output_json: str,
    ) -> RecallExecution:
        checkpoint = RecallCheckpoint.model_validate_json(checkpoint.model_dump_json())
        with self.records.transaction() as tx:
            key, admission = self._load(tx, guard.tenant_id, guard.recall_id)
            execution = admission.execution
            self._guard(execution, guard)
            self._check_output(admission, checkpoint, output_json)
            if checkpoint.stage != execution.state or not (
                admission.request.created_at <= checkpoint.completed_at <= self.clock()
            ):
                raise RecallError("INVARIANT_VIOLATION")
            existing = execution.checkpoint_refs.get(checkpoint.stage)
            if existing is not None and existing != checkpoint.checkpoint_ref:
                raise RecallError("INVARIANT_VIOLATION")
            for namespace, ref, value in (
                ("recall-output", checkpoint.output_ref, output_json),
                ("recall-checkpoint", checkpoint.checkpoint_ref, checkpoint.model_dump_json()),
            ):
                saved = tx.get(namespace, guard.tenant_id, ref)
                if saved is not None and saved != value:
                    raise RecallError("INVARIANT_VIOLATION")
                tx.put(namespace, guard.tenant_id, ref, value)
            execution = execution.replaced(
                checkpoint_refs={
                    **execution.checkpoint_refs,
                    checkpoint.stage: checkpoint.checkpoint_ref,
                },
                state_version=execution.state_version + 1,
            )
            tx.put(
                "recall-admission",
                guard.tenant_id,
                key,
                admission.replaced(execution=execution).model_dump_json(),
            )
            return execution

    def advance(self, guard: ExecutionGuard, target: ExecutionState) -> RecallExecution:
        with self.records.transaction() as tx:
            key, admission = self._load(tx, guard.tenant_id, guard.recall_id)
            execution = admission.execution
            self._guard(execution, guard)
            validate_transition(execution.state, target)
            # Terminal transitions need an atomic Pack/Trace/Outbox commit;
            # this skeleton deliberately cannot perform that operation.
            if target in get_args(TerminalState):
                raise RecallError("INVARIANT_VIOLATION")
            if execution.state != "CREATED":
                if execution.state not in get_args(Stage):
                    raise RecallError("INVARIANT_VIOLATION")
                ref = execution.checkpoint_refs.get(cast(Stage, execution.state))
                saved = tx.get("recall-checkpoint", guard.tenant_id, ref or "")
                if saved is None:
                    raise RecallError("INVARIANT_VIOLATION")
                checkpoint = RecallCheckpoint.model_validate_json(saved)
                output = tx.get("recall-output", guard.tenant_id, checkpoint.output_ref)
                if output is None or checkpoint.stage != execution.state:
                    raise RecallError("INVARIANT_VIOLATION")
                self._check_output(admission, checkpoint, output)
            if (
                target == "RUNNING_CONTEXT_ASSEMBLY"
                and execution.state
                in (
                    "RUNNING_VECTOR_SEARCH",
                    "RUNNING_CANDIDATE_VALIDATION",
                )
                and not admission.execution.confirmed_empty
            ):
                raise RecallError("INVARIANT_VIOLATION")
            execution = execution.replaced(state=target, state_version=execution.state_version + 1)
            tx.put(
                "recall-admission",
                guard.tenant_id,
                key,
                admission.replaced(execution=execution).model_dump_json(),
            )
            return execution
