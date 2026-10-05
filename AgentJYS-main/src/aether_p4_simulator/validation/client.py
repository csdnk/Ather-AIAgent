"""Synchronous, fixed-route current-P3 client for the P4 validation service."""

import math
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from typing import cast, get_args
from uuid import UUID

import httpx
from pydantic import BaseModel, TypeAdapter
from pydantic import ValidationError as ModelError

from aether_agent_memory.recall.contracts.models import RecallRecord
from aether_agent_memory.remember.contracts.foundation import FullBodyReadResult
from aether_agent_memory.remember.contracts.models import (
    DeleteReceipt,
    DeleteRequest,
    DocumentInput,
    LifecycleRequest,
    MemoryRef,
    ReflectionRequest,
    RetentionRequest,
    SourceRef,
)
from aether_agent_memory.runtime.contracts.client_definitions import ClientDefinitionReceipt
from aether_agent_memory.runtime.contracts.client_inputs import (
    ClientInputBinding,
    ClientInputReceipt,
)
from aether_agent_memory.runtime.contracts.client_recoveries import (
    ClientRunRecoveryLookup,
    ClientRunRecoveryResult,
    PrepareClientRecovery,
)
from aether_agent_memory.runtime.contracts.client_recovery_reads import (
    ClientRecoveryObjectEvidence,
    ReadClientRecovery,
    RecoveredClientObject,
    RecoveryObjectKind,
)
from aether_agent_memory.runtime.contracts.client_runs import (
    CheckpointClientRun,
    ClientRunRecord,
    ClientRunRegistration,
    RegisterClientRun,
)
from aether_agent_memory.runtime.contracts.client_state_binding import ClientStateBinding
from aether_agent_memory.runtime.contracts.client_states import (
    ClientExecutionState,
    operations_hash,
)
from aether_agent_memory.runtime.contracts.client_transfers import (
    ClientRunTransferLookup,
    ClientRunTransferResult,
    TransferClientRun,
    client_run_hash,
)
from aether_agent_memory.runtime.contracts.http_evidence import HTTP_EFFECT_ROUTES
from aether_agent_memory.runtime.contracts.models import TaskOperationView
from aether_agent_memory.runtime.contracts.mutation_receipts import (
    MutationKind,
    MutationLookup,
    MutationResult,
)
from aether_agent_memory.runtime.contracts.operation_lookup import HTTPCommandKind, OperationLookup

from .calls import ApiCall, OperationCall, route_template
from .confirmation import RequestConfirmation, compare_request
from .errors import PendingOperation, upstream_error
from .errors import ValidationError as AppError
from .models import (
    CapabilitiesData,
    ConsolidateData,
    ContextPack,
    CorrectionRequest,
    Identifier,
    LiveData,
    MemorySnapshot,
    OperationID,
    ProbeError,
    ProbeResult,
    ProcessingData,
    RecallRequest,
    RememberReceipt,
    RememberRequest,
    RuntimeHealthSnapshot,
    ScopeSelector,
    StatusData,
    TaskRecord,
)
from .story_models import (
    ArrayData,
    BodyRangeData,
    MemoryRefs,
    ObjectData,
    SourceRangeData,
    TaskData,
)

_IDENTIFIER = TypeAdapter(Identifier)
_OPERATION_ID = TypeAdapter(OperationID)


def _identifier(value: str) -> str:
    try:
        return _IDENTIFIER.validate_python(value, strict=True)
    except ModelError:
        raise AppError(400, "invalid_identifier", "资源 ID 格式不正确") from None


class P3ValidationClient:
    """Own one shared HTTP client; callers close it when the P4 service stops."""

    def __init__(
        self,
        base_url: str,
        credential: str,
        *,
        timeout_seconds: float = 60,
        probe_timeout_seconds: float = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not math.isfinite(probe_timeout_seconds) or not 0 < probe_timeout_seconds <= 60:
            raise ValueError("probe timeout must be in (0,60]")
        self._probe_timeout = min(probe_timeout_seconds, timeout_seconds)
        self._observer: ContextVar[Callable[[ApiCall], None] | None] = ContextVar(
            f"p3_observer_{id(self)}", default=None
        )
        self._effects: ContextVar[Callable[[OperationCall], None] | None] = ContextVar(
            f"p3_effects_{id(self)}", default=None
        )
        self._inputs: ContextVar[Callable[[OperationCall, bytes], None] | None] = ContextVar(
            f"p3_inputs_{id(self)}", default=None
        )
        self._run: ContextVar[Callable[[], ClientRunRecord] | None] = ContextVar(
            f"p3_run_{id(self)}", default=None
        )
        self._read_deadline: ContextVar[float | None] = ContextVar(
            f"p3_read_deadline_{id(self)}", default=None
        )
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {credential}"},
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def remember(self, body: RememberRequest, operation_id: str) -> RememberReceipt:
        return self._request(
            "POST",
            "/p3/remember",
            RememberReceipt,
            body=body,
            operation_id=operation_id,
            write=True,
        )

    def register_client_run(self, run_id: UUID, body: RegisterClientRun) -> ClientRunRegistration:
        response = self._send("POST", f"/p3/client-runs/{run_id}", body=body, write=True)
        code = self._business_code(response)
        errors = {
            "CAPACITY_EXCEEDED": (429, "run_limit", "已保留 10 轮；请核对原运行，重启不会清除登记"),
            "VERSION_CONFLICT": (409, "run_active", "已有运行或未确认的任务，请先核对"),
            "IDEMPOTENCY_CONFLICT": (409, "start_conflict", "开始标识已用于另一个场景"),
        }
        if code in errors:
            raise AppError(*errors[code])
        self._check_status(response, None, write=True)
        return self._parse(response, ClientRunRegistration, write=True)

    def get_client_run(self, run_id: UUID) -> ClientRunRecord:
        return self._request("GET", f"/p3/client-runs/{run_id}", ClientRunRecord)

    def save_client_definition(
        self, record: ClientRunRecord, payload: bytes
    ) -> ClientDefinitionReceipt:
        binding = record.definition
        if (
            binding is None
            or len(payload) != binding.size_bytes
            or sha256(payload).hexdigest() != binding.content_hash
        ):
            raise self._protocol_error(write=True)
        response = self._send(
            "PUT",
            f"/p3/client-runs/{record.run_id}/definition",
            content=payload,
            input_owner=(record.owner_id, record.revision),
            write=True,
        )
        self._check_status(response, None, write=True)
        receipt = self._parse(response, ClientDefinitionReceipt, write=True)
        if receipt != ClientDefinitionReceipt(run_id=record.run_id, **binding.model_dump()):
            raise self._protocol_error(write=True)
        return receipt

    def read_client_definition(self, record: ClientRunRecord) -> bytes:
        binding = record.definition
        if binding is None:
            raise AppError(409, "definition_binding_missing", "旧运行缺少原始定义，不能补造")
        response = self._send("GET", f"/p3/client-runs/{record.run_id}/definition")
        self._check_status(response, None, write=False)
        if (
            len(response.content) != binding.size_bytes
            or sha256(response.content).hexdigest() != binding.content_hash
            or response.headers.get("X-P3-Definition-Hash") != binding.content_hash
        ):
            raise self._protocol_error()
        return response.content

    def checkpoint_client_run(self, run_id: UUID, body: CheckpointClientRun) -> ClientRunRecord:
        return self._request(
            "PUT", f"/p3/client-runs/{run_id}", ClientRunRecord, body=body, write=True
        )

    def save_client_state(self, record: ClientRunRecord, payload: bytes) -> ClientRunRecord:
        try:
            state = ClientExecutionState.from_bytes(payload)
            binding = state.binding(payload)
            raw = record.snapshot.get("operations", [])
            assert isinstance(raw, list)
            operations = [OperationCall.model_validate(value) for value in raw]
            head = record.execution_state
            if (
                record.state_policy != "p4_state_v1"
                or record.definition is None
                or state.run_id != record.run_id
                or state.scenario_id != record.scenario_id
                or state.scope_id != record.scope_id
                or state.definition_hash != record.definition.content_hash
                or state.sequence != (1 if head is None else head.sequence + 1)
                or state.parent_hash != (None if head is None else head.content_hash)
                or state.stream_id != record.state_stream_id
                or state.parent_stream_id != (None if head is None else head.stream_id)
                or binding.operations_hash != operations_hash(operations)
            ):
                raise ValueError("original state binding changed")
        except (ValueError, RecursionError):
            raise self._protocol_error(write=True) from None
        response = self._send(
            "PUT",
            f"/p3/client-runs/{record.run_id}/states/{state.sequence}",
            content=payload,
            input_owner=(record.owner_id, record.revision),
            write=True,
        )
        self._check_status(response, None, write=True)
        updated = self._parse(response, ClientRunRecord, write=True)
        if (
            updated.revision != record.revision + 1
            or updated.execution_state != binding
            or any(
                getattr(updated, key) != getattr(record, key)
                for key in (
                    "run_id",
                    "scenario_id",
                    "scope_id",
                    "owner_id",
                    "auth_epoch",
                    "definition",
                    "scope_policy",
                    "state_policy",
                    "snapshot",
                )
            )
        ):
            raise self._protocol_error(write=True)
        return updated

    def read_client_state(self, record: ClientRunRecord) -> bytes:
        head = record.execution_state
        if record.state_policy != "p4_state_v1" or head is None:
            raise AppError(409, "execution_state_missing", "原运行缺少已确认执行状态，不能补造")
        response = self._send(
            "GET",
            f"/p3/client-runs/{record.run_id}/states/{head.sequence}",
            params=None if head.stream_id is None else {"stream_id": head.stream_id},
        )
        self._check_status(response, None, write=False)
        try:
            state = ClientExecutionState.from_bytes(response.content)
            if (
                state.binding(response.content) != head
                or response.headers.get("X-P3-State-Hash") != head.content_hash
                or state.run_id != record.run_id
                or state.scenario_id != record.scenario_id
                or state.scope_id != record.scope_id
            ):
                raise ValueError("original state bytes changed")
        except (ValueError, RecursionError):
            raise self._protocol_error() from None
        return response.content

    def recall(self, body: RecallRequest, operation_id: str) -> ContextPack:
        # Recall has an operation ID, but is not a memory write.
        return self._request(
            "POST",
            "/p3/recall",
            ContextPack,
            body=body,
            operation_id=operation_id,
        )

    def consolidate(self, selection: ScopeSelector, operation_id: str) -> ConsolidateData:
        return self._request(
            "POST",
            "/p3/remember/consolidate",
            ConsolidateData,
            body=selection,
            operation_id=operation_id,
            write=True,
        )

    def memory(self, memory_id: str) -> MemorySnapshot:
        return self._request("GET", f"/p3/remember/{_identifier(memory_id)}", MemorySnapshot)

    def processing(self, memory_id: str, *, timeout_seconds: float | None = None) -> ProcessingData:
        return self._request(
            "GET",
            f"/p3/remember/{_identifier(memory_id)}/processing",
            ProcessingData,
            timeout_seconds=timeout_seconds,
        )

    def operation(self, job_id: str, *, timeout_seconds: float) -> TaskOperationView:
        result = self._request(
            "GET",
            f"/p3/operations/{_identifier(job_id)}",
            TaskOperationView,
            timeout_seconds=timeout_seconds,
        )
        if result.task_id != job_id:
            raise self._protocol_error()
        return result

    def lookup_operation(
        self, operation_id: str, kind: HTTPCommandKind, *, timeout_seconds: float | None = None
    ) -> OperationLookup:
        result = self._request(
            "GET",
            f"/p3/operation-requests/{_identifier(operation_id)}",
            OperationLookup,
            params={"kind": kind},
            timeout_seconds=timeout_seconds,
        )
        if result.operation_id != operation_id or result.kind != kind:
            raise self._protocol_error(operation_id)
        return result

    def lookup_mutation(
        self, operation_id: str, kind: MutationKind, *, timeout_seconds: float | None = None
    ) -> MutationLookup:
        result = self._request(
            "GET",
            f"/p3/mutation-receipts/{_identifier(operation_id)}",
            MutationLookup,
            params={"kind": kind},
            timeout_seconds=timeout_seconds,
        )
        if result.operation_id != operation_id or result.kind != kind:
            raise self._protocol_error(operation_id)
        return result

    def mutation_result(
        self, operation_id: str, kind: MutationKind, *, timeout_seconds: float | None = None
    ) -> MutationResult:
        result = self._request(
            "GET",
            f"/p3/mutation-receipts/{_identifier(operation_id)}/result",
            MutationResult,
            params={"kind": kind},
            timeout_seconds=timeout_seconds,
        )
        if result.receipt.operation_id != operation_id or result.receipt.kind != kind:
            raise self._protocol_error(operation_id)
        return result

    def confirm_operation(self, intent: OperationCall) -> RequestConfirmation:
        kind = next(
            (
                kind
                for kind, route in HTTP_EFFECT_ROUTES.items()
                if route == (intent.method, intent.path)
            ),
            None,
        )
        if kind is None:
            return RequestConfirmation(
                operation_id=intent.operation_id, state="unconfirmed", lookup=None
            )
        lookup: OperationLookup | MutationLookup
        if kind in get_args(HTTPCommandKind):
            lookup = self.lookup_operation(intent.operation_id, cast(HTTPCommandKind, kind))
        else:
            lookup = self.lookup_mutation(intent.operation_id, cast(MutationKind, kind))
        return compare_request(intent, lookup)

    def operation_result[T: BaseModel](
        self, job_id: str, model: type[T], *, timeout_seconds: float
    ) -> T:
        return self._request(
            "GET",
            f"/p3/operations/{_identifier(job_id)}/result",
            model,
            timeout_seconds=timeout_seconds,
        )

    def correct(
        self,
        memory_id: str,
        body: CorrectionRequest,
        operation_id: str,
    ) -> RememberReceipt:
        return self._request(
            "POST",
            f"/p3/remember/{_identifier(memory_id)}/correct",
            RememberReceipt,
            body=body,
            operation_id=operation_id,
            write=True,
        )

    def recall_result(self, recall_id: str) -> ContextPack:
        return self._request(
            "GET",
            f"/p3/recalls/{_identifier(recall_id)}/result",
            ContextPack,
        )

    def task(self, task_id: str, *, timeout_seconds: float | None = None) -> TaskRecord:
        return self._request(
            "GET", f"/p3/tasks/{_identifier(task_id)}", TaskRecord, timeout_seconds=timeout_seconds
        )

    @contextmanager
    def observe_calls(self, observer: Callable[[ApiCall], None]) -> Iterator[None]:
        """Observe this execution context only; never intercept payloads or credentials."""
        token = self._observer.set(observer)
        try:
            yield
        finally:
            self._observer.reset(token)

    def upload_document(self, document_id: str, text: str, operation_id: str) -> DocumentInput:
        return self._request(
            "PUT",
            f"/p3/documents/{_identifier(document_id)}",
            DocumentInput,
            content=text.encode("utf-8"),
            params={"version": "1"},
            operation_id=operation_id,
            write=True,
        )

    def body(self, ref: MemoryRef) -> FullBodyReadResult:
        return self._request("POST", "/p3/remember/body", FullBodyReadResult, body=ref)

    def body_range(self, ref: MemoryRef, start: int, end: int) -> BodyRangeData:
        return self._request(
            "POST",
            "/p3/remember/body/range",
            BodyRangeData,
            body=ref,
            params={"start": start, "end": end},
        )

    def source_range(self, ref: SourceRef, start: int, end: int) -> SourceRangeData:
        return self._request(
            "POST",
            "/p3/sources/read-range",
            SourceRangeData,
            body=ref,
            params={"start": start, "end": end},
        )

    def recall_record(self, recall_id: str) -> RecallRecord:
        return self._request("GET", f"/p3/recalls/{_identifier(recall_id)}", RecallRecord)

    def catalog(self, selection: ScopeSelector, cursor: str | None = None) -> ObjectData:
        return self._request(
            "GET",
            "/p3/memories",
            ObjectData,
            params={
                **selection.model_dump(exclude_none=True),
                "limit": 100,
                **({"cursor": cursor} if cursor else {}),
            },
        )

    def placement(self, memory_id: str) -> ObjectData:
        return self._request("GET", f"/p3/operate/memories/{_identifier(memory_id)}", ObjectData)

    def lifecycle(
        self, memory_id: str, body: LifecycleRequest, operation_id: str
    ) -> MemorySnapshot:
        return self._request(
            "POST",
            f"/p3/remember/{_identifier(memory_id)}/lifecycle",
            MemorySnapshot,
            body=body,
            operation_id=operation_id,
            write=True,
        )

    def retention(self, memory_id: str) -> ObjectData:
        return self._request("GET", f"/p3/remember/{_identifier(memory_id)}/retention", ObjectData)

    def configure_retention(
        self, memory_id: str, body: RetentionRequest, operation_id: str
    ) -> ObjectData:
        return self._request(
            "POST",
            f"/p3/remember/{_identifier(memory_id)}/retention",
            ObjectData,
            body=body,
            operation_id=operation_id,
            write=True,
        )

    def reflection(
        self, selection: ScopeSelector, *, timeout_seconds: float | None = None
    ) -> ObjectData:
        return self._request(
            "GET",
            "/p3/remember/reflection",
            ObjectData,
            params=selection.model_dump(exclude_none=True),
            timeout_seconds=timeout_seconds,
        )

    def configure_reflection(self, body: ReflectionRequest, operation_id: str) -> ObjectData:
        return self._request(
            "POST",
            "/p3/remember/reflection",
            ObjectData,
            body=body,
            operation_id=operation_id,
            write=True,
        )

    def distill(self, refs: tuple[MemoryRef, ...], operation_id: str) -> TaskData:
        return self._request(
            "POST",
            "/p3/remember/distill",
            TaskData,
            body=MemoryRefs(refs),
            operation_id=operation_id,
            write=True,
        )

    def reprocess(self, memory_id: str, operation_id: str) -> TaskData:
        return self._request(
            "POST",
            f"/p3/remember/{_identifier(memory_id)}/reprocess",
            TaskData,
            operation_id=operation_id,
            write=True,
        )

    def reindex(self, memory_id: str, operation_id: str) -> TaskData:
        return self._request(
            "POST",
            f"/p3/remember/{_identifier(memory_id)}/reindex",
            TaskData,
            operation_id=operation_id,
            write=True,
        )

    def delete_memory(
        self, memory_id: str, body: DeleteRequest, operation_id: str
    ) -> DeleteReceipt:
        return self._request(
            "POST",
            f"/p3/remember/{_identifier(memory_id)}/delete",
            DeleteReceipt,
            body=body,
            operation_id=operation_id,
            write=True,
        )

    def delete_source(
        self, source_id: str, body: DeleteRequest, operation_id: str
    ) -> DeleteReceipt:
        return self._request(
            "POST",
            f"/p3/sources/{_identifier(source_id)}/delete",
            DeleteReceipt,
            body=body,
            operation_id=operation_id,
            write=True,
        )

    def revoke_source(
        self, source_id: str, body: DeleteRequest, operation_id: str
    ) -> DeleteReceipt:
        return self._request(
            "POST",
            f"/p3/sources/{_identifier(source_id)}/revoke",
            DeleteReceipt,
            body=body,
            operation_id=operation_id,
            write=True,
        )

    def task_progress(self, task_id: str, *, timeout_seconds: float | None = None) -> ObjectData:
        return self._request(
            "GET",
            f"/p3/tasks/{_identifier(task_id)}/progress",
            ObjectData,
            timeout_seconds=timeout_seconds,
        )

    def tasks(
        self, cursor: str | None = None, *, timeout_seconds: float | None = None
    ) -> ObjectData:
        return self._request(
            "GET",
            "/p3/tasks",
            ObjectData,
            params={
                "limit": 100,
                **({"cursor": cursor} if cursor else {}),
            },
            timeout_seconds=timeout_seconds,
        )

    def traces(
        self, before: int | None = None, *, timeout_seconds: float | None = None
    ) -> ObjectData:
        return self._request(
            "GET",
            "/p3/traces",
            ObjectData,
            params={
                "limit": 100,
                **({"before": before} if before else {}),
            },
            timeout_seconds=timeout_seconds,
        )

    def logs(self, trace_id: str, *, timeout_seconds: float | None = None) -> ObjectData:
        return self._request(
            "GET",
            f"/p3/logs/{_identifier(trace_id)}",
            ObjectData,
            params={"limit": 100},
            timeout_seconds=timeout_seconds,
        )

    def runtime(self, *, timeout_seconds: float | None = None) -> ObjectData:
        return self._request("GET", "/p3/runtime", ObjectData, timeout_seconds=timeout_seconds)

    def readyz(self, *, timeout_seconds: float | None = None) -> ObjectData:
        return self._request("GET", "/p3/readyz", ObjectData, timeout_seconds=timeout_seconds)

    def periodic_status(self, *, timeout_seconds: float | None = None) -> ObjectData:
        return self._request(
            "GET", "/p3/periodic/control", ObjectData, timeout_seconds=timeout_seconds
        )

    def incidents(self, *, timeout_seconds: float | None = None) -> ArrayData:
        return self._request("GET", "/p3/incidents", ArrayData, timeout_seconds=timeout_seconds)

    def status(self) -> StatusData:
        return StatusData(
            live=self._probe("/p3/live", LiveData),
            health=self._probe("/p3/health", RuntimeHealthSnapshot),
            ready=self._probe("/p3/ready", RuntimeHealthSnapshot, readiness=True),
            capabilities=self._probe("/p3/capabilities", CapabilitiesData),
        )

    def close(self) -> None:
        self._http.close()

    def save_client_input(
        self,
        record: ClientRunRecord,
        intent: OperationCall,
        payload: bytes,
    ) -> ClientInputReceipt:
        if intent.binding is None or sha256(payload).hexdigest() != intent.request_hash:
            raise AppError(409, "input_binding_mismatch", "原始请求字节与登记不一致")
        response = self._send(
            "PUT",
            f"/p3/client-runs/{record.run_id}/inputs/{_identifier(intent.operation_id)}",
            content=payload,
            write=True,
            input_owner=(record.owner_id, record.revision),
        )
        self._check_status(response, intent.operation_id, write=True)
        receipt = self._parse(response, ClientInputReceipt, intent.operation_id, write=True)
        if (
            receipt.run_id != record.run_id
            or receipt.operation_id != intent.operation_id
            or receipt.request_hash != intent.request_hash
            or receipt.size_bytes != len(payload)
            or receipt.binding_digest != intent.binding.digest
        ):
            raise self._protocol_error(intent.operation_id, write=True)
        return receipt

    def read_client_input(self, run_id: UUID | str, intent: OperationCall) -> bytes:
        if intent.binding is None:
            raise AppError(409, "input_binding_missing", "旧操作缺少原始请求绑定，不能补造输入")
        response = self._send(
            "GET",
            f"/p3/client-runs/{UUID(str(run_id))}/inputs/{_identifier(intent.operation_id)}",
        )
        self._check_status(response, intent.operation_id, write=False)
        if (
            sha256(response.content).hexdigest() != intent.request_hash
            or response.headers.get("X-P3-Request-Hash") != intent.request_hash
        ):
            raise self._protocol_error(intent.operation_id)
        return response.content

    @contextmanager
    def read_budget(self, timeout_seconds: float) -> Iterator[None]:
        """One context-local deadline covers restoration and every subsequent GET.

        HTTPX phase timeouts bound network waits. The checks after each response
        and at context exit reject a late report even if a transport ignores them.
        This context grants no admission rights and cannot send a mutation.
        """
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise AppError(400, "invalid_argument", "核对超时必须是有限正数")
        deadline = time.monotonic() + timeout_seconds
        parent = self._read_deadline.get()
        token = self._read_deadline.set(deadline if parent is None else min(parent, deadline))
        try:
            self._remaining_read_budget()
            yield
            self._remaining_read_budget()
        finally:
            self._read_deadline.reset(token)

    def transfer_client_run(
        self, record: ClientRunRecord, *, new_owner_id: str, transfer_id: str
    ) -> ClientRunTransferResult:
        body = TransferClientRun(
            expected_owner_id=record.owner_id,
            expected_revision=record.revision,
            expected_record_hash=client_run_hash(record),
            new_owner_id=new_owner_id,
        )
        result = self._request(
            "POST",
            f"/p3/client-runs/{record.run_id}/transfers/{_identifier(transfer_id)}",
            ClientRunTransferResult,
            body=body,
            operation_id=transfer_id,
            write=True,
        )
        if (
            result.run_id != record.run_id
            or result.transfer_id != transfer_id
            or result.request != body
            or result.previous != record
        ):
            raise self._protocol_error(transfer_id, write=True)
        return result

    def lookup_client_transfer(
        self, run_id: UUID | str, transfer_id: str
    ) -> ClientRunTransferLookup:
        identity = UUID(str(run_id))
        result = self._request(
            "GET",
            f"/p3/client-runs/{identity}/transfers/{_identifier(transfer_id)}",
            ClientRunTransferLookup,
        )
        if result.run_id != identity or result.transfer_id != transfer_id:
            raise self._protocol_error(transfer_id)
        return result

    def activate_client_run(
        self, record: ClientRunRecord, execution_state: ClientExecutionState | None
    ) -> ClientRunRecoveryResult:
        transfer_id = None if record.ownership is None else record.ownership.recovery_transfer_id
        if transfer_id is None:
            raise AppError(409, "recovery_hold_missing", "原运行没有待恢复的接管占用")
        body = PrepareClientRecovery(
            expected_owner_id=record.owner_id,
            expected_revision=record.revision,
            expected_record_hash=client_run_hash(record),
            target_state="queued" if execution_state is None else "running",
            execution_state=execution_state,
        )
        intent = body.intent()
        try:
            intent.require_original(record, transfer_id)
        except ValueError:
            raise self._protocol_error(transfer_id, write=True) from None
        result = self._request(
            "POST",
            f"/p3/client-runs/{record.run_id}/recoveries/{_identifier(transfer_id)}",
            ClientRunRecoveryResult,
            body=body,
            operation_id=transfer_id,
            write=True,
            exclude_none=False,
        )
        if (
            result.run_id != record.run_id
            or result.transfer_id != transfer_id
            or result.previous != record
            or result.request != intent
        ):
            raise self._protocol_error(transfer_id, write=True)
        return result

    def lookup_client_recovery(
        self, run_id: UUID | str, transfer_id: str
    ) -> ClientRunRecoveryLookup:
        identity = UUID(str(run_id))
        result = self._request(
            "GET",
            f"/p3/client-runs/{identity}/recoveries/{_identifier(transfer_id)}",
            ClientRunRecoveryLookup,
        )
        if result.run_id != identity or result.transfer_id != transfer_id:
            raise self._protocol_error(transfer_id)
        return result

    def _remaining_read_budget(self) -> float | None:
        deadline = self._read_deadline.get()
        if deadline is None:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AppError(504, "reconciliation_timeout", "整轮核对超时，不能使用未完成的报告")
        return remaining

    def _read_recovery_object(
        self,
        record: ClientRunRecord,
        kind: RecoveryObjectKind,
        suffix: str,
        *,
        stream_id: str | None = None,
    ) -> RecoveredClientObject:
        transfer_id = None if record.ownership is None else record.ownership.recovery_transfer_id
        if transfer_id is None:
            raise AppError(409, "recovery_hold_missing", "原运行没有待恢复的接管占用")
        expected = ReadClientRecovery(
            expected_owner_id=record.owner_id,
            expected_revision=record.revision,
            expected_record_hash=client_run_hash(record),
        )
        params = expected.model_dump(mode="json")
        if stream_id is not None:
            params["stream_id"] = _identifier(stream_id)
        response = self._send(
            "GET",
            f"/p3/client-runs/{record.run_id}/recoveries/{_identifier(transfer_id)}/{suffix}",
            params=params,
        )
        self._check_status(response, None, write=False)
        try:
            evidence = ClientRecoveryObjectEvidence.model_validate_json(
                response.headers.get("X-P3-Recovery-Object", "")
            )
            binding = evidence.binding
            digest = (
                binding.request_hash
                if isinstance(binding, ClientInputBinding)
                else binding.content_hash
            )
            if (
                evidence.run_id != record.run_id
                or evidence.transfer_id != transfer_id
                or evidence.record_hash != expected.expected_record_hash
                or evidence.kind != kind
                or len(response.content) != binding.size_bytes
                or sha256(response.content).hexdigest() != digest
            ):
                raise ValueError("recovery object differs from original binding")
        except ValueError:
            raise self._protocol_error() from None
        return RecoveredClientObject(evidence, response.content)

    def confirm_recovery_definition(
        self, record: ClientRunRecord, payload: bytes | None = None
    ) -> ClientDefinitionReceipt:
        """Confirm original bytes under the hold; ambiguous replies require the original GET."""
        transfer_id = None if record.ownership is None else record.ownership.recovery_transfer_id
        if transfer_id is None:
            raise AppError(409, "recovery_hold_missing", "原运行没有待恢复的接管占用")
        binding = record.definition
        if binding is None or (
            payload is not None
            and (
                len(payload) != binding.size_bytes
                or sha256(payload).hexdigest() != binding.content_hash
            )
        ):
            raise self._protocol_error(write=True)
        expected = ReadClientRecovery(
            expected_owner_id=record.owner_id,
            expected_revision=record.revision,
            expected_record_hash=client_run_hash(record),
        )
        response = self._send(
            "PUT",
            f"/p3/client-runs/{record.run_id}/recoveries/{_identifier(transfer_id)}/definition",
            params=expected.model_dump(mode="json"),
            content=payload,
            write=True,
        )
        self._check_status(response, None, write=True)
        receipt = self._parse(response, ClientDefinitionReceipt, write=True)
        if receipt != ClientDefinitionReceipt(run_id=record.run_id, **binding.model_dump()):
            raise self._protocol_error(write=True)
        return receipt

    def confirm_recovery_input(
        self, record: ClientRunRecord, intent: OperationCall
    ) -> ClientInputReceipt:
        """Confirm verified original bytes; an uncertain reply is never retried here."""
        original = self.read_recovery_input(record, intent)
        assert intent.binding is not None and record.ownership is not None
        transfer_id = record.ownership.recovery_transfer_id
        assert transfer_id is not None
        expected = ReadClientRecovery(
            expected_owner_id=record.owner_id,
            expected_revision=record.revision,
            expected_record_hash=client_run_hash(record),
        )
        response = self._send(
            "PUT",
            f"/p3/client-runs/{record.run_id}/recoveries/{_identifier(transfer_id)}"
            f"/inputs/{_identifier(intent.operation_id)}",
            params=expected.model_dump(mode="json"),
            operation_id=intent.operation_id,
            write=True,
        )
        self._check_status(response, intent.operation_id, write=True)
        receipt = self._parse(response, ClientInputReceipt, intent.operation_id, write=True)
        if receipt != ClientInputReceipt(
            run_id=record.run_id,
            operation_id=intent.operation_id,
            request_hash=intent.request_hash,
            binding_digest=intent.binding.digest,
            size_bytes=len(original.payload),
        ):
            raise self._protocol_error(intent.operation_id, write=True)
        return receipt

    def read_recovery_definition(self, record: ClientRunRecord) -> RecoveredClientObject:
        if record.definition is None:
            raise AppError(409, "definition_missing", "原运行没有定义绑定，不能补造")
        value = self._read_recovery_object(record, "definition", "definition")
        if value.evidence.binding != record.definition:
            raise self._protocol_error()
        return value

    def read_recovery_input(
        self, record: ClientRunRecord, intent: OperationCall
    ) -> RecoveredClientObject:
        raw = record.snapshot.get("operations", [])
        assert isinstance(raw, list)
        if intent.binding is None or intent not in [
            OperationCall.model_validate(item) for item in raw
        ]:
            raise AppError(409, "input_binding_missing", "原运行没有此请求的完整绑定")
        value = self._read_recovery_object(
            record, "input", f"inputs/{_identifier(intent.operation_id)}"
        )
        binding = value.evidence.binding
        if (
            not isinstance(binding, ClientInputBinding)
            or binding.operation_id != intent.operation_id
            or binding.request_hash != intent.request_hash
            or binding.binding_digest != intent.binding.digest
        ):
            raise self._protocol_error(intent.operation_id)
        return value

    def read_recovery_state(
        self, record: ClientRunRecord, sequence: int, stream_id: str | None = None
    ) -> RecoveredClientObject:
        if type(sequence) is not int or not 1 <= sequence <= 1024:
            raise AppError(400, "invalid_argument", "执行状态序号不合法")
        value = self._read_recovery_object(
            record, "state", f"states/{sequence}", stream_id=stream_id
        )
        try:
            state = ClientExecutionState.from_bytes(value.payload)
            if (
                not isinstance(value.evidence.binding, ClientStateBinding)
                or state.binding(value.payload) != value.evidence.binding
                or state.sequence != sequence
                or state.stream_id != stream_id
                or record.definition is None
                or state.definition_hash != record.definition.content_hash
                or state.run_id != record.run_id
                or state.scenario_id != record.scenario_id
                or state.scope_id != record.scope_id
            ):
                raise ValueError("recovery state differs from original target")
            head = record.execution_state
            if value.evidence.state == "ready" and (
                head is None
                or state.sequence > head.sequence
                or (state.sequence == head.sequence and value.evidence.binding != head)
            ):
                raise ValueError("ready recovery state contradicts published head")
        except (ValueError, RecursionError):
            raise self._protocol_error() from None
        return value

    @contextmanager
    def bind_client_run(self, current: Callable[[], ClientRunRecord]) -> Iterator[None]:
        token = self._run.set(current)
        try:
            yield
        finally:
            self._run.reset(token)

    @contextmanager
    def preserve_inputs(self, callback: Callable[[OperationCall, bytes], None]) -> Iterator[None]:
        token = self._inputs.set(callback)
        try:
            yield
        finally:
            self._inputs.reset(token)

    @contextmanager
    def observe_effects(self, observer: Callable[[OperationCall], None]) -> Iterator[None]:
        token = self._effects.set(observer)
        try:
            yield
        finally:
            self._effects.reset(token)

    def _send(
        self,
        method: str,
        path: str,
        *,
        body: BaseModel | None = None,
        operation_id: str | None = None,
        write: bool = False,
        probe: bool = False,
        timeout_seconds: float | None = None,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        content: bytes | None = None,
        input_owner: tuple[str, int] | None = None,
        exclude_none: bool = True,
    ) -> httpx.Response:
        if timeout_seconds is not None and (
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise AppError(400, "invalid_argument", "观察超时必须是有限正数")
        remaining = self._remaining_read_budget()
        if remaining is not None:
            if method != "GET":
                raise AppError(409, "read_only_observation", "只读核对不能发送写入请求")
            timeout_seconds = min(remaining, timeout_seconds) if timeout_seconds else remaining
        if operation_id is not None:
            try:
                operation_id = _OPERATION_ID.validate_python(operation_id)
            except ModelError:
                raise AppError(400, "invalid_argument", "操作 ID 格式不正确") from None
        headers = {"X-Operation-ID": operation_id} if operation_id is not None else {}
        if content is not None:
            headers["Content-Type"] = "text/plain; charset=utf-8"
        if input_owner is not None:
            headers.update(
                {
                    "X-P3-Run-Owner": input_owner[0],
                    "X-P3-Run-Revision": str(input_owner[1]),
                    "Content-Type": "application/octet-stream",
                }
            )
        started = time.monotonic()
        response = None
        try:
            request = self._http.build_request(
                method,
                path,
                headers=headers,
                params=params,
                content=content,
                json=body.model_dump(mode="json", exclude_none=exclude_none)
                if body is not None
                else None,
                timeout=(
                    timeout_seconds
                    if timeout_seconds is not None
                    else self._probe_timeout
                    if probe
                    else self._http.timeout
                ),
            )
            effect_observer = self._effects.get()
            if (
                self._run.get() is not None
                and (method, route_template(method, path)) in HTTP_EFFECT_ROUTES.values()
                and (effect_observer is None or operation_id is None or self._inputs.get() is None)
            ):
                raise AppError(409, "input_binding_missing", "原操作或原字节未保存，停止业务请求")
            intent = None
            if (
                effect_observer is not None
                and operation_id
                and method in {"POST", "PUT"}
                and not path.startswith("/p3/client-runs/")
            ):
                intent = OperationCall.prepare(
                    method="POST" if method == "POST" else "PUT",
                    path=route_template(method, path),
                    operation_id=operation_id,
                    request_hash=sha256(request.content).hexdigest(),
                    target=request.url.raw_path.decode("ascii"),
                    content_type=request.headers.get("content-type", ""),
                )
                # A failed checkpoint aborts before the network can accept this operation.
                effect_observer(intent)
                preserve = self._inputs.get()
                if preserve is not None:
                    preserve(intent, request.content)
                current_run = self._run.get()
                if current_run is not None:
                    record = current_run()
                    request.headers.update(
                        {
                            "X-P3-Run-ID": str(record.run_id),
                            "X-P3-Run-Owner": record.owner_id,
                            "X-P3-Run-Revision": str(record.revision),
                        }
                    )
            response = self._http.send(request)
            self._remaining_read_budget()
            if intent is not None and effect_observer is not None:
                job_id = response.headers.get("X-P3-Job-ID")
                try:
                    observed = OperationCall.model_validate(
                        {
                            **intent.model_dump(),
                            "phase": "observed",
                            "status_code": response.status_code,
                            "job_id": job_id,
                        }
                    )
                except ModelError:
                    raise self._protocol_error(operation_id, write=write) from None
                effect_observer(observed)
            return response
        except httpx.TimeoutException:
            raise AppError(
                504,
                "upstream_timeout",
                "等待 P3 响应超时",
                operation_id=operation_id,
                write_outcome="unconfirmed" if write else "not_applicable",
            ) from None
        except (httpx.ProtocolError, httpx.DecodingError):
            raise self._protocol_error(operation_id, write=write) from None
        except httpx.RequestError:
            raise AppError(
                502,
                "upstream_unavailable",
                "无法连接或读取 P3 服务",
                operation_id=operation_id,
                write_outcome="unconfirmed" if write else "not_applicable",
            ) from None
        finally:
            observer = self._observer.get()
            if observer is not None and not path.startswith("/p3/client-runs/"):
                observer(
                    ApiCall(
                        method=method,
                        path=route_template(method, path),
                        status_code=response.status_code if response is not None else None,
                        elapsed_ms=round((time.monotonic() - started) * 1000, 1),
                        operation_id=operation_id,
                    )
                )

    @staticmethod
    def _protocol_error(operation_id: str | None = None, *, write: bool = False) -> AppError:
        return AppError(
            502,
            "upstream_protocol_error",
            "P3 返回格式不符合接口约定",
            operation_id=operation_id,
            write_outcome="unconfirmed" if write else "not_applicable",
        )

    @staticmethod
    def _business_code(response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        code = payload.get("code") if isinstance(payload, dict) else None
        return code if isinstance(code, str) else None

    def _check_status(
        self,
        response: httpx.Response,
        operation_id: str | None,
        *,
        write: bool,
    ) -> None:
        if 200 <= response.status_code < 300:
            return
        if 300 <= response.status_code < 400:
            raise self._protocol_error(operation_id, write=write)
        if response.status_code == 400 and self._business_code(response) == "REQUEST_IN_PROGRESS":
            try:
                job_id = _IDENTIFIER.validate_python(
                    response.headers.get("X-P3-Job-ID"), strict=True
                )
            except ModelError:
                raise self._protocol_error(operation_id, write=write) from None
            raise PendingOperation(job_id, operation_id=operation_id, write=write)
        raise upstream_error(
            response.status_code,
            self._business_code(response),
            operation_id=operation_id,
            write=write,
        )

    def _parse[T: BaseModel](
        self,
        response: httpx.Response,
        model: type[T],
        operation_id: str | None = None,
        *,
        write: bool = False,
    ) -> T:
        try:
            return model.model_validate(response.json())
        except ValueError:
            # Covers invalid JSON and Pydantic validation errors without exposing
            # the offending response, which may contain user data or secrets.
            raise self._protocol_error(operation_id, write=write) from None

    def _request[T: BaseModel](
        self,
        method: str,
        path: str,
        model: type[T],
        *,
        body: BaseModel | None = None,
        operation_id: str | None = None,
        write: bool = False,
        timeout_seconds: float | None = None,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        content: bytes | None = None,
        exclude_none: bool = True,
    ) -> T:
        response = self._send(
            method,
            path,
            body=body,
            operation_id=operation_id,
            write=write,
            timeout_seconds=timeout_seconds,
            params=params,
            content=content,
            exclude_none=exclude_none,
        )
        self._check_status(response, operation_id, write=write)
        return self._parse(response, model, operation_id, write=write)

    def _probe[T: BaseModel](
        self,
        path: str,
        model: type[T],
        *,
        readiness: bool = False,
    ) -> ProbeResult[T]:
        status_code = None
        try:
            response = self._send("GET", path, probe=True)
            status_code = response.status_code
            if readiness and status_code == 503 and self._business_code(response) is None:
                data = self._parse(response, model)
                return ProbeResult[T](
                    ok=False,
                    status_code=status_code,
                    data=data,
                    error=ProbeError(code="not_ready", message="P3 尚未就绪，请查看分项健康状态"),
                )
            self._check_status(response, None, write=False)
            data = self._parse(response, model)
            return ProbeResult[T](ok=True, status_code=status_code, data=data, error=None)
        except AppError as error:
            return ProbeResult[T](
                ok=False,
                status_code=status_code,
                data=None,
                error=ProbeError(code=error.code, message=error.message),
            )
