"""Synchronous, fixed-route current-P3 client for the P4 validation service."""

import math
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

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
from aether_agent_memory.runtime.contracts.models import TaskOperationView

from .calls import ApiCall, route_template
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
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._probe_timeout = min(2.0, timeout_seconds)
        self._observer: ContextVar[Callable[[ApiCall], None] | None] = ContextVar(
            f"p3_observer_{id(self)}", default=None
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
    ) -> httpx.Response:
        if timeout_seconds is not None and (
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise AppError(400, "invalid_argument", "观察超时必须是有限正数")
        if operation_id is not None:
            try:
                operation_id = _OPERATION_ID.validate_python(operation_id)
            except ModelError:
                raise AppError(400, "invalid_argument", "操作 ID 格式不正确") from None
        headers = {"X-Operation-ID": operation_id} if operation_id is not None else {}
        if content is not None:
            headers["Content-Type"] = "text/plain; charset=utf-8"
        started = time.monotonic()
        response = None
        try:
            request = self._http.build_request(
                method,
                path,
                headers=headers,
                params=params,
                content=content,
                json=body.model_dump(mode="json", exclude_none=True) if body is not None else None,
                timeout=(
                    timeout_seconds
                    if timeout_seconds is not None
                    else self._probe_timeout
                    if probe
                    else self._http.timeout
                ),
            )
            response = self._http.send(request)
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
            if observer is not None:
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
