"""Read original effect evidence. A report is never an execution/ownership token."""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel

from aether_agent_memory.remember.contracts.models import (
    DeleteReceipt,
    DocumentInput,
    RememberReceipt,
)
from aether_agent_memory.runtime.contracts.client_runs import ClientOperation, ClientRunRecord
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    EffectStatus,
    TaskOperationView,
    TaskState,
)
from aether_agent_memory.runtime.contracts.mutation_receipts import MutationLookup
from aether_agent_memory.runtime.contracts.operation_lookup import OperationLookup
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.confirmation import RequestConfirmation
from aether_p4_simulator.validation.errors import ValidationError
from aether_p4_simulator.validation.models import ConsolidateData, ContextPack
from aether_p4_simulator.validation.story_models import ObjectData, TaskData

from .state import ResolvedResult, RestoredExecution, describe_result

Stage = Literal["input", "request", "admission", "task", "result"]
FindingState = Literal[
    "unconfirmed",
    "mismatch",
    "pending",
    "failed",
    "attention_required",
    "result_available",
    "result_unavailable",
]


class ReconciliationError(ContractModel):
    stage: Stage
    status: int
    code: str


class OperationFinding(ContractModel):
    intent: ClientOperation
    state: FindingState = "unconfirmed"
    input_state: Literal["matched", "unavailable", "mismatch"] = "unavailable"
    confirmation: RequestConfirmation | None = None
    admission_state: Literal["not_checked", "matched", "mismatch", "unconfirmed"] = "not_checked"
    task: TaskOperationView | None = None
    saved_result: ResolvedResult | None = None
    observed_result: ResolvedResult | None = None
    result_match: Literal["not_captured", "matched", "mismatch", "unavailable"] = "unavailable"
    result_validation: Literal["typed_response", "committed_metadata"] | None = None
    errors: tuple[ReconciliationError, ...] = ()


@dataclass(frozen=True)
class ReconciledExecution:
    restored: RestoredExecution
    findings: tuple[OperationFinding, ...]


_MODELS: dict[str, type[BaseModel]] = {
    "remember.save": RememberReceipt,
    "remember.correct": RememberReceipt,
    "remember.document": DocumentInput,
    "recall.execute": ContextPack,
    "remember.consolidate": ConsolidateData,
    "remember.distill": TaskData,
    "remember.reprocess": TaskData,
    "remember.reindex": TaskData,
    "remember.retention": ObjectData,
    "remember.reflection": ObjectData,
    "remember.delete": DeleteReceipt,
    "source.delete": DeleteReceipt,
    "source.revoke": DeleteReceipt,
}


def _failure(item: OperationFinding, stage: Stage, error: ValidationError) -> OperationFinding:
    if error.code == "reconciliation_timeout":
        raise error
    mismatch = error.code == "upstream_protocol_error" or item.state == "mismatch"
    return item.model_copy(
        update={
            "state": "mismatch" if mismatch else "result_unavailable",
            "errors": (
                *item.errors,
                ReconciliationError(stage=stage, status=error.status, code=error.code),
            ),
        }
    )


def _protocol() -> ValidationError:
    return ValidationError(502, "upstream_protocol_error", "原操作或结果绑定不一致")


def _request(
    client: P3ValidationClient, record: ClientRunRecord, item: OperationFinding
) -> OperationFinding:
    try:
        confirmation = client.confirm_operation(item.intent)
    except ValidationError as error:
        return _failure(item, "request", error)
    item = item.model_copy(update={"confirmation": confirmation})
    if confirmation.state != "matched":
        return item.model_copy(
            update={"state": "mismatch" if item.state == "mismatch" else confirmation.state}
        )
    lookup = confirmation.lookup
    evidence = (
        lookup.http_request
        if isinstance(lookup, OperationLookup)
        else lookup.receipt.http_request
        if isinstance(lookup, MutationLookup) and lookup.receipt
        else None
    )
    admission = evidence.client_run if evidence else None
    if admission is None:
        return item.model_copy(
            update={
                "admission_state": "unconfirmed",
                "state": "mismatch" if item.state == "mismatch" else "unconfirmed",
            }
        )
    if (
        admission.run_id != record.run_id
        or admission.owner_id
        != (record.ownership.owner_at(admission.revision) if record.ownership else record.owner_id)
        or admission.revision > record.revision
    ):
        return item.model_copy(update={"admission_state": "mismatch", "state": "mismatch"})
    return item.model_copy(update={"admission_state": "matched"})


def _command_result(
    client: P3ValidationClient, lookup: OperationLookup, item: OperationFinding
) -> OperationFinding:
    assert lookup.job_id is not None
    try:
        task = client.operation(lookup.job_id, timeout_seconds=60)
    except ValidationError as error:
        return _failure(item, "task", error)
    item = item.model_copy(update={"task": task})
    if (
        task.kind != lookup.kind
        or task.input_hash != lookup.input_hash
        or task.temporal.workflow_id != lookup.workflow_id
        or (
            task.temporal.binding is not None
            and task.temporal.binding.input_hash != lookup.input_hash
        )
    ):
        return _failure(item, "task", _protocol())
    if task.effect_status == EffectStatus.UNKNOWN or task.state == TaskState.ATTENTION:
        return item.model_copy(update={"state": "attention_required"})
    if task.state in {TaskState.FAILED, TaskState.CANCELLED}:
        return item.model_copy(update={"state": "failed"})
    if task.state != TaskState.SUCCEEDED:
        return item.model_copy(update={"state": "pending"})
    try:
        result = client.operation_result(lookup.job_id, _MODELS[lookup.kind], timeout_seconds=60)
        if isinstance(result, RememberReceipt) and result.operation_id != item.intent.operation_id:
            raise _protocol()
        if isinstance(result, ContextPack) and result.recall_id != lookup.job_id:
            raise _protocol()
        if isinstance(result, DocumentInput):
            assert lookup.http_request is not None
            target = urlsplit(lookup.http_request.target)
            if (
                target.path != f"/p3/documents/{result.document_id}"
                or parse_qs(target.query).get("version") != [result.document_version]
                or result.expected_hash != item.intent.request_hash
            ):
                raise _protocol()
        descriptor = describe_result(item.intent.operation_id, result)
    except ValidationError as error:
        return _failure(item, "result", error)
    return _resolved(item, descriptor, "typed_response")


def _mutation_result(
    client: P3ValidationClient, lookup: MutationLookup, item: OperationFinding
) -> OperationFinding:
    try:
        original = client.mutation_result(item.intent.operation_id, lookup.kind)
        if original.receipt != lookup.receipt:
            raise _protocol()
        if lookup.kind == "remember.lifecycle":
            # The P3 contract validates the original metadata hash. It deliberately
            # has no body and cannot be parsed as a hydrated MemorySnapshot.
            descriptor = ResolvedResult(
                operation_id=item.intent.operation_id,
                result_type="MemorySnapshot",
                parsed_hash=original.receipt.result_hash,
                basis="metadata_without_content",
            )
            return _resolved(item, descriptor, "committed_metadata")
        result = _MODELS[lookup.kind].model_validate(original.response)
        if isinstance(result, DeleteReceipt) and result.operation_id != item.intent.operation_id:
            raise _protocol()
        if (
            isinstance(result, (ConsolidateData, DeleteReceipt))
            and result.task_ids != original.receipt.task_ids
        ):
            raise _protocol()
        if isinstance(result, TaskData) and original.receipt.task_ids != (result.task_id,):
            raise _protocol()
        descriptor = describe_result(item.intent.operation_id, result)
    except ValidationError as error:
        return _failure(item, "result", error)
    except ValueError:
        return _failure(item, "result", _protocol())
    return _resolved(item, descriptor, "typed_response")


def _resolved(
    item: OperationFinding,
    descriptor: ResolvedResult,
    validation: Literal["typed_response", "committed_metadata"],
) -> OperationFinding:
    saved = item.saved_result
    match = (
        "not_captured"
        if saved is None
        else "matched"
        if saved.model_copy(update={"consumed": False}) == descriptor
        else "mismatch"
    )
    return item.model_copy(
        update={
            "state": "mismatch" if match == "mismatch" else "result_available",
            "observed_result": descriptor,
            "result_match": match,
            "result_validation": validation,
        }
    )


def reconcile_operations(
    client: P3ValidationClient, restored: RestoredExecution
) -> tuple[OperationFinding, ...]:
    """Inspect the full current journal, including the tail after the saved state."""
    raw = restored.record.snapshot.get("operations", [])
    assert isinstance(raw, list)
    findings = []
    for intent in map(ClientOperation.model_validate, raw):
        item = OperationFinding(
            intent=intent, saved_result=restored.data.resolved.get(intent.operation_id)
        )
        try:
            # ValidationClient checks exact bytes and the receipt hash; no bytes
            # or parsed business bodies escape into the reconciliation report.
            client.read_client_input(restored.record.run_id, intent)
            item = item.model_copy(update={"input_state": "matched"})
        except ValidationError as error:
            item = _failure(item, "input", error)
            if item.state == "mismatch":
                item = item.model_copy(update={"input_state": "mismatch"})
        item = _request(client, restored.record, item)
        if item.admission_state == "matched" and item.input_state == "matched":
            assert item.confirmation is not None
            lookup = item.confirmation.lookup
            if isinstance(lookup, OperationLookup):
                item = _command_result(client, lookup, item)
            elif isinstance(lookup, MutationLookup):
                item = _mutation_result(client, lookup, item)
        findings.append(item)
    return tuple(findings)
