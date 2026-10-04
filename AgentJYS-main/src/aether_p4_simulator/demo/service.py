"""Bounded demo execution with durable caller registration through P3."""

import math
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from typing import Literal, TypedDict
from uuid import UUID, uuid4

from pydantic import ValidationError as ModelError

from aether_agent_memory.runtime.contracts.client_recoveries import ClientRunRecoveryResult
from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_agent_memory.runtime.contracts.client_states import ClientExecutionState
from aether_agent_memory.runtime.contracts.client_transfers import ClientRunTransferResult
from aether_agent_memory.runtime.foundation.common import later, now
from aether_p4_simulator.validation.calls import ApiCall, OperationCall
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError

from .coverage import Coverage
from .definition import RunDefinition
from .initialization import InitializationAttempt, prepare_initialization
from .models import Diagnostics, Mode, RunSnapshot, SafeError, StartRequest, StepSnapshot
from .reconciliation import ReconciledExecution, reconcile_operations
from .registry import P3RunRegistry, RunRegistry
from .runner import run_library
from .scenarios import SCENARIOS, ScenarioSummary
from .state import ExecutionData, RestoredExecution, encode_state


class ScenarioListing(TypedDict):
    items: list[ScenarioSummary]
    enabled: bool
    unavailable_reason: str | None


class DemoService:
    def __init__(
        self,
        client: P3ValidationClient | None,
        *,
        unavailable_reason: str = "演示未启用或服务端配置不完整",
        registry: RunRegistry | None = None,
        wait_seconds: float = 60,
    ) -> None:
        if not math.isfinite(wait_seconds) or not 0 < wait_seconds <= 7200:
            raise ValueError("demo wait must be in (0,7200]")
        self.wait_seconds = wait_seconds
        self._client = client
        self._unavailable_reason = unavailable_reason
        self._lock = RLock()
        self._runs: dict[str, RunSnapshot] = {}
        self._records: dict[str, ClientRunRecord] = {}
        self._initializations: dict[str, InitializationAttempt] = {}
        self._initialization_tokens: dict[str, object] = {}
        self._initialization_started: set[str] = set()
        self._owner_id = uuid4().hex
        self._registry = registry or (P3RunRegistry(client) if client is not None else None)
        self._active: str | None = None
        self._closed = False
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="p3-demo")

    def list_scenarios(self) -> ScenarioListing:
        return {
            "items": [s.copy() for s in SCENARIOS],
            "enabled": self._client is not None and not self._closed,
            "unavailable_reason": (
                None if self._client is not None and not self._closed else self._unavailable_reason
            ),
        }

    def start(self, request: StartRequest) -> RunSnapshot:
        return self.start_with_status(request)[1]

    def start_with_status(self, request: StartRequest) -> tuple[bool, RunSnapshot]:
        """Return creation status under the same lock as idempotency registration."""
        run_id = str(request.request_id)
        with self._lock:
            if self._closed or self._client is None:
                raise ValidationError(503, "demo_unavailable", self._unavailable_reason)
            definition = RunDefinition.capture(request.request_id, request.scenario_id)
            texts = definition.texts
            run = RunSnapshot(
                run_id=run_id,
                scenario_id=request.scenario_id,
                total_steps=len(texts),
                coverage=Coverage().snapshot(),
                steps=[StepSnapshot(id=i, user_text=text) for i, text in enumerate(texts, 1)],
            )
            assert self._registry is not None
            registered = self._registry.register(run, self._owner_id, definition.binding())
            if registered.created or run_id not in self._runs:
                self._records[run_id] = registered.record
                self._runs[run_id] = RunSnapshot.model_validate(registered.record.snapshot)
            if registered.created:
                self._active = run_id
                self._registry.save_definition(registered.record, definition.encode())
                self._worker.submit(self._execute, run_id, registered.record.scope_id)
            return registered.created, self._view(registered.record)

    def _view(self, record: ClientRunRecord) -> RunSnapshot:
        run = RunSnapshot.model_validate(record.snapshot)
        if (
            record.owner_id != self._owner_id
            and run.state in {"queued", "running"}
            and now() > later(record.updated_at, 120)
        ):
            run.state = "unconfirmed"
            run.error = SafeError(
                status=409,
                code="run_owner_unconfirmed",
                message="原执行者进度已过期，请核对原任务；未重放业务",
                write_outcome="unconfirmed",
            )
        return run

    def _persist(self, run_id: str) -> None:
        assert self._registry is not None
        self._records[run_id] = self._registry.checkpoint(self._records[run_id], self._runs[run_id])

    def get(self, run_id: str) -> RunSnapshot:
        try:
            if str(UUID(run_id)) != run_id:
                raise ValueError
        except ValueError:
            raise ValidationError(400, "invalid_run_id", "运行 ID 格式不正确") from None
        with self._lock:
            if self._registry is None:
                raise ValidationError(503, "demo_unavailable", self._unavailable_reason)
            record = self._registry.get(run_id)
            local = self._runs.get(run_id)
            if (
                local is not None
                and local.error is not None
                and local.error.code == "checkpoint_unconfirmed"
                and record.owner_id == self._owner_id
                and record.snapshot["state"] in {"queued", "running"}
            ):
                return local.model_copy(deep=True)
            return self._view(record)

    def _execute(self, run_id: str, scope_id: str) -> None:
        coverage = Coverage()
        calls: dict[int, list[ApiCall]] = {}
        active_step = 0
        execution = ExecutionData()
        last_state: dict[str, object] | None = None

        def save_state() -> None:
            nonlocal last_state
            with self._lock:
                assert self._registry is not None
                try:
                    envelope = execution.envelope(self._records[run_id])
                except ValueError:
                    raise ValidationError(
                        409,
                        "execution_state_invalid",
                        "运行引用状态不完整，停止后续业务",
                        write_outcome="unconfirmed",
                    ) from None
                content = envelope.model_dump(mode="json", exclude={"sequence", "parent_hash"})
                if content == last_state:
                    return
                updated = self._registry.save_state(self._records[run_id], encode_state(envelope))
                self._records[run_id] = updated
                last_state = content

        def publish(step: StepSnapshot) -> None:
            nonlocal active_step
            with self._lock:
                active_step = step.id
                run = self._runs[run_id]
                run.current_step = step.id
                if step.evidence:
                    step.evidence.calls = list(calls.get(step.id, ()))
                run.steps[step.id - 1] = step.model_copy(deep=True)
                if step.state in {"passed", "failed", "blocked"}:
                    coverage.finish(step.id, step.state)
                run.coverage = coverage.snapshot()
                self._persist(run_id)

        def observe(call: ApiCall) -> None:
            with self._lock:
                coverage.observe(call, active_step)
                if active_step:
                    values = calls.setdefault(active_step, [])
                    if len(values) < 200:
                        values.append(call)
                    evidence = self._runs[run_id].steps[active_step - 1].evidence
                    if evidence:
                        evidence.calls = list(values)
                self._runs[run_id].coverage = coverage.snapshot()

        def journal(call: OperationCall) -> None:
            with self._lock:
                run = self._runs[run_id]
                prior = next(
                    (item for item in run.operations if item.operation_id == call.operation_id),
                    None,
                )
                if prior is not None and (
                    not prior.same_request(call)
                    or prior.phase == "observed"
                    or call.phase != "observed"
                ):
                    raise ValidationError(
                        409,
                        "operation_binding_changed",
                        "原操作意图已存在或已取得回执，禁止重新发送",
                        operation_id=call.operation_id,
                        write_outcome="unconfirmed",
                    )
                if prior is None and len(run.operations) >= 256:
                    raise ValidationError(
                        429, "operation_limit", "本轮操作登记达到上限，未发出新请求"
                    )
                if prior is None:
                    run.operations.append(call)
                else:
                    run.operations[run.operations.index(prior)] = call
                self._persist(run_id)

                if call.phase == "observed":
                    execution.operation_id, execution.phase = call.operation_id, "http_observed"
                    save_state()

        def preserve_input(operation: OperationCall, payload: bytes) -> None:
            with self._lock:
                assert self._registry is not None
                self._registry.save_input(self._records[run_id], operation, payload)
                execution.operation_id, execution.phase = operation.operation_id, "before_effect"
                save_state()

        def current_record() -> ClientRunRecord:
            with self._lock:
                return self._records[run_id]

        try:
            with self._lock:
                assert self._registry is not None
                try:
                    definition = RunDefinition.model_validate_json(
                        self._registry.read_definition(self._records[run_id])
                    )
                except ModelError:
                    raise ValidationError(
                        409, "definition_invalid", "原运行定义不完整或格式不兼容，停止执行"
                    ) from None
                definition.require_execution(self._records[run_id])
                self._runs[run_id].state = "running"
                self._persist(run_id)
                save_state()
            client = self._client
            if client is None:
                raise ValidationError(503, "demo_unavailable", self._unavailable_reason)
            with (
                client.observe_calls(observe),
                client.observe_effects(journal),
                client.preserve_inputs(preserve_input),
                client.bind_client_run(current_record),
            ):
                status = client.status()
                with self._lock:
                    if status.capabilities.ok and status.capabilities.data:
                        self._runs[run_id].mode = Mode.model_validate(
                            status.capabilities.data.model_dump(include=set(Mode.model_fields))
                        )
                    for name in ("live", "health", "ready", "capabilities"):
                        coverage.mark(
                            "GET",
                            "/p3/" + name,
                            "passed" if getattr(status, name).ok else "blocked",
                        )
                if not (
                    status.live.ok
                    and status.ready.ok
                    and status.capabilities.ok
                    and status.ready.data
                    and status.ready.data.readiness == "ready"
                ):
                    raise ValidationError(503, "not_ready", "P3 未就绪；本轮没有发送任何业务写入")
                if self._runs[run_id].scenario_id == "library-basic":
                    run_library(
                        client,
                        scope_id,
                        publish,
                        definition=definition,
                        execution=execution,
                        save_state=save_state,
                        wait_seconds=self.wait_seconds,
                    )
                else:
                    from .diagnostics import collect
                    from .execution import StoryRun
                    from .stories import run_story

                    story = StoryRun(
                        client,
                        scope_id,
                        self._runs[run_id].scenario_id,
                        self._runs[run_id].mode,
                        publish,
                        definition=definition,
                        execution=execution,
                        save_state=save_state,
                        wait_seconds=self.wait_seconds,
                    )
                    try:
                        run_story(story)
                    finally:
                        active_step = 0
                        try:
                            diagnostics = collect(story, coverage)
                        except Exception:
                            diagnostics = Diagnostics(
                                state="incomplete",
                                notes=["诊断采集未完成；业务结果保持不变，未重放操作。"],
                            )
                        with self._lock:
                            self._runs[run_id].diagnostics = diagnostics
            with self._lock:
                self._runs[run_id].state = "passed"
                self._active = None
        except Exception as error:
            if not isinstance(error, ValidationError):
                error = ValidationError(
                    500, "internal_error", "执行出现异常，结果待核对", write_outcome="unconfirmed"
                )
            state: Literal["blocked", "failed", "unconfirmed"] = (
                "blocked"
                if error.code in {"not_ready", "provider_unavailable"}
                else "failed"
                if error.code
                in {
                    "recall_check_failed",
                    "processing_failed",
                    "operation_failed",
                    "check_failed",
                }
                else "unconfirmed"
            )
            with self._lock:
                run = self._runs[run_id]
                run.state = state
                run.error = SafeError.model_validate(error.to_dict())
                for step in run.steps:
                    if step.state == "running":
                        step.state = state
                        coverage.finish(step.id, state)
                    elif step.state == "pending":
                        step.state = "skipped"
                if state != "unconfirmed":
                    self._active = None
        finally:
            with self._lock:
                self._runs[run_id].coverage = coverage.snapshot()
                try:
                    self._persist(run_id)
                except Exception:
                    self._runs[run_id].state = "unconfirmed"
                    self._active = run_id
                    self._runs[run_id].error = SafeError(
                        status=503,
                        code="checkpoint_unconfirmed",
                        message="进度登记结果未确认；请查询原运行，未重放业务",
                        write_outcome="unconfirmed",
                    )

    def recover_initialization(
        self, run_id: str, transfer_id: str, original_definition: bytes | None = None
    ) -> RunSnapshot:
        """Resume only a provably unstarted original run after explicit transfer and activation."""
        try:
            identity = UUID(run_id)
            if str(identity) != run_id:
                raise ValueError
        except ValueError:
            raise ValidationError(400, "invalid_run_id", "运行 ID 格式不正确") from None
        with self._lock:
            if self._closed or self._client is None or self._registry is None:
                raise ValidationError(503, "demo_unavailable", self._unavailable_reason)
            if self._active is not None and self._active != run_id:
                raise ValidationError(409, "run_active", "本实例仍持有另一轮运行，不能派发恢复")
            attempt = self._initializations.setdefault(run_id, InitializationAttempt(transfer_id))
            if attempt.transfer_id != transfer_id:
                raise ValidationError(
                    409, "initialization_intent_changed", "请继续核对本实例原接管标识"
                )
            try:
                prepared = prepare_initialization(
                    self._client,
                    self._registry,
                    attempt,
                    identity,
                    self._owner_id,
                    original_definition,
                )
            except Exception:
                # A rejected identifier or another owner's receipt did not reserve a transfer.
                if attempt.original is None:
                    del self._initializations[run_id]
                raise
            record = prepared.record
            if record != prepared.activation.record or run_id in self._initialization_tokens:
                return self._view(record)
            self._runs[run_id] = RunSnapshot.model_validate(record.snapshot)
            self._records[run_id] = record
            self._active = run_id
            token = object()
            self._initialization_tokens[run_id] = token
            try:
                self._worker.submit(self._execute_initialization, run_id, record.scope_id, token)
            except Exception:
                del self._initialization_tokens[run_id]
                raise ValidationError(
                    503,
                    "initialization_dispatch_failed",
                    "初始化工作线程提交失败，可继续核对原接管并重试",
                    operation_id=transfer_id,
                    write_outcome="unconfirmed",
                ) from None
            return self._view(record)

    def _execute_initialization(self, run_id: str, scope_id: str, token: object) -> None:
        with self._lock:
            if (
                self._initialization_tokens.get(run_id) is not token
                or run_id in self._initialization_started
            ):
                return
            self._initialization_started.add(run_id)
        self._execute(run_id, scope_id)

    def restore_execution(self, run_id: str) -> RestoredExecution:
        """Read original persisted data; do not acquire ownership or dispatch a worker."""
        with self._lock:
            if self._registry is None:
                raise ValidationError(503, "demo_unavailable", self._unavailable_reason)
            record = self._registry.get(run_id)
            try:
                definition = RunDefinition.model_validate_json(
                    self._registry.read_definition(record)
                )
                definition.require_execution(record)
                envelope = ClientExecutionState.from_bytes(self._registry.read_state(record))
                data = ExecutionData.from_envelope(envelope, record)
            except ValueError:
                raise ValidationError(
                    409, "execution_state_invalid", "原执行状态损坏或不兼容，不能重建"
                ) from None
            if self._registry.get(run_id) != record:
                raise ValidationError(
                    409, "execution_state_changed", "读取期间原运行已变化，需要重新核对"
                )
            return RestoredExecution(
                record,
                definition,
                envelope,
                data,
                [value.model_dump(mode="json") for value in envelope.operations]
                == record.snapshot.get("operations", []),
            )

    def reconcile_execution(self, run_id: str, timeout_seconds: float = 60) -> ReconciledExecution:
        """Read original evidence without claiming ownership or restarting a story."""
        if self._client is None or self._registry is None:
            raise ValidationError(503, "demo_unavailable", self._unavailable_reason)
        with self._client.read_budget(timeout_seconds):
            restored = self.restore_execution(run_id)
            findings = reconcile_operations(self._client, restored)
            if self._registry.get(run_id) != restored.record:
                raise ValidationError(
                    409, "execution_state_changed", "读取期间原运行已变化，需要重新核对"
                )
            return ReconciledExecution(restored, findings)

    def transfer_execution(
        self, report: ReconciledExecution, transfer_id: str
    ) -> ClientRunTransferResult:
        """Acquire a fenced recovery hold; do not install or dispatch a local run.

        P3 compares the exact original record in its accepting transaction. A
        lost reply must be queried by the same transfer ID before further work.
        """
        if self._client is None or self._closed:
            raise ValidationError(503, "demo_unavailable", self._unavailable_reason)
        return self._client.transfer_client_run(
            report.restored.record, new_owner_id=self._owner_id, transfer_id=transfer_id
        )

    def activate_execution(self, report: ReconciledExecution) -> ClientRunRecoveryResult:
        """Publish original data plus the current journal; no local story is dispatched."""
        if self._client is None or self._closed:
            raise ValidationError(503, "demo_unavailable", self._unavailable_reason)
        record = report.restored.record
        if record.owner_id != self._owner_id or not record.recovery_held:
            raise ValidationError(409, "recovery_hold_missing", "当前实例未持有原运行的恢复占用")
        envelope = report.restored.data.envelope(record)
        return self._client.activate_client_run(record, envelope)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        # Never close the shared HTTP client while its worker is reading a result.
        self._worker.shutdown(wait=True)
        if self._client is not None:
            self._client.close()
