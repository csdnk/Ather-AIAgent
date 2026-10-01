"""Five fixed stories consuming public P3 contracts, never core storage."""

import hashlib
import time
from collections.abc import Sequence
from functools import partial

from aether_agent_memory.remember.contracts.models import (
    CorrectionRequest,
    DeleteReceipt,
    DeleteRequest,
    DocumentInput,
    LifecycleRequest,
    MemoryRef,
    MemorySnapshot,
    ReflectionRequest,
    RememberReceipt,
    RetentionRequest,
)
from aether_agent_memory.runtime.contracts.models import Scope
from aether_p4_simulator.validation.errors import ValidationError
from aether_p4_simulator.validation.models import ConsolidateData
from aether_p4_simulator.validation.story_models import DistillResultData, ObjectData, TaskData

from .execution import StoryRun
from .scenarios import RULES


def consolidate(run: StoryRun, keys: Sequence[str]) -> list[MemoryRef]:
    operation = run.op("consolidate")
    receipt = run.resolve(
        partial(run.client.consolidate, run.selection, operation),
        ConsolidateData,
        operation,
    )
    # Admission may return no new tasks when remember already scheduled extraction.
    # Observe those original tasks too; never resubmit a write to get a fresh task.
    task_ids = dict.fromkeys(
        [
            *(task_id for key in keys for task_id in run.receipts[key].task_ids),
            *receipt.task_ids,
        ]
    )
    for task_id in task_ids:
        run.wait_task(task_id)
    refs = run.collect_episodes(keys)
    run.current_step.response_text = (
        f"P3 已返回并核实 {len(refs)} 条实际 episode，长期投影已就绪。"
        "下一步只带本轮 task 范围，不发送旧会话或旧聊天记录。"
    )
    return refs


def library(run: StoryRun) -> None:
    with run.step(1, "/p3/documents/{document_id}", method="PUT"):
        operation = run.op("upload")
        document_id = run.scope_id + "_rules"
        document = run.resolve(
            partial(run.client.upload_document, document_id, RULES, operation),
            DocumentInput,
            operation,
        )
        run.check(
            "文档回执",
            document.document_id == document_id
            and document.document_version == "1"
            and document.expected_hash == hashlib.sha256(RULES.encode("utf-8")).hexdigest(),
            "UTF-8 原始字节、文档 ID、版本及 SHA-256 与本轮上传一致",
        )
        run.remember("rules", RULES, document=document)
    with run.step(2, "/p3/remember") as step:
        run.remember("loan", step.user_text)
    with run.step(3, "/p3/recall") as step:
        run.recall(step.user_text, ("rules",), ("5 本", "30 天"))
    with run.step(4, "/p3/recall") as step:
        run.recall(step.user_text, ("rules",), ("二楼",))
    with run.step(5, "/p3/recall") as step:
        run.recall(step.user_text, ("loan",), ("机器学习入门",))
    with run.step(6, "/p3/remember/body"):
        snapshot = run.current("rules")
        ref = snapshot.ref
        body = run.client.body(ref)
        run.require_ref(body.memory)
        run.require_sources(body.sources)
        run.check(
            "真实正文",
            body.outcome == "read" and body.content == snapshot.content,
            "记忆正文与当前摘要快照一致；原始文档另从来源读取",
        )
        assert body.content is not None  # The preceding checked snapshot has a non-empty body.
        quote = "借期 30 天"
        run.check("借期证据存在", quote in body.content, "真实摘要中包含待核对片段")
        start = body.content.index(quote)
        end = start + len(quote)
        excerpt = run.client.body_range(ref, start, end)
        source = run.receipts["rules"].source
        source_start = RULES.index(quote)
        source_end = source_start + len(quote)
        original = run.client.source_range(source, source_start, source_end)
        run.require_ref(excerpt.memory)
        run.require_sources(excerpt.sources)
        run.check(
            "Unicode 片段",
            excerpt.outcome == "read"
            and excerpt.content == quote
            and (excerpt.start_char, excerpt.end_char) == (start, end)
            and original.source == source
            and original.content == quote
            and (original.start_char, original.end_char) == (source_start, source_end),
            "摘要与原文各自按 Unicode 字符定位，返回的引用片段一致",
        )
        run.current_step.response_text = (
            f"当前记忆摘要：{body.content}\n原始来源片段：{original.content}"
        )
        run.evidence.memories, run.evidence.source_ids = [ref], [source.source_id]
    with run.step(7, "/p3/recall") as step:
        run.recall(step.user_text, ("rules",), ("9 点", "17 点"))
    with run.step(8, "/p3/recall"):
        run.remember("noise", "我今天整理了书桌，把水杯放到左边。")
        run.recalls["last"] = run.recall(
            "再确认一下，我借的是哪一本书？",
            ("loan",),
            ("机器学习入门",),
        )
    with run.step(9, "/p3/memories", method="GET"):
        items = run.catalog()
        pack = run.recalls["last"]
        record = run.client.recall_record(pack.recall_id)
        if record.recall_id != pack.recall_id or not run.scope_matches(record.scope):
            run.reject_scope()
        run.check(
            "召回记录",
            record.state == "completed" and record.result_available,
            "指定本轮 recall 的记录已完成并可读取",
        )
        run.evidence.recall_id = pack.recall_id
        run.current_step.response_text = f"本轮目录读取 {len(items)} 条；指定召回记录已完成。"


def weather(run: StoryRun) -> None:
    keys = ("weather", "park", "backup")
    for number, key in enumerate(keys, 1):
        with run.step(number, "/p3/remember") as step:
            run.remember(key, step.user_text)
    with run.step(4, "/p3/recall") as step:
        run.recall(step.user_text, ("weather",), ("小雨", "20 摄氏度"))
    with run.step(5, "/p3/recall") as step:
        run.recall(step.user_text, ("backup",), ("图书馆",))
    with run.step(6, "/p3/remember/consolidate"):
        consolidate(run, keys)
    with run.step(7, "/p3/recall"):
        run.recall("我之前为雨天定的备用安排是什么？", ("backup",), ("图书馆",), long_term=True)
    with run.step(8, "/p3/recall"):
        run.recall("我原来的户外计划是什么？", ("park",), ("公园",), long_term=True)
        run.placement(run.episodes["park"][0])


def preferences(run: StoryRun) -> None:
    with run.step(1, "/p3/remember") as step:
        run.remember("preference", step.user_text)
    with run.step(2, "/p3/recall") as step:
        old = run.recall(step.user_text, ("preference",), ("无糖咖啡", "安静"))
    with run.step(3, "/p3/remember/{memory_id}/correct"):
        before = run.current("preference")
        operation = run.op("correct")
        request = CorrectionRequest(
            expected_version=before.ref.version,
            expected_object_revision=before.object_revision,
            content="我喜欢无糖红茶，阅读时希望安静。",
            source=run.source_input("corrected_source"),
            reason="本轮虚构偏好更正",
        )
        receipt = run.resolve(
            partial(run.client.correct, before.ref.memory_id, request, operation),
            RememberReceipt,
            operation,
        )
        run.accept_receipt("preference", receipt, operation, corrected=before.ref)
        after = run.current("preference")
        run.check(
            "版本与无关事实",
            after.ref.version == before.ref.version + 1
            and "无糖红茶" in after.content
            and "安静" in after.content
            and "咖啡" not in after.content,
            "只更正饮品；安静阅读要求仍在真实新版本中",
        )
        run.current_step.response_text = f"P3 当前版本 v{after.ref.version}：{after.content}"
    with run.step(4, "/p3/recall") as step:
        pack = run.recall(step.user_text, ("preference",), ("无糖红茶", "安静"))
        run.check(
            "旧偏好不混入",
            all("咖啡" not in item.content for group in pack.groups for item in group.items),
            "本轮新召回未混入旧饮品事实",
        )
    with run.step(5, "/p3/recalls/{recall_id}/result", method="GET"):
        run.old_result_invalid(old)
    with run.step(6, "/p3/remember/{memory_id}/lifecycle"):
        for target in ("archived", "active"):
            before = run.current("preference")
            operation = run.op(target)
            lifecycle_request = LifecycleRequest(
                expected_version=before.ref.version,
                expected_object_revision=before.object_revision,
                target=target,
                reason="本轮虚构安排生命周期检查",
            )
            item = run.resolve(
                partial(run.client.lifecycle, before.ref.memory_id, lifecycle_request, operation),
                MemorySnapshot,
                operation,
            )
            run.require_ref(item.ref)
            run.require_sources(item.sources)
            run.check("生命周期 " + target, item.status == target, "使用每次新读取的对象修订号")
        run.wait_ready(item.ref)
        run.current_step.response_text = "P3 已先确认归档，再确认恢复；恢复后的投影已就绪。"
    with run.step(7, "/p3/remember/{memory_id}/retention"):
        before = run.current("preference")
        prior = run.client.retention(before.ref.memory_id).root
        if MemoryRef.model_validate(prior.get("memory")) != before.ref:
            run.reject_scope()
        operation = run.op("retention")
        retention_request = RetentionRequest(
            expected_version=before.ref.version,
            expected_object_revision=before.object_revision,
            enabled=True,
            completed=True,
            archive_after_idle_hours=168,
            reason="本轮只验证策略设置与回读，不等待实际过期",
        )
        configured = run.resolve(
            partial(
                run.client.configure_retention, before.ref.memory_id, retention_request, operation
            ),
            ObjectData,
            operation,
        ).root
        value = run.client.retention(before.ref.memory_id).root
        if MemoryRef.model_validate(value.get("memory")) != before.ref:
            run.reject_scope()
        policy = value.get("policy", {})
        run.check(
            "保留策略回读",
            configured.get("object_revision", 0) > before.object_revision
            and policy.get("version") == before.ref.version
            and policy.get("enabled") is True
            and policy.get("completed") is True
            and policy.get("idle_hours") == 168,
            "168 小时闲置归档策略已回读；本轮不声称过期或物理清理完成",
        )
        run.current_step.response_text = "实际策略：已启用、Working 已完成、闲置 168 小时后归档。"
    with run.step(8, "/p3/remember/{memory_id}/reprocess"):
        for name in ("reprocess", "reindex"):
            ref = run.current("preference").ref
            operation = run.op(name)
            task_receipt = run.resolve(
                partial(getattr(run.client, name), ref.memory_id, operation),
                TaskData,
                operation,
            )
            run.wait_task(task_receipt.task_id)
            run.wait_ready(ref)
        run.check("重处理与重建索引", True, "两个实际任务均 succeeded，且当前投影 ready")
        run.current_step.response_text = "重新处理与重建索引的任务已完成；不是只展示受理回执。"


def learning(run: StoryRun) -> None:
    keys = ("lists", "dicts", "practice")
    for number, key in enumerate(keys, 1):
        with run.step(number, "/p3/remember") as step:
            run.remember(key, step.user_text)
    with run.step(4, "/p3/recall") as step:
        run.recall(step.user_text, keys, ("列表", "字典", "小练习"))
    with run.step(5, "/p3/remember/consolidate"):
        episodes = consolidate(run, keys)
        run.placement(episodes[0])
    with run.step(6, "/p3/remember/reflection"):
        prior = run.client.reflection(run.selection).root
        run.check(
            "独立反思范围",
            prior.get("revision") == 0 and prior.get("status") == "not_enrolled",
            "仅配置本轮新建且尚未登记的范围",
        )
        operation = run.op("reflection")
        request = ReflectionRequest(
            selection=run.selection,
            expected_revision=0,
            min_episodes=3,
            period_hours=24,
            reason="本轮学习记录反思设置检查",
        )
        run.resolve(
            partial(run.client.configure_reflection, request, operation),
            ObjectData,
            operation,
        )
        value = run.client.reflection(run.selection).root
        if not run.scope_matches(Scope.model_validate(value.get("scope"))):
            run.reject_scope()
        run.check(
            "反思策略回读",
            value.get("revision") == 1 and value.get("policy") == request.model_dump(mode="json"),
            "登记与回读一致；不把 24 小时策略当成本轮已验证周期提炼",
        )
        run.current_step.response_text = (
            "P3 已登记并回读反思策略：至少 3 条 episode，周期 24 小时。"
        )
    with run.step(7, "/p3/remember/distill"):
        for ref in episodes:
            run.require_ref(ref)
        before = {ref.memory_id: ref for ref in run.catalog(require_complete=True)}
        operation = run.op("distill")
        receipt = run.resolve(
            partial(run.client.distill, tuple(episodes), operation),
            TaskData,
            operation,
        )
        task = run.wait_task(receipt.task_id, allow_failed=True)
        if task.state != "succeeded":
            # A failed job alone is NOT proof that the model is missing.
            # Periodic reflection publishes independently of the distill task.
            deadline = time.monotonic() + run.wait_seconds
            reflection = {}
            while (remaining := deadline - time.monotonic()) > 0:
                reflection = run.client.reflection(
                    run.selection, timeout_seconds=min(10, remaining)
                ).root
                if not run.scope_matches(Scope.model_validate(reflection.get("scope"))):
                    run.reject_scope()
                if reflection.get("status") == "provider_unavailable":
                    break
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            if (
                run.mode is not None
                and run.mode.semantic_processing == "literal_baseline"
                and task.state == "failed"
                and task.effect_status == "no_effect"
                and reflection.get("status") == "provider_unavailable"
            ):
                run.current_step.response_text = (
                    "P3 已受理提炼，但真实任务失败且未产生效果；"
                    "反思状态明确为 provider_unavailable。当前未配置模型，不能展示提炼总结。"
                )
                run.check("条件不足证据", True, "failed / no_effect + provider_unavailable")
                raise ValidationError(503, "provider_unavailable", "未配置提炼模型；不生成虚构总结")
            raise ValidationError(409, "processing_failed", "真实提炼任务未成功，不能展示生成结果")
        run.check(
            "提炼任务与产物绑定",
            task.kind == "remember.distill"
            and task.owner_flow == "remember"
            and task.effect_status == "confirmed"
            and task.subject.owner == "remember"
            and task.subject.object_type == "memory"
            and task.subject.object_id == episodes[0].memory_id
            and task.result_ref is not None
            and task.result_ref.owner == "remember"
            and task.result_ref.object_type == "processing_result"
            and task.result_ref.object_id == task.task_id
            and task.result_ref.scope == task.subject.scope,
            "原任务已成功且效果确认；结果必须绑定该提炼任务及本轮 episode",
        )
        run.current_step.response_text = "原提炼任务已成功；下一步读取该任务的实际产物。"
    with run.step(8, "/p3/operations/{job_id}/result", method="GET"):
        run.evidence.job_id = task.task_id
        run.evidence.task_ids = [task.task_id]
        result = run.client.operation_result(task.task_id, DistillResultData, timeout_seconds=10)
        run.check(
            "任务产物结构",
            result.zero_output == (not result.memories)
            and result.candidate_count >= len(result.memories)
            and not result.awaiting_extraction_provider
            and len({ref.memory_id for ref in result.memories}) == len(result.memories),
            "只接受原任务的完整结果；无产出、延后提取与重复引用不能混作新总结",
        )
        generated, counts = [], {"新增": 0, "更新": 0, "复用": 0}
        for ref in result.memories:
            if not run.scope_matches(ref.scope):
                run.reject_scope()
            item = run.client.memory(ref.memory_id)
            if item.ref != ref or not run.scope_matches(item.ref.scope):
                run.reject_scope()
            run.require_sources(item.sources)
            run.check(
                "真实语义产物",
                item.kind == "semantic" and item.status == "active",
                "提炼结果必须是 active semantic，不能把 episode 当作总结",
            )
            previous = before.get(ref.memory_id)
            if previous is None:
                label = "新增"
            elif previous == ref:
                label = "复用"
            else:
                run.check(
                    "提炼版本连续",
                    ref.version == previous.version + 1 and item.supersedes == previous,
                    "同 ID 更新须为基线的直接后继，不猜测跨版本产物",
                )
                label = "更新"
            counts[label] += 1
            run.refs[ref.memory_id] = ref
            run.wait_ready(ref)
            generated.append(item)
        run.check("实际任务产物", True, "逐项核对原任务引用、正文、版本和本轮来源")
        run.evidence.memories = [item.ref for item in generated]
        run.current_step.response_text = (
            "任务成功，但未产生输出；不展示已有目录内容充当本次总结。"
            if result.zero_output
            else "原任务返回："
            + "、".join(f"{label} {count}" for label, count in counts.items())
            + "。复用项不是新增总结。\n\n"
            + "\n\n".join(item.content for item in generated)
        )


def forget(run: StoryRun) -> None:
    cleanup_tasks: list[str] = []
    with run.step(1, "/p3/remember") as step:
        run.remember("booking", step.user_text)
    with run.step(2, "/p3/recall") as step:
        old = run.recall(step.user_text, ("booking",), ("DEMO-BOOK-017",))
    with run.step(3, "/p3/remember/{memory_id}/delete"):
        item = run.current("booking")
        operation = run.op("delete_booking")
        receipt = run.resolve(
            partial(
                run.client.delete_memory,
                item.ref.memory_id,
                DeleteRequest(expected_revision=item.object_revision, reason="本轮虚构预约取消"),
                operation,
            ),
            DeleteReceipt,
            operation,
        )
        deletion_evidence(run, receipt, operation)
        cleanup_tasks.extend(receipt.task_ids)
    with run.step(4, "/p3/recalls/{recall_id}/result", method="GET"):
        run.old_result_invalid(old)
        run.recall("我的测试预约编号是多少？", (), absent=tuple(run.refs))
        body = run.client.body(run.receipts["booking"].memories[0])
        run.check(
            "正文读屏障", body.outcome == "excluded", "删除后正文不可读取，不只检查搜索未命中"
        )
    for number, key, text, name in (
        (5, "second", "独立测试资料二：纸质活动票 DEMO-SOURCE-028。", "delete_source"),
        (6, "third", "独立测试资料三：演示取件码 DEMO-SOURCE-039。", "revoke_source"),
    ):
        path = "/p3/sources/{source_id}/" + ("delete" if name == "delete_source" else "revoke")
        with run.step(number, path):
            saved = run.remember(key, text)
            run.current(key)  # Verify source binding before any destructive operation.
            run.require_sources((saved.source,))
            operation = run.op(name)
            # P3 initializes NEW sources at revision 1. This is not source_version:
            # these sources were created here and have never been mutated.
            receipt = run.resolve(
                partial(
                    getattr(run.client, name),
                    saved.source.source_id,
                    DeleteRequest(expected_revision=1, reason="仅本轮独立虚构资料"),
                    operation,
                ),
                DeleteReceipt,
                operation,
            )
            deletion_evidence(run, receipt, operation)
            cleanup_tasks.extend(receipt.task_ids)
            try:
                run.client.source_range(saved.source, 0, 8)
            except ValidationError as error:
                if error.status != 410 or error.code != "memory_gone":
                    raise
                run.check("来源读屏障", True, "实际来源读取已被拒绝（410 MEMORY_GONE）")
            else:
                run.check("来源读屏障", False, "删除/撤销后来源仍可读")
    with run.step(7, "/p3/recall"):
        run.recall("测试预约、活动票与取件码还有记录吗？", (), absent=tuple(run.refs))
        run.catalog()
        for task_id in dict.fromkeys(cleanup_tasks):
            run.wait_task(task_id)
        run.check("后台处置任务", True, "本轮返回的清理/重验证任务均已报告成功")
        run.current_step.response_text = (
            "本轮三份资料均未被召回，相关处置任务已完成。"
            "这是可见性与任务状态检查，不代表来源保留策略下的全部物理副本已擦除。"
        )


def deletion_evidence(run: StoryRun, receipt: DeleteReceipt, operation: str) -> None:
    if receipt.operation_id != operation:
        run.reject_scope()
    run.task_ids.update(receipt.task_ids)
    run.evidence.task_ids = list(receipt.task_ids)
    run.evidence.cleanup_state = receipt.cleanup_state
    run.check("读屏障已提交", receipt.blocked, "真实删除/撤销回执；不把 pending 写成 completed")
    run.current_step.response_text = (
        f"P3 已确认读屏障，回执清理状态：{receipt.cleanup_state}。"
        "物理清理需另外核对，不能用“已受理”冒充完成。"
    )


def run_story(run: StoryRun) -> None:
    {
        "library-full": library,
        "weather-weekend": weather,
        "preference-update": preferences,
        "learning-review": learning,
        "forget-sources": forget,
    }[run.scenario_id](run)
