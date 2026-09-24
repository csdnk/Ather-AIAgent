"""Bounded Working summaries; originals remain evidence, never replaced by summaries."""

import asyncio
import re
from typing import Any, Protocol, cast

from aether_agent_memory.remember.contracts.models import MemorySnapshot, SourceRef
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    RunResult,
    TaskRecord,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.requests import text_hash

from .decay import initial
from .retention import hours


class SummaryPort(Protocol):
    async def select(
        self, ctx: TrustedContext, text: str, task_context: str, max_chars: int
    ) -> tuple[str, ...]:
        """Return bounded exact quotes. An LLM may select, but may not invent text."""
        ...


class ExtractiveSummary:
    """Offline provider: sentence selection, explicitly not a semantic quality claim."""

    async def select(
        self, ctx: TrustedContext, text: str, task_context: str, max_chars: int
    ) -> tuple[str, ...]:
        sentences = [s.strip() for s in re.split(r"(?<=[。！？.!?])\s*|[\r\n]+", text) if s.strip()]
        if not sentences:
            return ()
        query = set(task_context.lower()) - set(" \n\r，。的是否")
        ordered = sorted(
            enumerate(sentences), key=lambda p: (-len(query & set(p[1].lower())), p[0])
        )
        selected: list[tuple[int, str]] = []
        remaining = max_chars
        for index, sentence in ordered:
            quote = sentence[:remaining]
            if quote and quote not in [q for _, q in selected]:
                selected.append((index, quote))
                remaining -= len(quote)
            if remaining == 0:
                break
        return tuple(q for _, q in sorted(selected))


class WorkingSummaries:
    def __init__(self, owner: Any, provider: SummaryPort | None = None) -> None:
        self.owner = owner
        self.provider = provider or ExtractiveSummary()

    def needed(self, text: str, document: bool = False) -> bool:
        return document or len(text.encode("utf-8")) >= self.owner.policy.working_summary_min_bytes

    @staticmethod
    def descriptor(source: SourceRef, text: str, task_context: str = "") -> str:
        task = f"当前任务：{task_context}\n" if task_context else ""
        return (
            f"{task}已保存长内容；来源 {source.source_id}@{source.source_version}。\n"
            "摘要尚未生成；此记录不包含原文全文，精确细节请读取来源。\n"
            f"原文开头节选：{text[:256]}"
        )

    def register(self, tx: Any, item: MemorySnapshot, task_context: str = "") -> None:
        tx.write(
            "remember_working_summaries",
            item.ref.memory_id,
            {
                "memory": item.ref.model_dump(mode="json"),
                "source": item.sources[0].model_dump(mode="json"),
                "task_context": task_context,
                "state": "pending",
                "representation": "source_reference",
                "attempts": 0,
            },
        )

    async def process(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot
    ) -> RunResult:
        owner = self.owner
        with owner.uow.transaction() as tx:
            owner.tasks.guard(tx, task)
            state = tx.read("remember_working_summaries", item.ref.memory_id)
            if not state or state["memory"] != item.ref.model_dump(mode="json"):
                return RunResult(
                    outcome="obsolete",
                    effect_status=EffectStatus.NO_EFFECT,
                    reason="summary input version changed",
                )
            tx.write(
                "remember_working_summaries",
                item.ref.memory_id,
                {**state, "state": "running", "task_id": task.task_id},
            )
        source = SourceRef.model_validate(state["source"])
        quotes: list[dict[str, Any]] = []
        failure = None
        start = 0
        try:
            part_budget = owner.policy.extraction_chunk_tokens - owner.tokenizer.count(
                state["task_context"]
            )
            if part_budget < 16:
                raise ValueError("task context exceeds summary input budget")
            while True:
                # Source pages are independently hash verified; no full-body hydration.
                page = await owner.source_access.read(ctx, source, start, None)
                # Bound provider input independently from P2's read page size.
                from .policy import chunks

                for offset, _, text in chunks(page["content"], owner.tokenizer.count, part_budget):
                    checkpoint_key = fingerprint([task.task_id, start + offset, text_hash(text)])
                    with owner.uow.transaction() as tx:
                        checkpoint = tx.read("remember_summary_parts", checkpoint_key)
                    if checkpoint is None:
                        last_error = "summary_selection_invalid"
                        for _ in range(owner.policy.summary_attempts):
                            with owner.uow.transaction() as tx:
                                owner.tasks.guard(tx, task)
                                attempts = tx.read("remember_summary_attempts", checkpoint_key) or 0
                                if attempts >= owner.policy.summary_attempts:
                                    break
                                tx.write("remember_summary_attempts", checkpoint_key, attempts + 1)
                            owner.consume_call(task)
                            try:
                                async with asyncio.timeout(
                                    owner.policy.summary_call_timeout_seconds
                                ):
                                    selected = await self.provider.select(
                                        ctx,
                                        text,
                                        state["task_context"],
                                        owner.policy.summary_part_chars,
                                    )
                                if (
                                    not isinstance(selected, (tuple, list))
                                    or not selected
                                    and text.strip()
                                    or any(
                                        not isinstance(q, str) or not q.strip() or q not in text
                                        for q in selected
                                    )
                                    or sum(len(q) for q in selected)
                                    > owner.policy.summary_part_chars
                                ):
                                    raise ValueError("summary must contain bounded original quotes")
                                checkpoint = [
                                    {
                                        "quote": q,
                                        "start_char": start + offset + text.index(q),
                                        "end_char": start + offset + text.index(q) + len(q),
                                    }
                                    for q in selected
                                ]
                                with owner.uow.transaction() as tx:
                                    owner.tasks.guard(tx, task)
                                    tx.write("remember_summary_parts", checkpoint_key, checkpoint)
                                break
                            except FoundationError:
                                raise
                            except Exception as exc:
                                last_error = type(exc).__name__
                        if checkpoint is None:
                            raise ValueError(last_error)
                    quotes.extend(checkpoint)
                if page["next_start"] is None:
                    break
                start = page["next_start"]
        except (ValueError, OSError, TimeoutError) as exc:
            failure = type(exc).__name__
        except FoundationError as exc:
            if exc.code not in {ErrorCode.CONTRACT_VIOLATION, ErrorCode.DEPENDENCY_UNAVAILABLE}:
                raise
            failure = exc.code.value
        chosen: list[dict[str, Any]] = []
        remaining = owner.policy.working_summary_max_chars
        # Preserve source order; mark omissions explicitly. Do not manufacture prose.
        for quote in quotes:
            text = quote["quote"]
            if remaining <= 0:
                break
            if text in [q["quote"] for q in chosen]:
                continue
            text = text[:remaining]
            chosen.append({**quote, "quote": text, "end_char": quote["start_char"] + len(text)})
            remaining -= len(text)
        if not chosen and failure is None:
            failure = "no_supported_summary"
        content = item.content
        if failure is None:
            prefix = f"当前任务：{state['task_context']}\n" if state["task_context"] else ""
            content = (
                f"{prefix}来源 {source.source_id}@{source.source_version} 的节选式摘要：\n"
                + "\n".join(q["quote"] for q in chosen)
                + "\n此摘要不包含全部信息；数字、条款、代码及完整审查需核对原文。"
            )
            await owner.bodies.persist(ctx, item.ref.scope, content)
        with owner.uow.transaction() as tx:
            owner.tasks.guard(tx, task)
            if owner.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision != "allowed":
                latest = tx.read("remember_working_summaries", item.ref.memory_id)
                if latest and latest["memory"] == state["memory"]:
                    tx.write(
                        "remember_working_summaries",
                        item.ref.memory_id,
                        {**state, "state": "obsolete", "task_id": task.task_id},
                    )
                return RunResult(
                    outcome="obsolete",
                    effect_status=EffectStatus.NO_EFFECT,
                    reason="source or Working changed during summary",
                )
            owner.source_access.checked(tx, ctx, source)
            current = owner.current(tx, item.ref.memory_id)
            updated = current
            if failure is None:
                old = owner.change(
                    tx, current, status="superseded", projection_state="not_required"
                )
                owner.emit(tx, ctx, old, "projection_stale")
                updated = MemorySnapshot.model_validate(
                    {
                        **current.model_dump(),
                        "ref": {**current.ref.model_dump(), "version": current.ref.version + 1},
                        "revision": 1,
                        "object_revision": old.object_revision + 1,
                        "content": content,
                        "content_hash": text_hash(content),
                        "supersedes": current.ref,
                    }
                )
                owner.put(tx, updated)
                # Summarization is not user use: carry the prior decay anchor forward.
                retention = tx.read("remember_retention", owner.refkey(current.ref)) or initial(
                    "working", hours(current.created_at)
                )
                tx.write("remember_retention", owner.refkey(updated.ref), retention)
                enrollment = tx.read("remember_retention_enrollment", current.ref.memory_id)
                if enrollment and enrollment["version"] == current.ref.version:
                    tx.write(
                        "remember_retention_enrollment",
                        current.ref.memory_id,
                        {
                            **enrollment,
                            "version": updated.ref.version,
                            "object_revision": updated.object_revision,
                        },
                    )
                owner.emit(tx, ctx, updated, "corrected")
            summary_state = {
                **state,
                "memory": updated.ref.model_dump(mode="json"),
                "state": "failed" if failure else "ready",
                "reason": failure,
                "representation": "source_reference" if failure else "extractive_summary",
                "task_id": task.task_id,
                "provider_calls": tx.read("remember_model_calls", task.task_id) or 0,
                "evidence": chosen if not failure else [],
                "is_complete": False,
                "fallback": "extract_original" if failure else None,
            }
            tx.write("remember_working_summaries", item.ref.memory_id, summary_state)
            follow_ctx = ctx.model_copy(
                update={"operation_id": fingerprint([task.task_id, "followup"])}
            )
            pending = tx.read("remember_pending", item.ref.memory_id) or {}
            tx.write(
                "remember_pending",
                item.ref.memory_id,
                {
                    **pending,
                    "ref": updated.ref.model_dump(mode="json"),
                    "state": "pending",
                    "context": follow_ctx.model_dump(mode="json"),
                    "created_at": pending.get("created_at", current.created_at),
                    "tokens": pending.get("tokens", 0),
                },
            )
            tasks = []
            if (tx.read("remember_source_ranges", source.source_id) or {}).get("chars", 0):
                tasks.append(owner.enqueue(tx, follow_ctx, updated, "remember.compress"))
            tasks.extend(owner.schedule(tx, follow_ctx, updated.ref.scope, force=True))
            result = cast(
                RunResult,
                owner.finish(
                    tx,
                    ctx,
                    task,
                    {
                        "summary_state": summary_state["state"],
                        "reason": failure,
                        "working": updated.ref.model_dump(mode="json"),
                        "memories": [],
                        "task_ids": tasks,
                        "fallback": summary_state["fallback"],
                    },
                ),
            )
        # Cache is a best-effort replica, after the authoritative version commits.
        # Its TTL is supplied by the cache provider; publication does not depend on C.
        if failure is None:
            try:
                with owner.uow.transaction() as tx:
                    eligible = owner.final_guard(tx, ctx, (updated.ref,), "recall").items[0]
                if eligible.decision == "allowed":
                    await owner.bodies.admit(updated.ref.scope, updated.content)
                with owner.uow.transaction() as tx:
                    eligible = owner.final_guard(tx, ctx, (updated.ref,), "recall").items[0]
                valid = eligible.decision == "allowed"
            except FoundationError:
                valid = False
            if not valid and owner.bodies.cache:
                try:
                    await owner.bodies.cache.delete(updated.ref.scope, updated.content_hash)
                except Exception:
                    # Qualification already prevents stale reads; record cleanup uncertainty.
                    with owner.uow.transaction() as tx:
                        tx.write(
                            "remember_cache_admission",
                            updated.ref.memory_id,
                            {
                                "state": "cleanup_pending",
                                "bytes": len(updated.content.encode()),
                            },
                        )
        return result
