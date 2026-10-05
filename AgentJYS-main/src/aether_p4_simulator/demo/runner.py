"""Execute the fixed library story using only actual P3 results."""

import time
from collections.abc import Callable
from functools import partial

from aether_agent_memory.runtime.contracts.models import Scope
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from aether_p4_simulator.validation.models import (
    ContextPack,
    RecallRequest,
    RememberReceipt,
    RememberRequest,
)
from aether_p4_simulator.validation.operations import resolve_operation

from .definition import RunDefinition
from .models import Check, Evidence, ProjectionEvidence, StepSnapshot
from .state import ExecutionData, RecallHandle


def _scope_matches(scope: Scope, scope_id: str) -> bool:
    return scope.task_id == scope_id and scope.session_id == scope_id + "_session"


def _scope_error(operation_id: str) -> ValidationError:
    return ValidationError(
        502,
        "scope_mismatch",
        "P3 返回的引用不属于本轮，已停止展示",
        operation_id=operation_id,
        write_outcome="unconfirmed",
    )


def run_library(
    client: P3ValidationClient,
    scope_id: str,
    publish: Callable[[StepSnapshot], None],
    *,
    definition: RunDefinition,
    wait_seconds: float = 60,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    execution: ExecutionData | None = None,
    save_state: Callable[[], None] | None = None,
) -> None:
    state = execution if execution is not None else ExecutionData()
    capture = save_state or (lambda: None)
    receipts = state.receipts
    selection = {"task_id": scope_id, "session_id": scope_id + "_session"}
    for number, turn in enumerate(definition.library, 1):
        state.step, state.phase = number, "step_started"
        started = clock()
        operation_id = f"{scope_id}_{number}"
        evidence = Evidence(path=f"/p3/{turn.action}", operation_id=operation_id)
        step = StepSnapshot(id=number, user_text=turn.user_text, state="running", evidence=evidence)

        def emit(
            evidence: Evidence = evidence,
            started: float = started,
            step: StepSnapshot = step,
        ) -> None:
            evidence.elapsed_ms = round((clock() - started) * 1000, 1)
            publish(step.model_copy(deep=True))
            capture()

        def on_pending(
            job_id: str, evidence: Evidence = evidence, emit: Callable[[], None] = emit
        ) -> None:
            evidence.job_id = job_id
            emit()

        emit()
        if turn.action == "remember":
            body = RememberRequest.model_validate(
                {
                    "source": {
                        "kind": "conversation",
                        "external_id": operation_id,
                        "external_version": definition.text("source_version"),
                        "occurred_at": definition.event_times[number - 1],
                    },
                    "selection": selection,
                    "content": {"kind": "text", "text": turn.user_text},
                }
            )
            receipt = resolve_operation(
                client,
                partial(client.remember, body, operation_id),
                RememberReceipt,
                operation_id=operation_id,
                write=True,
                wait_seconds=wait_seconds,
                clock=clock,
                sleep=sleep,
                on_pending=on_pending,
            )
            state.parsed(operation_id, receipt)
            capture()
            if receipt.operation_id != operation_id:
                raise _scope_error(operation_id)
            if not receipt.saved or not receipt.memories:
                raise ValidationError(
                    409,
                    "save_not_confirmed",
                    "P3 未确认保存记忆",
                    operation_id=operation_id,
                    write_outcome="unconfirmed",
                )
            if any(not _scope_matches(ref.scope, scope_id) for ref in receipt.memories):
                raise _scope_error(operation_id)
            state.scope = state.scope or receipt.memories[0].scope
            state.refs.update({ref.memory_id: ref for ref in receipt.memories})
            state.sources[receipt.source.source_id] = receipt.source
            state.task_ids.update(receipt.task_ids)
            receipts[turn.target] = receipt
            state.consume(operation_id)
            capture()
            evidence.memories = list(receipt.memories)
            evidence.source_ids = [receipt.source.source_id]
            evidence.task_ids = list(receipt.task_ids)
            emit()
            deadline = clock() + wait_seconds
            for ref in receipt.memories:
                while (remaining := deadline - clock()) > 0:
                    processing = client.processing(
                        ref.memory_id, timeout_seconds=min(10.0, remaining)
                    )
                    if processing.memory != ref:
                        raise _scope_error(operation_id)
                    evidence.processing = [
                        p for p in evidence.processing if p.memory_id != ref.memory_id
                    ] + [
                        ProjectionEvidence(
                            memory_id=ref.memory_id,
                            memory_status=processing.memory_status
                            if processing.memory_status
                            in {"active", "archived", "deleted", "superseded", "expired"}
                            else "unknown",
                            projection_state=processing.projection_state
                            if processing.projection_state
                            in {"pending", "building", "ready", "failed", "stale", "not_required"}
                            else "unknown",
                            processing_state=processing.state
                            if processing.state
                            in {
                                "awaiting_consolidation",
                                "processing",
                                "completed",
                                "failed",
                                "awaiting_extraction_provider",
                                "completed_with_summary_failure",
                            }
                            else "unknown",
                        )
                    ]
                    emit()
                    if (
                        processing.memory_status == "active"
                        and processing.projection_state == "ready"
                    ):
                        break
                    if processing.projection_state == "failed" or processing.state == "failed":
                        raise ValidationError(409, "processing_failed", "当前记忆处理失败")
                    sleep(min(0.5, max(0.0, deadline - clock())))
                else:
                    raise ValidationError(
                        504,
                        "observation_timeout",
                        "Working 投影仍未就绪，停止后续步骤",
                        operation_id=operation_id,
                        write_outcome="unconfirmed",
                    )
            step.response_text = "P3 已确认保存；Working 投影已就绪。\n" + " · ".join(
                ref.memory_id for ref in receipt.memories
            )
            step.checks = [Check(name="保存与投影", passed=True, detail="本轮记忆 active / ready")]
        else:
            request = RecallRequest(
                query=turn.user_text,
                selection=selection,
                sources="working",
                token_budget=definition.number("basic_token_budget"),
            )
            pack = resolve_operation(
                client,
                partial(client.recall, request, operation_id),
                ContextPack,
                operation_id=operation_id,
                write=False,
                wait_seconds=wait_seconds,
                clock=clock,
                sleep=sleep,
                on_pending=on_pending,
            )
            state.parsed(operation_id, pack)
            capture()
            items = [item for group in pack.groups for item in group.items]
            all_refs = [ref for receipt in receipts.values() for ref in receipt.memories]
            if (
                not _scope_matches(pack.scope, scope_id)
                or pack.scope != all_refs[0].scope
                or any(item.memory not in all_refs for item in items)
            ):
                raise _scope_error(operation_id)
            target = receipts[turn.target]
            matching = [item for item in items if item.memory in target.memories]
            evidence.recall_id = pack.recall_id
            evidence.context = pack
            evidence.memories = [item.memory for item in items]
            evidence.source_ids = sorted({s.source_id for item in items for s in item.sources})
            step.response_text = pack.rendered_context
            step.checks = [
                Check(name="本轮引用", passed=True, detail="全部返回记忆属于本轮真实回执"),
                Check(
                    name="Working 完整覆盖",
                    passed=(
                        pack.outcome == "available"
                        and pack.coverage.working == "complete"
                        and pack.selected_sources == ("working",)
                    ),
                    detail=f"{pack.outcome} / {pack.coverage.working}",
                ),
                Check(
                    name="目标内容",
                    passed=all(
                        any(term in item.content for item in matching)
                        for term in turn.expected_terms
                    ),
                    detail="只检查实际召回内容，不向问题注入预期答案",
                ),
            ]
            if not all(check.passed for check in step.checks):
                step.state = "failed"
                emit()
                raise ValidationError(409, "recall_check_failed", "真实召回未满足本步检查条件")
            state.recalls["last"] = RecallHandle(recall_id=pack.recall_id, scope=pack.scope)
            state.consume(operation_id)
            capture()
        step.state = "passed"
        state.completed_steps.append(number)
        state.phase = "step_complete"
        emit()
