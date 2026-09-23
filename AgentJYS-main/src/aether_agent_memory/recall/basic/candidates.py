"""A-owned bounded chunk discovery. B is the sole qualification authority."""
# 候选发现：Query 编码 → 分页检索块 → B 审查资格 → 按记忆去重取 Top K。
# A 只使用 B 返回的资格凭据；本阶段不读取正文，也不记录访问热度。

from __future__ import annotations

import asyncio
import math
from datetime import datetime

from aether_agent_memory.recall.contracts.foundation import (
    ChunkHit,
    ChunkSearchRequest,
    ChunkSearchResult,
    MemoryCandidate,
    MemorySearchRequest,
    MemorySearchResult,
)
from aether_agent_memory.recall.contracts.models import EmbeddingRequest
from aether_agent_memory.recall.contracts.ports import EmbeddingPort, GenerationSearchPort
from aether_agent_memory.recall.embedding.spaces import EmbeddingSpaces, validate_embedding
from aether_agent_memory.remember.contracts.foundation import (
    CandidateQualificationResult,
    CandidateQualificationTarget,
)
from aether_agent_memory.remember.contracts.ports import MemoryQualificationPort
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Flow,
    Permission,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.requests import matches, select_scope
from aether_agent_memory.runtime.foundation.storage import SQLiteUnitOfWork
from aether_agent_memory.runtime.foundation.telemetry import observed


def seconds_left(deadline: str, clock: str) -> float:
    # 将绝对期限转换为剩余秒数；已过期返回 0，同步提供方的期限仍由显式检查保证。
    return max(
        0, (datetime.fromisoformat(deadline) - datetime.fromisoformat(clock)).total_seconds()
    )


def target(hit: ChunkHit) -> CandidateQualificationTarget:
    # 仅把块的身份、版本和摘要交给 B；相似度分数不能成为授权依据。
    return CandidateQualificationTarget.model_validate(
        hit.model_dump(include=set(CandidateQualificationTarget.model_fields))
    )


def logical_key(hit: ChunkHit) -> tuple[str, str]:
    # 用作用域和 memory_id 识别同一条记忆，避免多个块或版本重复占位。
    return hit.memory.scope.model_dump_json(), hit.memory.memory_id


@observed("recall.candidates")
class MemoryCandidates:
    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        embedding: EmbeddingPort,
        vectors: GenerationSearchPort,
        qualification: MemoryQualificationPort,
        spaces: EmbeddingSpaces,
    ) -> None:
        # 注入编码、块检索和 B 资格接口，便于独立装配与契约测试。
        self.uow, self.identity = uow, identity
        self.embedding, self.vectors, self.qualification = embedding, vectors, qualification
        self.spaces = spaces

    async def qualify(
        self,
        ctx: TrustedContext,
        hits: tuple[ChunkHit, ...],
        request: MemorySearchRequest,
    ) -> dict[str, CandidateQualificationResult]:
        # 把 B 响应按完整目标键对齐；漏项、重复、空间或授权凭据错配都属于契约错误。
        targets = tuple(target(h) for h in hits)
        response = await self.qualification.qualify(ctx, targets, request.purpose)
        self.check_deadline(request.deadline_at)
        values = tuple(
            CandidateQualificationResult.model_validate_json(r.model_dump_json()) for r in response
        )
        result = {r.target.model_dump_json(): r for r in values}
        if len(result) != len(values) or set(result) != {t.model_dump_json() for t in targets}:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "qualification response mismatch")
        for value in values:
            space = self.spaces.resolve(request.model_space)
            if value.manifest is not None and (
                value.manifest.dimensions != space.dimensions
                or value.manifest.embedding_tokenizer != space.tokenizer_id
            ):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "manifest space mismatch")
            if value.guard is not None and (
                value.guard.authorization_epoch != ctx.principal.auth_epoch
                or value.guard.checked_at > self.identity.clock()
                or value.guard.checked_at > request.deadline_at
            ):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid qualification stamp")
        return result

    def check_deadline(self, deadline: str) -> None:
        # 有些 async 提供方内部同步执行，不会及时让出事件循环，必须主动检查期限。
        if self.identity.clock() >= deadline:
            raise FoundationError(ErrorCode.DEADLINE_EXCEEDED, "search deadline expired")

    async def search(self, ctx: TrustedContext, request: MemorySearchRequest) -> MemorySearchResult:
        # 返回候选及覆盖程度，不把依赖故障、预算耗尽或无法核验伪装成完整检索。
        request = MemorySearchRequest.model_validate_json(request.model_dump_json())
        scope = select_scope(ctx, request.selection)
        if request.deadline_at > ctx.deadline_at:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "search deadline exceeds context")
        with self.uow.transaction() as tx:
            self.identity.authorize(
                tx,
                ctx,
                Permission.READ,
                RecordRef(
                    owner=Flow.RECALL,
                    object_type="search",
                    object_id=request.operation_id,
                    scope=scope,
                ),
            )
        space = self.spaces.resolve(request.model_space)
        examined = rounds = 0
        candidates: list[MemoryCandidate] = []
        # 跨页保存已见块及其资格，避免重复审查，并发现同一块的分数漂移。
        seen: dict[str, ChunkHit] = {}
        evidence: dict[str, CandidateQualificationResult] = {}
        # 同一记忆的证据冲突最多重验一次；仍冲突的记忆在本次搜索中永久排除。
        retried: set[tuple[str, str]] = set()
        blocked: set[tuple[str, str]] = set()
        incomplete = False
        stop = "exhausted"
        cursor: str | None = None
        cursors: set[str] = set()
        last_score: float | None = None
        try:
            async with asyncio.timeout(seconds_left(request.deadline_at, self.identity.clock())):
                encoding = EmbeddingRequest(
                    operation_id=request.operation_id,
                    usage="query",
                    texts=(request.query,),
                    model_space=request.model_space,
                    deadline_at=request.deadline_at,
                )
                encoded = await self.embedding.embed(ctx, encoding)
                self.check_deadline(request.deadline_at)
                validate_embedding(encoding, encoded, space)
                # 分页同时受轮数、累计命中数和总期限约束，防止补取失控。
                while rounds < request.max_rounds and examined < request.max_chunk_hits:
                    page_request = ChunkSearchRequest(
                        operation_id=request.operation_id,
                        selection=request.selection,
                        model_space=space,
                        vector=encoded.items[0].vector,
                        limit=min(100, request.chunk_page_size, request.max_chunk_hits - examined),
                        cursor=cursor,
                        deadline_at=request.deadline_at,
                    )
                    page = await self.vectors.search(ctx, page_request)
                    self.check_deadline(request.deadline_at)
                    page = ChunkSearchResult.model_validate_json(page.model_dump_json())
                    rounds += 1
                    if page.request != page_request:
                        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "search page mismatch")
                    if any(
                        not matches(h.memory.scope, scope) or not math.isfinite(h.score)
                        for h in page.hits
                    ):
                        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "unsafe chunk hit")
                    if [h.score for h in page.hits] != sorted(
                        (h.score for h in page.hits), reverse=True
                    ):
                        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "unordered chunk page")
                    # 工作预算按实际收到的命中数计费，即使重复块也已经消耗检索工作。
                    examined += len(page.hits)
                    if page.hits:
                        if last_score is not None and page.hits[0].score > last_score:
                            raise FoundationError(
                                ErrorCode.CONTRACT_VIOLATION, "page order changed"
                            )
                        last_score = page.hits[-1].score
                    new: dict[str, ChunkHit] = {}
                    for hit in page.hits:
                        key = target(hit).model_dump_json()
                        previous = seen.get(key) or new.get(key)
                        if previous is not None and previous.score != hit.score:
                            raise FoundationError(
                                ErrorCode.CONTRACT_VIOLATION, "chunk score changed"
                            )
                        if key not in seen:
                            new[key] = hit
                    if new:
                        seen.update(new)
                        evidence.update(await self.qualify(ctx, tuple(new.values()), request))
                    incomplete |= page.coverage != "complete"
                    groups: dict[tuple[str, str], list[ChunkHit]] = {}
                    for key, hit in seen.items():
                        # 只有合格块参与分组和评分；被排除块的高分不得抬高记忆排名。
                        if evidence[key].decision == "allowed":
                            groups.setdefault(logical_key(hit), []).append(hit)
                        elif evidence[key].decision == "unverifiable":
                            incomplete = True
                    candidates = []
                    for group_key, hits in groups.items():
                        if group_key in blocked:
                            continue
                        stamps = {self.binding(evidence[target(h).model_dump_json()]) for h in hits}
                        if len(stamps) > 1:
                            if group_key in retried or rounds >= request.max_rounds:
                                blocked.add(group_key)
                                incomplete = True
                                continue
                            retried.add(group_key)
                            # 资格重验也计入轮数预算，不能在分页预算之外无限调用 B。
                            rounds += 1
                            fresh = await self.qualify(ctx, tuple(hits), request)
                            evidence.update(fresh)
                            hits = [
                                h
                                for h in hits
                                if fresh[target(h).model_dump_json()].decision == "allowed"
                            ]
                            incomplete |= any(r.decision == "unverifiable" for r in fresh.values())
                            stamps = {
                                self.binding(fresh[target(h).model_dump_json()]) for h in hits
                            }
                            if len(stamps) > 1:
                                blocked.add(group_key)
                                incomplete = True
                                continue
                        if hits:
                            proof = evidence[target(hits[0]).model_dump_json()]
                            assert proof.manifest is not None and proof.guard is not None
                            candidates.append(
                                MemoryCandidate(
                                    memory=hits[0].memory,
                                    manifest=proof.manifest,
                                    guard=proof.guard,
                                    hits=tuple(sorted(hits, key=lambda h: h.chunk_index)),
                                    best_score=max(h.score for h in hits),
                                    rank=1,
                                )
                            )
                    # 分数相同时使用完整记忆引用稳定排序，保证翻页和复验结果可重现。
                    candidates.sort(key=lambda c: (-c.best_score, c.memory.model_dump_json()))
                    candidates = candidates[: request.memory_top_k]
                    # 检索接口已保证全局降序；当前合格记忆满 K 后即可停止补取。
                    if len(candidates) == request.memory_top_k:
                        stop = "top_k"
                        break
                    if page.coverage == "unavailable":
                        stop = "dependency"
                        break
                    if page.next_cursor is None:
                        stop = "exhausted"
                        break
                    if page.next_cursor in cursors or (not new and page.hits):
                        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "repeating chunk page")
                    cursors.add(page.next_cursor)
                    cursor = page.next_cursor
                else:
                    stop = "hit_limit" if examined >= request.max_chunk_hits else "round_limit"
        except TimeoutError:
            stop, incomplete = "deadline", True
        except ValueError as exc:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "invalid search provider result"
            ) from exc
        except FoundationError as exc:
            if exc.code not in {ErrorCode.DEPENDENCY_UNAVAILABLE, ErrorCode.DEADLINE_EXCEEDED}:
                raise
            stop, incomplete = (
                ("deadline" if exc.code == ErrorCode.DEADLINE_EXCEEDED else "dependency"),
                True,
            )
        # 返回前再查身份：撤权必须失败，不能降级成部分结果或空结果。
        # Revocation is never downgraded to a partial/empty result.
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
        if self.identity.clock() >= request.deadline_at:
            stop, incomplete = "deadline", True
        return MemorySearchResult(
            request=request,
            scope=scope,
            candidates=tuple(
                c.model_copy(update={"rank": i + 1}) for i, c in enumerate(candidates)
            ),
            examined_chunk_hits=examined,
            rounds_used=rounds,
            stop_reason=stop,
            coverage=(
                "unavailable"
                if not candidates and stop in {"dependency", "deadline"}
                else "partial"
                if incomplete or stop in {"hit_limit", "round_limit"}
                else "complete"
            ),
        )

    @staticmethod
    def binding(value: CandidateQualificationResult) -> str:
        # 比较发布清单和权限/内容/关系修订；单纯复核时间变化不算证据冲突。
        assert value.manifest is not None and value.guard is not None
        # Different observation times alone do not imply different authority revisions.
        return value.manifest.model_dump_json() + str(
            value.guard.model_dump(exclude={"checked_at"})
        )
