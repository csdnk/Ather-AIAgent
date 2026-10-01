"""Bounded, in-memory demo ownership."""

from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from typing import Literal, TypedDict
from uuid import UUID, uuid4

from aether_p4_simulator.validation.calls import ApiCall
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError

from .coverage import Coverage
from .models import Diagnostics, Mode, RunSnapshot, SafeError, StartRequest, StepSnapshot
from .runner import run_library
from .scenarios import LIBRARY, SCENARIOS, STORY_TEXTS, ScenarioSummary


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
    ) -> None:
        self._client = client
        self._unavailable_reason = unavailable_reason
        self._lock = RLock()
        self._runs: dict[str, RunSnapshot] = {}
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
            if run_id in self._runs:
                prior = self._runs[run_id]
                if prior.scenario_id != request.scenario_id:
                    raise ValidationError(409, "start_conflict", "开始标识已用于另一个场景")
                return False, prior.model_copy(deep=True)
            if self._active is not None:
                raise ValidationError(409, "run_active", "已有运行或未确认的任务，请先核对")
            if len(self._runs) >= 10:
                raise ValidationError(429, "run_limit", "已保留 10 轮，请在核对任务后重启演示服务")
            texts = (
                tuple(turn.user_text for turn in LIBRARY)
                if request.scenario_id == "library-basic"
                else STORY_TEXTS[request.scenario_id]
            )
            run = RunSnapshot(
                run_id=run_id,
                scenario_id=request.scenario_id,
                total_steps=len(texts),
                coverage=Coverage().snapshot(),
                steps=[StepSnapshot(id=i, user_text=text) for i, text in enumerate(texts, 1)],
            )
            self._runs[run_id] = run
            self._active = run_id
            self._worker.submit(self._execute, run_id, "demo_" + uuid4().hex)
            return True, run.model_copy(deep=True)

    def get(self, run_id: str) -> RunSnapshot:
        try:
            if str(UUID(run_id)) != run_id:
                raise ValueError
        except ValueError:
            raise ValidationError(400, "invalid_run_id", "运行 ID 格式不正确") from None
        with self._lock:
            if run_id not in self._runs:
                raise ValidationError(404, "run_not_found", "P4 没有此轮记录，不能确认先前执行结果")
            return self._runs[run_id].model_copy(deep=True)

    def _execute(self, run_id: str, scope_id: str) -> None:
        coverage = Coverage()
        calls: dict[int, list[ApiCall]] = {}
        active_step = 0

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

        try:
            with self._lock:
                self._runs[run_id].state = "running"
            client = self._client
            if client is None:
                raise ValidationError(503, "demo_unavailable", self._unavailable_reason)
            with client.observe_calls(observe):
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
                    run_library(client, scope_id, publish)
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

    def close(self) -> None:
        with self._lock:
            self._closed = True
        # Never close the shared HTTP client while its worker is reading a result.
        self._worker.shutdown(wait=True)
        if self._client is not None:
            self._client.close()
