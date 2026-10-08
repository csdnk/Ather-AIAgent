"""Whole-body planning with B-owned relations and persisted commit expectations."""
# 完整正文组包与条件提交：候选/Working 读取 → 关系补全 → 正文核验 → 排序预算 → 保存计划。
# 计划仅记录待交付内容；commit 在 RF 事务内重新向 B 核验后才发布结果和 packed 事件。

from __future__ import annotations

import asyncio
import math
from typing import Any, cast

from aether_agent_memory.recall.contracts.foundation import (
    ContextAssemblyPlan,
    ContextCommitRequest,
    ContextPackUnit,
    MemorySearchResult,
    RankingEvidence,
    RecallPlanRequest,
)
from aether_agent_memory.recall.contracts.models import (
    ContextGroup,
    ContextItem,
    ContextPack,
    Coverage,
    RecallRecord,
    RecallRequest,
)
from aether_agent_memory.recall.contracts.ports import MemoryCandidatePort
from aether_agent_memory.remember.basic.passages import original_passages, render_passages
from aether_agent_memory.remember.contracts.foundation import (
    ContextGuardRequest,
    FullBodyReadResult,
    GuardStamp,
    MemoryRelationSnapshot,
    OriginalPassage,
    ProjectionManifest,
    ProjectionReadiness,
)
from aether_agent_memory.remember.contracts.models import (
    ConflictGroup,
    MemoryReadBatch,
    MemoryRef,
    MemorySnapshot,
)
from aether_agent_memory.remember.contracts.ports import (
    MemoryContextGuardPort,
    MemoryFoundationPort,
)
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Flow,
    Permission,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.requests import select_scope, text_hash
from aether_agent_memory.runtime.foundation.telemetry import observed
from aether_agent_memory.runtime.foundation.transactions import native
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .candidates import seconds_left
from .service import Recall


def stamp_equal(left: GuardStamp, right: GuardStamp) -> bool:
    # 忽略观察时间，比较内容、对象、关系和授权版本是否仍是同一份事实。
    return left.model_dump(exclude={"checked_at"}) == right.model_dump(exclude={"checked_at"})


@observed("recall.assembly")
class ContextAssembly:
    def __init__(
        self,
        base: Recall,
        candidates: MemoryCandidatePort,
        bodies: MemoryFoundationPort,
        guards: MemoryContextGuardPort,
    ) -> None:
        # A 负责排序组包，B 接口负责正文、关系与最终有效性；复用原 Recall 基础设施。
        self.base, self.candidates, self.bodies, self.guards = base, candidates, bodies, guards
        self.uow, self.identity = base.uow, base.identity

    def check(self, ctx: TrustedContext, request: RecallPlanRequest) -> None:
        # 每个关键阶段重新授权并检查期限，覆盖异步等待期间的撤权和超时。
        with self.uow.transaction() as tx:
            self.identity.authorize(
                tx,
                ctx,
                Permission.READ,
                RecordRef(
                    owner=Flow.RECALL,
                    object_type="recall",
                    object_id=request.recall_id,
                    scope=select_scope(ctx, request.selection),
                ),
            )
        if self.identity.clock() >= request.deadline_at:
            raise FoundationError(ErrorCode.DEADLINE_EXCEEDED, "assembly deadline expired")

    def revalidate(
        self,
        tx: MetadataTransaction,
        ctx: TrustedContext,
        expected: ContextGuardRequest,
        deadline: str,
    ) -> tuple[GuardStamp, ...]:
        # 必须使用调用方事务复核 B 当前事实；缺成员是契约错误，事实变化是结果失效。
        # 返回的是 B 当前凭据，A 不根据旧计划自行生成授权证明。
        self.identity.revalidate(tx, ctx)
        if self.identity.clock() >= deadline:
            tx.abort(ErrorCode.DEADLINE_EXCEEDED, "context guard expired")
        result = tuple(
            GuardStamp.model_validate_json(g.model_dump_json())
            for g in self.guards.revalidate_context(tx, ctx, expected)
        )
        actual = {g.memory.model_dump_json(): g for g in result}
        original = {g.memory.model_dump_json(): g for g in expected.expected}
        if len(actual) != len(result) or set(actual) != set(original):
            tx.abort(ErrorCode.CONTRACT_VIOLATION, "final guard coverage mismatch")
        for key, guard in actual.items():
            if not stamp_equal(original[key], guard):
                tx.abort(ErrorCode.RESULT_INVALIDATED, "context facts changed")
            if (
                guard.checked_at < original[key].checked_at
                or guard.checked_at > self.identity.clock()
                or guard.authorization_epoch != ctx.principal.auth_epoch
            ):
                tx.abort(ErrorCode.CONTRACT_VIOLATION, "invalid current guard")
        if self.identity.clock() >= deadline:
            tx.abort(ErrorCode.DEADLINE_EXCEEDED, "context guard arrived late")
        self.identity.revalidate(tx, ctx)
        return result

    def read_fact(self, ctx: TrustedContext, recall_id: str, ref: MemoryRef) -> None:
        # A repeated page/read does not recreate an envelope with a new timestamp.
        # 记录实际成功读取的业务事实；同一 Recall 和记忆引用只产生一次 read 事件。
        # 读过但最终未入包仍然是一次读取，不能随组包失败抹去该事实。
        access_key = fingerprint([recall_id, ref.model_dump(mode="json")])
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            if tx.read("outbox", fingerprint([access_key, "read"])) is None:
                self.base.access(tx, ctx, recall_id, ref, "read")

    async def plan(self, ctx: TrustedContext, request: RecallPlanRequest) -> ContextAssemblyPlan:
        # 校验组包策略和期限后生成可持久化计划；此时尚未发布最终 Recall 结果。
        request = RecallPlanRequest.model_validate_json(request.model_dump_json())
        await asyncio.to_thread(self.check, ctx, request)
        if (
            request.deadline_at > ctx.deadline_at
            or request.context_tokenizer != self.base.tokenizer.identifier
            or request.policy_version != self.base.policy_version
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "assembly policy binding mismatch")
        try:
            async with asyncio.timeout(seconds_left(request.deadline_at, self.identity.clock())):
                return await self.build(ctx, request)
        except TimeoutError as exc:
            raise FoundationError(ErrorCode.DEADLINE_EXCEEDED, "assembly timed out") from exc
        except ValueError as exc:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "invalid assembly provider result"
            ) from exc

    async def build(self, ctx: TrustedContext, request: RecallPlanRequest) -> ContextAssemblyPlan:
        discovered = await self.discover(ctx, request)
        return await self.assemble_discovered(ctx, request, discovered)

    def check_plan_policy(self, ctx: TrustedContext, request: RecallPlanRequest) -> None:
        self.check(ctx, request)
        if (
            request.deadline_at > ctx.deadline_at
            or request.context_tokenizer != self.base.tokenizer.identifier
            or request.policy_version != self.base.policy_version
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "assembly policy binding mismatch")

    async def discover(self, ctx: TrustedContext, request: RecallPlanRequest) -> dict[str, Any]:
        await asyncio.to_thread(self.check_plan_policy, ctx, request)
        return cast(dict[str, Any], await self._build_phase(ctx, request, None))

    async def assemble_discovered(
        self, ctx: TrustedContext, request: RecallPlanRequest, discovered: dict[str, Any]
    ) -> ContextAssemblyPlan:
        await asyncio.to_thread(self.check_plan_policy, ctx, request)
        return cast(ContextAssemblyPlan, await self._build_phase(ctx, request, discovered))

    async def _build_phase(
        self, ctx: TrustedContext, request: RecallPlanRequest, discovered: dict[str, Any] | None
    ) -> dict[str, Any] | ContextAssemblyPlan:
        # 按来源收集可信内容，再以整条记忆或完整冲突组为单位进行排序和预算选择。
        scope = select_scope(ctx, request.selection)
        snapshots: dict[str, MemorySnapshot] = {}
        conflicts: dict[str, ConflictGroup] = {}
        reasons: set[str] = set()
        excluded: set[str] = set()
        coverage = {"working": "not_requested", "long_term": "not_requested"}
        manifests: dict[str, ProjectionManifest] = {}
        # 分别保留候选与关系读取时的凭据，正文必须与这些观察到的修订一致。
        candidate_guards: dict[str, GuardStamp] = {}
        relation_guards: dict[str, GuardStamp] = {}
        source_ranks: dict[str, dict[str, int]] = {}
        # Retain qualified chunk identities across Temporal discover/assemble stages.
        matched_chunks: dict[str, list[int]] = {}

        def visible(ref: MemoryRef) -> bool:
            with self.uow.transaction() as tx:
                return self.identity.discoverable(
                    tx,
                    ctx,
                    RecordRef(
                        owner=Flow.REMEMBER,
                        object_type="memory",
                        object_id=ref.memory_id,
                        scope=ref.scope,
                    ),
                    request.selection,
                )

        def accept_batch(batch: MemoryReadBatch, requested: set[str] | None) -> list[str]:
            # 严格对齐 eligibility 和快照：allowed 必须有对应正文快照及正确修订。
            # 明确 excluded 是正常排除；unverifiable 则降低可交付覆盖结论。
            batch = MemoryReadBatch.model_validate_json(batch.model_dump_json())
            eligibility = {i.ref.model_dump_json(): i for i in batch.eligibility.items}
            if (
                len(eligibility) != len(batch.eligibility.items)
                or (requested is not None and set(eligibility) != requested)
                or batch.eligibility.authorization_epoch != ctx.principal.auth_epoch
            ):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "read eligibility mismatch")
            items = {i.ref.model_dump_json(): i for i in batch.items}
            excluded.update(
                key for key, verdict in eligibility.items() if verdict.decision == "excluded"
            )
            if len(items) != len(batch.items):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "duplicate read snapshot")
            if set(items) != {
                key for key, verdict in eligibility.items() if verdict.decision == "allowed"
            }:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "allowed snapshot missing")
            for key, item in items.items():
                verdict = eligibility.get(key)
                if (
                    not visible(item.ref)
                    or verdict is None
                    or verdict.decision != "allowed"
                    or verdict.checked_revision != item.object_revision
                    or text_hash(item.content) != item.content_hash
                    or (requested is not None and key not in requested)
                ):
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "untrusted read snapshot")
                if key in snapshots and snapshots[key] != item:
                    raise FoundationError(
                        ErrorCode.RESULT_INVALIDATED, "snapshot changed during read"
                    )
                snapshots[key] = item
                self.read_fact(ctx, request.recall_id, item.ref)
            if any(i.decision == "unverifiable" for i in eligibility.values()):
                reasons.add("qualification_unverifiable")
            for group in batch.conflicts:
                if not any(m.model_dump_json() in eligibility for m in group.members):
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "unrelated conflict group")
                if any(not visible(m) for m in group.members):
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "conflict scope mismatch")
                if group.group_id in conflicts and conflicts[group.group_id] != group:
                    raise FoundationError(
                        ErrorCode.RESULT_INVALIDATED, "conflict changed during read"
                    )
                conflicts[group.group_id] = group
            # 关系成员和关系修订必须来自同一 B 快照，避免旧关系配上新正文。
            if items:
                relation_view = self.guards.relations(ctx, tuple(i.ref for i in items.values()))
                relation_view = MemoryRelationSnapshot.model_validate_json(
                    relation_view.model_dump_json()
                )
                guards = {g.memory.model_dump_json(): g for g in relation_view.guards}
                if set(guards) != set(items):
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "relation coverage mismatch"
                    )
                reported = {g.group_id: g for g in batch.conflicts}
                current = {g.group_id: g for g in relation_view.conflicts}
                if reported != current:
                    raise FoundationError(ErrorCode.RESULT_INVALIDATED, "relation snapshot changed")
                for key, guard in guards.items():
                    if (
                        guard.authorization_epoch != ctx.principal.auth_epoch
                        or guard.checked_at > self.identity.clock()
                    ):
                        raise FoundationError(
                            ErrorCode.CONTRACT_VIOLATION, "invalid relation guard"
                        )
                    relation_guards[key] = guard
            return list(items)

        # 每个来源独立进行向量候选发现；待发布索引不能冒充穷尽后的正常空结果。
        if discovered is None:
            for source in request.sources:
                search = getattr(request, source + "_search")
                assert search is not None
                saved = (
                    snapshots.copy(),
                    conflicts.copy(),
                    relation_guards.copy(),
                    manifests.copy(),
                    candidate_guards.copy(),
                    matched_chunks.copy(),
                )
                source_ranks[source] = {}
                try:
                    readiness = await self.base.memories.projection_readiness(
                        ctx, request.selection, source
                    )
                    readiness = ProjectionReadiness.model_validate_json(readiness.model_dump_json())
                    if readiness.source != source:
                        raise FoundationError(
                            ErrorCode.CONTRACT_VIOLATION, "readiness source mismatch"
                        )
                    found = await self.candidates.search(ctx, search)
                    found = MemorySearchResult.model_validate_json(found.model_dump_json())
                    await asyncio.to_thread(self.check, ctx, request)
                    if found.request != search or found.scope != scope:
                        raise FoundationError(
                            ErrorCode.CONTRACT_VIOLATION, "candidate result mismatch"
                        )
                    coverage[source] = found.coverage
                    if found.coverage != "complete":
                        reasons.add(source + "_" + found.stop_reason)
                    if not readiness.complete:
                        coverage[source] = "partial" if found.candidates else "unavailable"
                        reasons.add(
                            source
                            + ("_index_failed" if readiness.failed_count else "_index_pending")
                        )
                    source_ranks[source] = {
                        c.memory.model_dump_json(): c.rank for c in found.candidates
                    }
                    for candidate in found.candidates:
                        key = candidate.memory.model_dump_json()
                        manifests[key], candidate_guards[key] = candidate.manifest, candidate.guard
                        matched_chunks[key] = [
                            h.chunk_index
                            for h in sorted(candidate.hits, key=lambda h: (-h.score, h.chunk_index))
                        ]
                    if found.candidates:
                        refs = tuple(c.memory for c in found.candidates)
                        batch = await asyncio.to_thread(self.base.memories.load, ctx, refs)
                        await asyncio.to_thread(
                            accept_batch, batch, {r.model_dump_json() for r in refs}
                        )
                except FoundationError as exc:
                    if exc.code != ErrorCode.DEPENDENCY_UNAVAILABLE:
                        raise
                    coverage[source] = "unavailable"
                    reasons.add(source + "_dependency")
                    source_ranks[source] = {}
                    (
                        snapshots,
                        conflicts,
                        relation_guards,
                        manifests,
                        candidate_guards,
                        matched_chunks,
                    ) = saved

            return {
                "snapshots": {k: v.model_dump(mode="json") for k, v in snapshots.items()},
                "conflicts": {k: v.model_dump(mode="json") for k, v in conflicts.items()},
                "reasons": sorted(reasons),
                "excluded": sorted(excluded),
                "coverage": coverage,
                "manifests": {k: v.model_dump(mode="json") for k, v in manifests.items()},
                "candidate_guards": {
                    k: v.model_dump(mode="json") for k, v in candidate_guards.items()
                },
                "relation_guards": {
                    k: v.model_dump(mode="json") for k, v in relation_guards.items()
                },
                "source_ranks": source_ranks,
                "matched_chunks": matched_chunks,
            }
        snapshots = {
            k: MemorySnapshot.model_validate(v) for k, v in discovered["snapshots"].items()
        }
        conflicts = {k: ConflictGroup.model_validate(v) for k, v in discovered["conflicts"].items()}
        reasons, excluded = set(discovered["reasons"]), set(discovered["excluded"])
        coverage, source_ranks = discovered["coverage"], discovered["source_ranks"]
        matched_chunks = discovered.get("matched_chunks", {})
        manifests = {
            k: ProjectionManifest.model_validate(v) for k, v in discovered["manifests"].items()
        }
        candidate_guards = {
            k: GuardStamp.model_validate(v) for k, v in discovered["candidate_guards"].items()
        }
        relation_guards = {
            k: GuardStamp.model_validate(v) for k, v in discovered["relation_guards"].items()
        }

        # 阶段三：补全冲突组成员；补读另设上限，不能让关系扩展突破工作预算。
        # Fetch relation members under B control, bounded independently from primary K.
        queried = set(snapshots)
        for _ in range(10):
            missing = {
                m.model_dump_json(): m
                for g in conflicts.values()
                for m in g.members
                if m.model_dump_json() not in queried
            }
            if not missing:
                break
            if len(queried) + len(missing) > 1000:
                reasons.add("relation_limit")
                break
            queried.update(missing)
            saved_relation = (snapshots.copy(), conflicts.copy(), relation_guards.copy())
            try:
                batch = await asyncio.to_thread(
                    self.base.memories.load, ctx, tuple(missing.values())
                )
                await asyncio.to_thread(accept_batch, batch, set(missing))
            except FoundationError as exc:
                if exc.code != ErrorCode.DEPENDENCY_UNAVAILABLE:
                    raise
                snapshots, conflicts, relation_guards = saved_relation
                reasons.add("relation_dependency")
                break
            await asyncio.to_thread(self.check, ctx, request)
            await asyncio.sleep(0)
        else:
            reasons.add("relation_limit")

        # 阶段四：按记忆引用读取完整正文，不把向量命中的块文本当成最终正文。
        refs = tuple(s.ref for s in snapshots.values())
        bodies: dict[str, FullBodyReadResult] = {}
        if refs:
            response = await self.bodies.load_bodies(ctx, refs)
            await asyncio.to_thread(self.check, ctx, request)
            results = tuple(
                FullBodyReadResult.model_validate_json(r.model_dump_json()) for r in response
            )
            all_bodies = {r.memory.model_dump_json(): r for r in results}
            if len(all_bodies) != len(results) or set(all_bodies) != set(snapshots):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "body response mismatch")
            for key, body in all_bodies.items():
                if body.outcome == "excluded":
                    excluded.add(key)
                    continue
                if body.outcome != "read":
                    reasons.add("body_" + body.outcome)
                    continue
                assert body.guard is not None
                # 正文摘要和修订要同时匹配候选、元数据和关系凭据；任一变更都不能继续使用。
                before = candidate_guards.get(key)
                snapshot = snapshots[key]
                if (
                    body.guard.authorization_epoch != ctx.principal.auth_epoch
                    or body.guard.checked_at > self.identity.clock()
                ):
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid body guard")
                if (
                    body.guard.body_hash != snapshot.content_hash
                    or body.guard.object_revision != snapshot.object_revision
                    or not stamp_equal(relation_guards[key], body.guard)
                    or body.guard.checked_at < relation_guards[key].checked_at
                    or (
                        before is not None
                        and (
                            not stamp_equal(before, body.guard)
                            or body.guard.checked_at < before.checked_at
                        )
                    )
                ):
                    reasons.add("body_stale")
                    continue
                bodies[key] = body
                await asyncio.to_thread(self.read_fact, ctx, request.recall_id, body.memory)

        # 阶段五：先建立成员到冲突组的映射，再按完整组形成不可拆分的入包单元。
        by_member: dict[str, list[ConflictGroup]] = {}
        for relation in conflicts.values():
            for member in relation.members:
                by_member.setdefault(member.model_dump_json(), []).append(relation)
        units: list[ContextPackUnit] = []
        skipped: set[str] = set()
        done: set[str] = set()
        scores: dict[str, float] = {}
        # 单来源保留向量名次；多来源按记忆级 RRF 融合，每来源只贡献一次。
        if len(request.sources) == 1:
            scores = {key: -float(rank) for key, rank in source_ranks[request.sources[0]].items()}
        else:
            for ranks in source_ranks.values():
                for key, rank in ranks.items():
                    scores[key] = scores.get(key, 0) + 1 / (60 + rank)
        for key in sorted(scores, key=lambda k: (-scores[k], k)):
            groups = by_member.get(key, [])
            group = groups[0] if groups else None
            group_id = group.group_id if group else fingerprint([request.recall_id, key])
            if group_id in done:
                continue
            done.add(group_id)
            members = [m.model_dump_json() for m in group.members] if group else [key]
            if any(len(by_member.get(m, [])) > 1 for m in members):
                skipped.add(group_id)
                reasons.add("overlapping_conflicts")
                continue
            # 缺一个可信成员就跳过整组，避免只展示冲突的一方；独立组仍可继续入包。
            if any(m not in bodies for m in members):
                skipped.add(group_id)
                if any(m not in bodies and m not in excluded for m in members):
                    reasons.add("incomplete_group")
                continue
            # A single logical memory cannot appear through different versions/groups.
            logical = {
                (b.memory.scope.model_dump_json(), b.memory.memory_id)
                for u in units
                for b in u.bodies
            }
            unit_keys = [
                (bodies[m].memory.scope.model_dump_json(), bodies[m].memory.memory_id)
                for m in members
            ]
            if len(set(unit_keys)) != len(unit_keys) or logical.intersection(unit_keys):
                raise FoundationError(ErrorCode.RESULT_INVALIDATED, "multiple memory versions")
            passages: tuple[OriginalPassage, ...] = ()
            manifest = manifests.get(key)
            if (
                group is None
                and snapshots[key].kind == "working"
                and manifest is not None
                and manifest.expected_chunk_count > 1
                and key in matched_chunks
            ):
                descriptors = {c.chunk_index: c for c in manifest.chunks}
                passages = original_passages(
                    bodies[key].content or "",
                    manifest.body_hash,
                    tuple(descriptors[i] for i in matched_chunks[key]),
                )
            units.append(
                ContextPackUnit(
                    group_id=group_id,
                    bodies=tuple(bodies[m] for m in members),
                    primary_memories=tuple(bodies[m].memory for m in members if m in manifests),
                    conflict=group,
                    rank=len(units) + 1,
                    passages=passages,
                )
            )

        # 显式启用重排序时，必须在正文交给重排序器之前复核资格，防止越权泄露。
        if units and self.base.settings.rerank_policy != "disabled":
            expected = self.expectations(units, manifests)

            def validate_rerank() -> None:
                with self.uow.transaction() as tx:
                    self.revalidate(tx, ctx, expected, request.deadline_at)

            await asyncio.to_thread(validate_rerank)
            if self.base.reranker is None:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "missing reranker")
            try:
                async with asyncio.timeout(self.base.settings.rerank_timeout_seconds):
                    ranked = await self.base.reranker.rerank(
                        ctx,
                        request.query,
                        tuple(self.unit_content(u) for u in units),
                    )
                await asyncio.to_thread(self.check, ctx, request)
                if len(ranked) != len(units) or not all(math.isfinite(s) for s in ranked):
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid rerank scores")
                units = [
                    u
                    for _, u in sorted(
                        zip(ranked, units, strict=True),
                        key=lambda pair: (-pair[0], pair[1].group_id),
                    )
                ]
            except (TimeoutError, FoundationError) as exc:
                code = exc.code if isinstance(exc, FoundationError) else ErrorCode.DEADLINE_EXCEEDED
                if self.base.settings.rerank_policy != "fallback" or code not in {
                    ErrorCode.DEADLINE_EXCEEDED,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                }:
                    raise
                reasons.add("rerank_" + code.value)

        # 阶段六：预算计算使用实际渲染串（含编号、换行）；整组放不下就跳过，不截断正文。
        selected: list[ContextPackUnit] = []
        rendered = ""
        for unit in units:
            if unit.passages:
                # Budget remains A-owned. Select whole matching chunks in vector-score
                # order; never truncate a chunk or send an entire long file by accident.
                admitted: list[OriginalPassage] = []
                for passage in unit.passages:
                    selected_passages = tuple([*admitted, passage])
                    fragment = f"[{len(selected) + 1}] " + render_passages(selected_passages) + "\n"
                    if self.base.tokenizer.count(rendered + fragment) <= request.token_budget:
                        admitted.append(passage)
                if not admitted:
                    skipped.add(unit.group_id)
                    continue
                unit = unit.model_copy(update={"passages": tuple(admitted)})
            fragment = f"[{len(selected) + 1}] " + self.unit_content(unit) + "\n"
            if (
                len(selected) >= self.base.settings.max_items
                or self.base.tokenizer.count(rendered + fragment) > request.token_budget
            ):
                skipped.add(unit.group_id)
                continue
            rendered += fragment
            selected.append(unit.model_copy(update={"rank": len(selected) + 1}))
        if units and not selected:
            raise FoundationError(ErrorCode.BUDGET_TOO_SMALL, "no complete group fits budget")
        # 明确排除全部内容可返回正常空结果；无法核验导致的无内容必须报告依赖失败。
        if not selected and reasons:
            pending_only = all(reason.endswith("_index_pending") for reason in reasons)
            raise FoundationError(
                ErrorCode.REQUEST_IN_PROGRESS if pending_only else ErrorCode.DEPENDENCY_UNAVAILABLE,
                "no verified group available: " + ",".join(sorted(reasons)),
            )
        evidence = (
            tuple(
                RankingEvidence(
                    memory=b.memory,
                    source=source,
                    source_rank=ranks[key],
                    rrf_k=60,
                    rrf_contribution=1 / (60 + ranks[key]),
                )
                for unit in selected
                for b in unit.bodies
                for source, ranks in source_ranks.items()
                if (key := b.memory.model_dump_json()) in ranks
            )
            if len(request.sources) > 1
            else ()
        )
        plan = ContextAssemblyPlan(
            request=request,
            scope=scope,
            units=tuple(selected),
            skipped_group_ids=tuple(sorted(skipped)),
            rendered_context=rendered,
            tokens_used=self.base.tokenizer.count(rendered),
            rank_evidence=evidence,
            degradation_reasons=tuple(sorted(reasons)),
        )

        def persist_plan() -> None:
            self.check(ctx, request)
            with self.uow.transaction() as tx:
                self.identity.revalidate(tx, ctx)
                prior = tx.read("recall_assembly", request.recall_id)
                if prior is not None:
                    tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "assembly ID already bound")
                # 保存计划、原身份、B 凭据和内容签名，供 commit 防篡改及结果重取时使用。
                tx.write(
                    "recall_assembly",
                    request.recall_id,
                    {
                        "plan": plan.model_dump(mode="json"),
                        "context": ctx.model_dump(mode="json"),
                        "expectations": self.expectations(selected, manifests).model_dump(
                            mode="json"
                        ),
                        "coverage": coverage,
                        "signature": fingerprint(plan.model_dump(mode="json")),
                    },
                )
                tx.before_commit.append(lambda: self.identity.revalidate(tx, ctx))

        await asyncio.to_thread(persist_plan)
        return plan

    @staticmethod
    def unit_content(unit: ContextPackUnit) -> str:
        if unit.passages:
            return render_passages(unit.passages)
        return "\n".join(b.content or "" for b in unit.bodies)

    @staticmethod
    def expectations(
        units: list[ContextPackUnit], manifests: dict[str, ProjectionManifest]
    ) -> ContextGuardRequest:
        # 所有来源主候选都附带发布清单；关系补读成员不冒充主候选。
        return ContextGuardRequest(
            expected=tuple(b.guard for u in units for b in u.bodies if b.guard is not None),
            manifests=tuple(
                manifests[key]
                for u in units
                for b in u.bodies
                if (key := b.memory.model_dump_json()) in manifests
            ),
        )

    def commit(
        self, tx: Transaction, ctx: TrustedContext, request: ContextCommitRequest
    ) -> RecordRef:
        # 在调用方 RF 事务中验证计划归属、原请求和当前凭据，再原子写入结果与事件。
        # 重复提交先重新复核有效性；同一已提交计划不会重复写 packed 事件。
        sql = native(tx)
        request = ContextCommitRequest.model_validate_json(request.model_dump_json())
        plan = request.plan
        saved = sql.read("recall_assembly", plan.request.recall_id)
        row = sql.read("recall_requests", plan.request.recall_id)
        if saved is None or row is None:
            sql.abort(ErrorCode.NOT_FOUND, "no persisted recall plan")
        if (
            saved["signature"] != fingerprint(plan.model_dump(mode="json"))
            or saved["context"]["principal"] != ctx.principal.model_dump(mode="json")
            or saved["context"]["operation_id"] != ctx.operation_id
            or request.operation_id != ctx.operation_id
        ):
            sql.abort(ErrorCode.CONTRACT_VIOLATION, "plan does not belong to this operation")
        record = RecallRecord.model_validate(row["record"])
        self.identity.authorize(sql, ctx, Permission.READ, self.base.ref(record))
        # 不能只验证计划自洽：还要绑定 RF 最初接收的查询、范围、来源、预算与期限。
        if row.get("request") is None:
            sql.abort(ErrorCode.CONTRACT_VIOLATION, "missing original recall request")
        original = RecallRequest.model_validate(row["request"])
        selected = (
            ("working", "long_term")
            if original.sources == "both"
            or (original.sources == "auto" and (record.scope.session_id or record.scope.task_id))
            else ("long_term",)
            if original.sources == "auto"
            else (original.sources,)
        )
        if (
            row["signature"] != fingerprint(original.model_dump(mode="json"))
            or row["context"]["principal"] != ctx.principal.model_dump(mode="json")
            or row["context"]["operation_id"] != ctx.operation_id
            or original.query != plan.request.query
            or original.selection != plan.request.selection
            or original.token_budget != plan.request.token_budget
            or selected != plan.request.sources
            or record.scope != plan.scope
            or record.deadline_at != plan.request.deadline_at
        ):
            sql.abort(ErrorCode.CONTRACT_VIOLATION, "plan mismatches original RF request")
        expected = ContextGuardRequest.model_validate(saved["expectations"])
        current = self.revalidate(sql, ctx, expected, plan.request.deadline_at)
        # 请求附带的 final_guards 只是预期值；真正的当前权限证明来自事务内 B 复核。
        # Supplied final_guards are never trusted as authority; B is called inside tx.
        ContextCommitRequest.model_validate({**request.model_dump(), "final_guards": current})
        if record.state == "completed":
            if row.get("assembly_signature") != saved["signature"]:
                sql.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "different committed plan")
            return self.base.ref(record)
        if record.revision != request.expected_recall_revision or record.state != "running":
            sql.abort(ErrorCode.VERSION_CONFLICT, "recall state changed before commit")
        pack = ContextPack(
            recall_id=record.recall_id,
            scope=plan.scope,
            outcome="degraded"
            if plan.units and plan.degradation_reasons
            else "available"
            if plan.units
            else "empty",
            selected_sources=plan.request.sources,
            coverage=Coverage.model_validate(saved["coverage"]),
            groups=tuple(
                ContextGroup(
                    group_id=u.group_id,
                    conflict=u.conflict,
                    items=tuple(
                        ContextItem(
                            memory=b.memory,
                            content=self.unit_content(u) if u.passages else b.content or "",
                            sources=b.sources,
                            representation="original_passages" if u.passages else "original",
                            passages=u.passages,
                        )
                        for b in u.bodies
                    ),
                )
                for u in plan.units
            ),
            rendered_context=plan.rendered_context,
            token_budget=plan.request.token_budget,
            tokens_used=plan.tokens_used,
            tokenizer_id=plan.request.context_tokenizer,
            policy_version=plan.request.policy_version,
            degradation_reasons=plan.degradation_reasons,
            committed_at=self.identity.clock(),
        )
        # 最终结果与下面的 packed 事件共享同一事务，禁止只提交其中一部分。
        sql.write(
            "recall_requests",
            record.recall_id,
            {
                **row,
                "record": record.model_copy(
                    update={
                        "state": "completed",
                        "stage": "finalize",
                        "revision": record.revision + 1,
                        "result_available": True,
                    }
                ).model_dump(mode="json"),
                "pack": pack.model_dump(mode="json"),
                "assembly_signature": saved["signature"],
            },
        )
        for unit in plan.units:
            for body in unit.bodies:
                self.base.access(sql, ctx, record.recall_id, body.memory, "packed")

        # RF hooks may run before this hook; recheck B after all business writes as well.
        def final_check() -> None:
            # 事务真正提交前再次检查 B；失败时本事务结果和 packed 事件一起回滚。
            self.revalidate(sql, ctx, expected, plan.request.deadline_at)

        sql.before_commit.append(final_check)
        return self.base.ref(record)
