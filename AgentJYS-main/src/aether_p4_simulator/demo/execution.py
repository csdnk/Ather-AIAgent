"""Small fixed-story context: ownership, checked results and bounded observation."""

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from functools import partial
from typing import Literal, NoReturn

from pydantic import BaseModel

from aether_agent_memory.remember.contracts.models import (
    DocumentInput,
    MemoryRef,
    MemorySnapshot,
    RememberReceipt,
    RememberRequest,
    SourceInput,
    SourceRef,
)
from aether_agent_memory.runtime.contracts.models import Scope, TaskRecord
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from aether_p4_simulator.validation.models import (
    ContextPack,
    ProcessingData,
    RecallRequest,
    ScopeSelector,
)
from aether_p4_simulator.validation.operations import resolve_operation

from .definition import RunDefinition
from .models import (
    Check,
    Evidence,
    Mode,
    ProjectionEvidence,
    ScenarioID,
    StepSnapshot,
    TaskEvidence,
)
from .state import ExecutionData, RecallHandle


class StoryRun:
    def __init__(
        self,
        client: P3ValidationClient,
        scope_id: str,
        scenario_id: ScenarioID,
        mode: Mode | None,
        publish: Callable[[StepSnapshot], None],
        *,
        definition: RunDefinition,
        wait_seconds: float = 60,
        execution: ExecutionData | None = None,
        save_state: Callable[[], None] | None = None,
    ) -> None:
        self.client, self.scope_id, self.scenario_id = client, scope_id, scenario_id
        if definition.scenario_id != scenario_id:
            raise ValidationError(409, "definition_scenario_changed", "运行场景与原定义不同")
        self.definition = definition
        self.mode, self.publish, self.wait_seconds = mode, publish, wait_seconds
        self.selection = ScopeSelector(task_id=scope_id, session_id=scope_id + "_session")
        self.execution = execution if execution is not None else ExecutionData()
        self.save_state = save_state or (lambda: None)
        self.refs = self.execution.refs
        self.sources = self.execution.sources
        self.receipts = self.execution.receipts
        self.episodes = self.execution.episodes
        self.recalls = self.execution.recalls
        self.task_ids = self.execution.task_ids
        self._current_step: StepSnapshot | None = None
        self.started = 0.0

    @property
    def scope(self) -> Scope | None:
        return self.execution.scope

    @scope.setter
    def scope(self, value: Scope | None) -> None:
        self.execution.scope = value

    def consume(self, operation_id: str) -> None:
        self.execution.consume(operation_id)
        self.save_state()

    @property
    def current_step(self) -> StepSnapshot:
        assert self._current_step is not None, "story execution requires an active step"
        return self._current_step

    @contextmanager
    def step(
        self, number: int, path: str, *, method: Literal["GET", "POST", "PUT"] = "POST"
    ) -> Iterator[StepSnapshot]:
        self.started = time.monotonic()
        self.execution.step, self.execution.phase = number, "step_started"
        self._current_step = StepSnapshot(
            id=number,
            user_text=self.definition.texts[number - 1],
            state="running",
            evidence=Evidence(method=method, path=path, operation_id=f"{self.scope_id}_{number}"),
        )
        self.emit()
        try:
            yield self.current_step
            self.current_step.state = "passed"
            self.execution.completed_steps.append(number)
            self.execution.phase = "step_complete"
        finally:
            self.emit()

    @property
    def evidence(self) -> Evidence:
        evidence = self.current_step.evidence
        assert evidence is not None, "story steps always include evidence"
        return evidence

    def emit(self) -> None:
        self.evidence.elapsed_ms = round((time.monotonic() - self.started) * 1000, 1)
        self.publish(self.current_step)
        self.save_state()

    def check(self, name: str, condition: bool, detail: str) -> None:
        self.current_step.checks.append(Check(name=name, passed=bool(condition), detail=detail))
        if not condition:
            self.emit()
            raise ValidationError(409, "check_failed", "真实结果未满足检查：" + name)

    def op(self, name: str) -> str:
        return f"{self.scope_id}_{self.current_step.id}_{name}"

    def resolve[T: BaseModel](
        self, call: Callable[[], T], model: type[T], operation_id: str, *, write: bool = True
    ) -> T:
        def pending(job_id: str) -> None:
            self.evidence.job_id = job_id
            self.emit()

        result = resolve_operation(
            self.client,
            call,
            model,
            operation_id=operation_id,
            write=write,
            wait_seconds=self.wait_seconds,
            on_pending=pending,
        )
        self.execution.parsed(operation_id, result)
        self.save_state()
        return result

    def scope_matches(self, scope: Scope, *, task_only: bool = False) -> bool:
        if scope.task_id != self.scope_id:
            return False
        if scope.session_id != (None if task_only else self.selection.session_id):
            return False
        if self.scope is None:
            return True
        return all(
            getattr(scope, field) == getattr(self.scope, field)
            for field in ("tenant_id", "application_id", "user_id", "agent_id")
        )

    def reject_scope(self) -> NoReturn:
        raise ValidationError(
            502,
            "scope_mismatch",
            "返回引用不属于本轮或来源不一致，停止展示与后续操作",
            operation_id=self.evidence.operation_id,
            write_outcome="unconfirmed",
        )

    def require_ref(self, ref: MemoryRef) -> None:
        if not self.scope_matches(ref.scope) or self.refs.get(ref.memory_id) != ref:
            self.reject_scope()

    def require_sources(self, sources: Sequence[SourceRef]) -> None:
        if not sources or any(self.sources.get(s.source_id) != s for s in sources):
            self.reject_scope()

    def source_input(
        self,
        name: str,
        kind: Literal[
            "conversation", "tool_result", "task_state", "text", "document"
        ] = "conversation",
    ) -> SourceInput:
        return SourceInput(
            kind=kind,
            external_id=self.op(name),
            external_version=self.definition.text("source_version"),
            occurred_at=self.definition.event_times[self.current_step.id - 1],
        )

    def accept_receipt(
        self,
        key: str,
        receipt: RememberReceipt,
        operation_id: str,
        *,
        corrected: MemoryRef | None = None,
        summarize: bool = False,
    ) -> None:
        if receipt.operation_id != operation_id or not receipt.memories:
            self.reject_scope()
        for ref in receipt.memories:
            if not self.scope_matches(ref.scope):
                self.reject_scope()
            if corrected is not None and (
                ref.memory_id != corrected.memory_id or ref.version != corrected.version + 1
            ):
                self.reject_scope()
            if corrected is None and ref.memory_id in self.refs:
                self.reject_scope()
        self.scope = self.scope or receipt.memories[0].scope
        if any(ref.scope != self.scope for ref in receipt.memories):
            self.reject_scope()
        self.sources[receipt.source.source_id] = receipt.source
        for ref in receipt.memories:
            self.refs[ref.memory_id] = ref
        self.receipts[key] = receipt
        self.task_ids.update(receipt.task_ids)
        self.consume(operation_id)
        self.evidence.memories = list(receipt.memories)
        self.evidence.source_ids = [receipt.source.source_id]
        self.evidence.task_ids = list(receipt.task_ids)
        self.emit()
        for ref in receipt.memories:
            if summarize:
                ref = self.finish_summary(ref, receipt)
            self.wait_ready(ref)
        self.check("保存与投影", receipt.saved, "本轮实际记忆 active / ready")

    def finish_summary(self, original: MemoryRef, receipt: RememberReceipt) -> MemoryRef:
        """Only an owned summary publication can advance this document's version."""
        self.require_ref(original)
        tasks = [self.task(task_id) for task_id in receipt.task_ids]
        summaries = [
            task
            for task in tasks
            if task.kind == "remember.summarize"
            and task.subject.owner == "remember"
            and task.subject.object_type == "memory"
            and task.subject.object_id == original.memory_id
        ]
        if not summaries:
            state = self.processing(original)
            if state.working_summary is not None:
                self.reject_scope()
            return original
        if len(summaries) != 1:
            self.reject_scope()
        task = self.wait_task(summaries[0].task_id)
        item = self.client.memory(original.memory_id)
        if (
            task.effect_status != "confirmed"
            or item.ref.memory_id != original.memory_id
            or item.ref.scope != original.scope
            or item.ref.version != original.version + 1
            or item.supersedes != original
            or item.sources != (receipt.source,)
            or item.kind != "working"
            or item.status != "active"
        ):
            self.reject_scope()
        state = self.client.processing(original.memory_id, timeout_seconds=10)
        summary = state.working_summary
        if (
            state.memory != item.ref
            or summary is None
            or summary.memory != item.ref
            or summary.source != receipt.source
            or summary.task_id != task.task_id
            or summary.state != "ready"
            or summary.representation != "extractive_summary"
            or summary.is_complete is not False
        ):
            self.reject_scope()
        # The immutable save receipt still records v1; current ownership records v2.
        self.refs[item.ref.memory_id] = item.ref
        self.evidence.memories = [original, item.ref]
        self.check("摘要版本链", True, "真实摘要任务成功；来源一致且新版本明确 supersedes 原版本")
        return item.ref

    def remember(
        self, key: str, text: str, *, document: DocumentInput | None = None
    ) -> RememberReceipt:
        operation_id = self.op("save_" + key)
        body = RememberRequest(
            source=self.source_input("source_" + key, "document" if document else "conversation"),
            selection=self.selection,
            content=document or {"kind": "text", "text": text},
        )
        receipt = self.resolve(
            partial(self.client.remember, body, operation_id),
            RememberReceipt,
            operation_id,
        )
        self.accept_receipt(key, receipt, operation_id, summarize=document is not None)
        actual = self.current(key)
        self.current_step.response_text = (
            f"P3 已确认保存 {len(receipt.memories)} 条本轮记忆，Working 投影已就绪。\n"
            + actual.content
        )
        return receipt

    def current(self, key: str) -> MemorySnapshot:
        ref = self.refs[self.receipts[key].memories[0].memory_id]
        self.require_ref(ref)
        item = self.client.memory(ref.memory_id)
        self.require_ref(item.ref)
        self.require_sources(item.sources)
        return item

    def processing(self, ref: MemoryRef, *, remaining: float = 10) -> ProcessingData:
        self.require_ref(ref)
        value = self.client.processing(ref.memory_id, timeout_seconds=min(10, remaining))
        if value.memory != ref:
            self.reject_scope()
        self.evidence.processing = [
            row for row in self.evidence.processing if row.memory_id != ref.memory_id
        ] + [
            ProjectionEvidence(
                memory_id=ref.memory_id,
                memory_status=value.memory_status,
                projection_state=value.projection_state,
                processing_state=value.state,
            )
        ]
        self.emit()
        return value

    def wait_ready(self, ref: MemoryRef) -> ProcessingData:
        deadline = time.monotonic() + self.wait_seconds
        while (remaining := deadline - time.monotonic()) > 0:
            state = self.processing(ref, remaining=remaining)
            if state.memory_status == "active" and state.projection_state == "ready":
                return state
            if state.projection_state == "failed":
                raise ValidationError(409, "processing_failed", "当前记忆投影处理失败")
            time.sleep(min(0.2, remaining))
        self.timeout()

    def timeout(self) -> NoReturn:
        raise ValidationError(
            504,
            "observation_timeout",
            "观察期限已到，未确认完成；没有补发写入",
            operation_id=self.evidence.operation_id,
            write_outcome="unconfirmed",
        )

    def task(self, task_id: str, *, remaining: float = 10) -> TaskRecord:
        if task_id not in self.task_ids:
            self.reject_scope()
        task = self.client.task(task_id, timeout_seconds=min(10, remaining))
        if task.task_id != task_id or not self.scope_matches(task.subject.scope):
            self.reject_scope()
        # Tasks may target derived objects, but must be in this exact run scope.
        row = TaskEvidence(
            task_id=task_id,
            kind=task.kind,
            state=task.state,
            effect_status=task.effect_status,
            error_code=task.error_code,
        )
        self.evidence.tasks = [r for r in self.evidence.tasks if r.task_id != task_id] + [row]
        if task_id not in self.evidence.task_ids:
            self.evidence.task_ids.append(task_id)
        self.emit()
        return task

    def wait_task(self, task_id: str, *, allow_failed: bool = False) -> TaskRecord:
        self.task_ids.add(task_id)
        deadline = time.monotonic() + self.wait_seconds
        while (remaining := deadline - time.monotonic()) > 0:
            task = self.task(task_id, remaining=remaining)
            if task.state in {"succeeded", "failed", "cancelled"}:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.timeout()
                progress = self.client.task_progress(
                    task_id,
                    timeout_seconds=min(10, remaining),
                ).root
                stages = progress.get("stages")
                checkpoints = progress.get("checkpoints")
                if not isinstance(stages, list) or not isinstance(checkpoints, list):
                    raise ValidationError(502, "upstream_protocol_error", "任务进度结构不完整")
                if any(row.get("task_id") != task_id for row in stages + checkpoints):
                    self.reject_scope()
                self.evidence.tasks[-1].completed_parts = sum(
                    int(row.get("completed_parts", 0)) for row in stages
                )
                if task.state == "succeeded" or allow_failed:
                    return task
                raise ValidationError(409, "processing_failed", "P3 报告本轮后台任务未成功")
            # Temporal marks an in-flight effect unknown before it commits. Observe
            # the original running task; never advance or reissue its write.
            if task.state == "attention_required" or (
                task.effect_status == "unknown" and task.state != "running"
            ):
                raise ValidationError(
                    409,
                    "operation_unconfirmed",
                    "任务效果未知，需要人工核对",
                    write_outcome="unconfirmed",
                )
            time.sleep(0.2)
        self.timeout()

    def collect_episodes(self, keys: Sequence[str]) -> list[MemoryRef]:
        """Discover through owned processing results, then validate each full snapshot."""
        result = {}
        for key in keys:
            found = []
            for saved in self.receipts[key].memories:
                working = self.refs[saved.memory_id]
                state = self.processing(working)
                for memory_id in state.derived_memory_ids:
                    item = self.client.memory(memory_id)
                    if item.ref.memory_id != memory_id or not self.scope_matches(item.ref.scope):
                        self.reject_scope()
                    self.require_sources(item.sources)
                    if item.kind != "episodic" or item.status != "active":
                        continue
                    self.refs[memory_id] = item.ref
                    self.wait_ready(item.ref)
                    found.append(item.ref)
            self.check("实际 episode", bool(found), "长期化后读取真实派生记忆，不用 Working 冒充")
            result[key] = tuple(found)
        self.episodes.update(result)
        self.evidence.memories = list(
            {ref.memory_id: ref for refs in result.values() for ref in refs}.values()
        )
        return self.evidence.memories

    def recall(
        self,
        query: str,
        keys: Sequence[str],
        terms: Sequence[str] = (),
        *,
        long_term: bool = False,
        absent: Sequence[str] = (),
    ) -> ContextPack:
        operation_id = self.op("recall")
        body = RecallRequest(
            query=query,
            selection=ScopeSelector(task_id=self.scope_id) if long_term else self.selection,
            sources="long_term" if long_term else "working",
            token_budget=self.definition.number("story_token_budget"),
        )
        pack = self.resolve(
            partial(self.client.recall, body, operation_id),
            ContextPack,
            operation_id,
            write=False,
        )
        if not self.scope_matches(pack.scope, task_only=long_term):
            self.reject_scope()
        items = [item for group in pack.groups for item in group.items]
        for item in items:
            self.require_ref(item.memory)
            self.require_sources(item.sources)
        allowed = {
            ref.memory_id
            for key in keys
            for ref in (self.episodes[key] if long_term else self.receipts[key].memories)
        }
        matching = [item for item in items if item.memory.memory_id in allowed]
        self.evidence.context, self.evidence.recall_id = pack, pack.recall_id
        self.evidence.memories = [item.memory for item in items]
        self.evidence.source_ids = sorted({s.source_id for item in items for s in item.sources})
        self.current_step.response_text = (
            pack.rendered_context or "P3 返回空结果：本轮范围未召回记忆。"
        )
        self.check("本轮引用", True, "返回引用、来源及 task 范围全部归属本轮")
        coverage = pack.coverage.long_term if long_term else pack.coverage.working
        self.check(
            "召回覆盖",
            pack.outcome in {"available", "empty"} and coverage == "complete",
            f"{pack.outcome} / {coverage}",
        )
        self.check(
            "实际目标内容",
            all(any(term in item.content for item in matching) for term in terms),
            "检查真实返回，不将答案送入查询",
        )
        self.check(
            "不可见性",
            not any(item.memory.memory_id in absent for item in items),
            "被删除/撤销的目标不得出现在返回中",
        )
        self.recalls["last"] = RecallHandle(recall_id=pack.recall_id, scope=pack.scope)
        self.consume(operation_id)
        return pack

    def old_result_invalid(self, pack: RecallHandle | ContextPack) -> None:
        # pack was returned and scope-checked earlier in this run.
        self.evidence.recall_id = pack.recall_id
        try:
            self.client.recall_result(pack.recall_id)
        except ValidationError as error:
            if error.status != 410 or error.code != "recall_invalidated":
                raise
            self.check("旧结果失效", True, "指定旧 recall 的真实响应为 410 RESULT_INVALIDATED")
            self.current_step.response_text = (
                "P3 已拒绝使用旧召回结果（410）；不是拿其他错误冒充失效。"
            )
        else:
            self.check("旧结果失效", False, "旧结果仍可读取")

    def placement(self, ref: MemoryRef) -> None:
        self.require_ref(ref)
        value = self.client.placement(ref.memory_id).root
        if MemoryRef.model_validate(value.get("memory")) != ref:
            self.reject_scope()
        self.check("调度信息", True, "仅验证本轮目标的实际调度快照，不推断迁移已完成")

    def catalog(self, *, require_complete: bool = False) -> list[MemoryRef]:
        items = []
        cursor = None
        for _ in range(3):
            page = self.client.catalog(self.selection, cursor).root
            if not isinstance(page.get("items"), list):
                raise ValidationError(502, "upstream_protocol_error", "目录结构不完整")
            for row in page["items"]:
                ref = MemoryRef.model_validate(row.get("ref"))
                if not self.scope_matches(ref.scope):
                    self.reject_scope()
                items.append(ref)
            cursor = page.get("next_cursor")
            if not cursor:
                break
        if require_complete and (cursor or len({ref.memory_id for ref in items}) != len(items)):
            raise ValidationError(
                502, "catalog_incomplete", "未取得完整且无重复的目录基线，停止提交提炼"
            )
        self.check("目录范围", True, f"读取本轮目录 {len(items)} 条；目录元数据不等于正文可读")
        return items
