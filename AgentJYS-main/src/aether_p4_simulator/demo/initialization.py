"""Recover only runs proven not to have admitted a business operation or published state."""

import re
from dataclasses import dataclass
from uuid import UUID

from aether_agent_memory.runtime.contracts.client_recoveries import (
    ClientRunRecoveryResult,
    PrepareClientRecovery,
)
from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_agent_memory.runtime.contracts.client_states import ClientExecutionState
from aether_agent_memory.runtime.contracts.client_transfers import (
    ClientRunTransferResult,
    client_run_hash,
)
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError

from .definition import RunDefinition
from .models import Diagnostics, RunSnapshot
from .registry import RunRegistry
from .state import ExecutionData


@dataclass
class InitializationAttempt:
    transfer_id: str
    original: ClientRunRecord | None = None


@dataclass(frozen=True)
class InitializedRun:
    record: ClientRunRecord
    activation: ClientRunRecoveryResult


def unsafe() -> ValidationError:
    return ValidationError(
        409, "initialization_not_safe", "原运行已开始、证据不完整或发生变化，不能从头恢复"
    )


def unconfirmed(transfer_id: str) -> ValidationError:
    return ValidationError(
        409,
        "initialization_unconfirmed",
        "原初始化恢复结果未确认，请继续核对同一接管标识",
        operation_id=transfer_id,
        write_outcome="unconfirmed",
    )


def require_unstarted(record: ClientRunRecord) -> RunSnapshot:
    try:
        run = RunSnapshot.model_validate(record.snapshot)
    except ValueError:
        raise unsafe() from None
    if (
        record.scope_policy != "p4_task_v1"
        or record.state_policy != "p4_state_v1"
        or not re.fullmatch(r"p4r_[0-9a-f]{32}", record.scope_id)
        or record.definition is None
        or record.ownership is None
        or run.run_id != str(record.run_id)
        or run.scenario_id != record.scenario_id
        or run.state not in {"queued", "running", "unconfirmed"}
        or run.operations
        or record.execution_state is not None
        or run.current_step != 0
        or run.total_steps != len(run.steps)
        or run.mode is not None
        or run.diagnostics != Diagnostics()
        or any(
            row.calls or row.state != "unexecuted" or row.http_statuses or row.step_ids
            for row in run.coverage
        )
        or any(
            step.id != index
            or step.state not in {"pending", "skipped"}
            or step.response_text
            or step.checks
            or step.evidence is not None
            for index, step in enumerate(run.steps, 1)
        )
    ):
        raise unsafe()
    return run


def definition(payload: bytes, record: ClientRunRecord) -> RunDefinition:
    try:
        value = RunDefinition.model_validate_json(payload)
    except (ValueError, RecursionError):
        raise unsafe() from None
    value.require_execution(record)
    snapshot = require_unstarted(record)
    if tuple(step.user_text for step in snapshot.steps) != value.texts:
        raise unsafe()
    return value


def require_transfer(
    result: ClientRunTransferResult, attempt: InitializationAttempt, run_id: UUID, owner_id: str
) -> ClientRunRecord:
    if (
        result.run_id != run_id
        or result.transfer_id != attempt.transfer_id
        or result.request.new_owner_id != owner_id
        or (attempt.original is not None and result.previous != attempt.original)
    ):
        raise unsafe()
    require_unstarted(result.previous)
    attempt.original = result.previous
    return result.record


def prepare_initialization(
    client: P3ValidationClient,
    registry: RunRegistry,
    attempt: InitializationAttempt,
    run_id: UUID,
    owner_id: str,
    original_definition: bytes | None,
) -> InitializedRun:
    """Resolve original receipts before dispatch; no missing lookup proves absent effects."""
    transfer_id = attempt.transfer_id
    result = client.lookup_client_transfer(run_id, transfer_id).result
    if result is None:
        original = attempt.original or registry.get(str(run_id))
        require_unstarted(original)
        if original_definition is not None:
            definition(original_definition, original)
        attempt.original = original
        try:
            result = client.transfer_client_run(
                original, new_owner_id=owner_id, transfer_id=transfer_id
            )
        except ValidationError as error:
            if error.write_outcome != "unconfirmed":
                raise
            try:
                result = client.lookup_client_transfer(run_id, transfer_id).result
            except ValidationError:
                raise unconfirmed(transfer_id) from None
            if result is None:
                raise unconfirmed(transfer_id) from None
    held = require_transfer(result, attempt, run_id, owner_id)
    envelope = None if held.snapshot["state"] == "queued" else ExecutionData().envelope(held)
    intent = PrepareClientRecovery(
        expected_owner_id=held.owner_id,
        expected_revision=held.revision,
        expected_record_hash=client_run_hash(held),
        target_state="queued" if envelope is None else "running",
        execution_state=envelope,
    ).intent()
    recovered = client.lookup_client_recovery(run_id, transfer_id).result
    if recovered is None:
        if registry.get(str(run_id)) != held:
            raise unsafe()
        payload = (
            original_definition
            if original_definition is not None
            else client.read_recovery_definition(held).payload
        )
        definition(payload, held)
        try:
            client.confirm_recovery_definition(held, original_definition)
        except ValidationError as error:
            if error.write_outcome != "unconfirmed":
                raise
            try:
                observed = client.read_recovery_definition(held)
            except ValidationError:
                raise unconfirmed(transfer_id) from None
            if observed.evidence.state != "ready" or observed.payload != payload:
                raise unconfirmed(transfer_id) from None
        try:
            recovered = client.activate_client_run(held, envelope)
        except ValidationError as error:
            if error.write_outcome != "unconfirmed":
                raise
            try:
                recovered = client.lookup_client_recovery(run_id, transfer_id).result
            except ValidationError:
                raise unconfirmed(transfer_id) from None
            if recovered is None:
                raise unconfirmed(transfer_id) from None
    if recovered.previous != held or recovered.request != intent:
        raise unsafe()
    # An earlier activation receipt still requires actual original objects and current ownership.
    payload = client.read_client_definition(held)
    definition(payload, held)
    if original_definition is not None and payload != original_definition:
        raise unsafe()
    if envelope is not None:
        actual = ClientExecutionState.from_bytes(client.read_client_state(recovered.record))
        if actual != envelope:
            raise unsafe()
    current = registry.get(str(run_id))
    if (
        current.owner_id != owner_id
        or current.ownership != recovered.record.ownership
        or current.revision < recovered.record.revision
        or (current.revision == recovered.record.revision and current != recovered.record)
        or any(
            getattr(current, key) != getattr(held, key)
            for key in (
                "run_id",
                "scope_id",
                "scenario_id",
                "definition",
                "auth_epoch",
                "scope_policy",
                "state_policy",
            )
        )
    ):
        raise unsafe()
    return InitializedRun(current, recovered)
