"""Recall admission with real assembly; external stores and B ports are test doubles."""

import copy
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.basic.generation import GenerationRecall
from aether_agent_memory.recall.basic.service import Recall
from aether_agent_memory.recall.contracts.foundation import (
    ContextCommitRequest,
    MemoryCandidate,
    MemorySearchResult,
)
from aether_agent_memory.recall.contracts.models import ContextPack, RecallRecord, RecallRequest
from aether_agent_memory.remember.contracts.foundation import (
    FullBodyReadResult,
    MemoryRelationSnapshot,
    ProjectionReadiness,
)
from aether_agent_memory.remember.contracts.models import (
    ConflictGroup,
    EligibilityBatch,
    EligibilityResult,
    MemoryReadBatch,
    MemorySnapshot,
)
from aether_agent_memory.runtime.contracts.models import ErrorCode, ScopeSelector, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later, now
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.foundation.transactions import StorageTransaction
from aether_agent_memory.runtime.temporal.recall import recall_binding
from recall_records import InMemoryRecallRecords


def example(name):
    cases = json.loads(
        (Path(__file__).resolve().parents[3] / "contracts/p3/fixtures/cases.json").read_text(
            "utf-8"
        )
    )
    return copy.deepcopy(next(c["payload"] for c in cases if c["model"] == name and c["valid"]))


@pytest.fixture
def make_recall():
    def build(
        settings=None,
        entries=(("long_term", "high", (0.95,)), ("long_term", "low", (0.79,))),
        sources="long_term",
    ):
        clock = now()
        context = example("runtime.TrustedContext")
        context["principal"]["home_scope"]["session_id"] = "session"
        context["deadline_at"] = later(clock, 60)
        ctx = TrustedContext.model_validate(context)
        rows = {}
        tx = Mock()
        tx.read.side_effect = lambda table, key: copy.deepcopy(rows.get((table, key)))
        tx.write.side_effect = lambda table, key, value: rows.__setitem__(
            (table, key), copy.deepcopy(value)
        )
        tx.before_commit = []

        def abort(code, message):
            raise FoundationError(code, message)

        tx.abort.side_effect = abort
        uow = SimpleNamespace(transaction=lambda: nullcontext(tx), telemetry=None)
        identity = Mock()
        identity.clock.return_value = clock
        identity.discoverable.return_value = True
        events = Mock()
        events.append.side_effect = lambda tx, ctx, event: tx.write(
            "outbox", event.event_id, {"event": event.model_dump(mode="json")}
        )
        candidates, snapshots, bodies = {}, {}, {}
        for source, memory_id, scores in entries:
            candidate = example("recall.MemoryCandidate")
            ref = {
                "memory_id": memory_id,
                "version": 1,
                "scope": ctx.principal.home_scope.model_dump(),
            }
            candidate["memory"] = ref
            candidate["guard"].update(memory=ref, checked_at=clock)
            manifest = candidate["manifest"]
            manifest.update(memory=ref, expected_chunk_count=len(scores))
            content = example("remember.FullBodyReadResult")["content"]
            chunks, hits = [], []
            for index, score in enumerate(scores):
                start, end = (
                    len(content) * index // len(scores),
                    len(content) * (index + 1) // len(scores),
                )
                vector_id = fingerprint([memory_id, index])
                digest = text_hash(content[start:end])
                chunks.append(
                    {
                        **manifest["chunks"][0],
                        "chunk_index": index,
                        "start_char": start,
                        "end_char": end,
                        "vector_id": vector_id,
                        "input_hash": digest,
                    }
                )
                hits.append(
                    {
                        **candidate["hits"][0],
                        "memory": ref,
                        "memory_source": source,
                        "chunk_index": index,
                        "vector_id": vector_id,
                        "input_hash": digest,
                        "score": score,
                        "rank": index + 1,
                    }
                )
            manifest["chunks"] = chunks
            candidate.update(hits=hits, best_score=max(scores))
            candidates[memory_id] = MemoryCandidate.model_validate(candidate)
            snapshot = example("remember.MemorySnapshot")
            snapshot.update(
                ref=ref,
                kind="working" if source == "working" else "semantic",
                content=content,
                content_hash=manifest["body_hash"],
                projection_state="ready",
                model_space=manifest["model_space"],
            )
            snapshots[memory_id] = MemorySnapshot.model_validate(snapshot)
            body = example("remember.FullBodyReadResult")
            body.update(memory=ref, guard=candidate["guard"])
            bodies[memory_id] = FullBodyReadResult.model_validate(body)

        conflicts = []
        authority = Mock()

        def load(ctx, refs):
            return MemoryReadBatch(
                items=tuple(snapshots[r.memory_id] for r in refs),
                eligibility=EligibilityBatch(
                    authorization_epoch=ctx.principal.auth_epoch,
                    items=tuple(
                        EligibilityResult(
                            ref=r, decision="allowed", reason="test", checked_revision=1
                        )
                        for r in refs
                    ),
                ),
                conflicts=tuple(g for g in conflicts if any(r in g.members for r in refs)),
            )

        authority.load.side_effect = AssertionError("Recall must use address-aware batch reads")
        authority.load_recall_batch = AsyncMock(side_effect=load)
        authority.relations.side_effect = lambda ctx, refs: MemoryRelationSnapshot(
            guards=tuple(bodies[r.memory_id].guard for r in refs),
            conflicts=tuple(g for g in conflicts if any(r in g.members for r in refs)),
        )
        authority.load_bodies = AsyncMock(
            side_effect=lambda ctx, refs: tuple(bodies[r.memory_id] for r in refs)
        )
        authority.projection_readiness = AsyncMock(
            side_effect=lambda ctx, selection, source: ProjectionReadiness(
                source=source,
                ready_count=len(candidates),
                pending_count=0,
                failed_count=0,
                complete=True,
            )
        )

        def search(ctx, request):
            selected = sorted(
                (
                    c
                    for c in candidates.values()
                    if c.hits[0].memory_source == request.memory_source
                ),
                key=lambda c: (-c.best_score, c.memory.memory_id),
            )[: request.memory_top_k]
            return MemorySearchResult(
                request=request,
                scope=ctx.principal.home_scope,
                candidates=tuple(
                    c.model_copy(update={"rank": i + 1}) for i, c in enumerate(selected)
                ),
                examined_chunk_hits=sum(len(c.hits) for c in selected),
                rounds_used=1,
                stop_reason="top_k" if len(selected) == request.memory_top_k else "exhausted",
                coverage="complete",
            )

        search_port = SimpleNamespace(search=AsyncMock(side_effect=search))
        base = Recall(
            uow,
            identity,
            events,
            authority,
            Mock(),
            Mock(),
            "space_1",
            settings=settings,
            tokenizer=SimpleNamespace(identifier="test_tokens", count=len),
        )
        recall = GenerationRecall(base, search_port, authority, authority)
        request = recall.plan_request(
            ctx,
            RecallRequest(
                query="query", selection=ScopeSelector(session_id="session"), sources=sources
            ),
            "threshold_recall",
        )
        return SimpleNamespace(
            recall=recall,
            assembly=recall.assembly,
            ctx=ctx,
            request=request,
            candidates=candidates,
            authority=authority,
            conflicts=conflicts,
            rows=rows,
        )

    return build


@pytest.mark.parametrize(
    "threshold,score,expected",
    [
        (0.8, 0.800001, ["only"]),
        (0.8, 0.8, ["only"]),
        (0.8, 0.799999, []),
        (0.8, -0.1, []),
        (0.0, -0.1, []),
        (0.0, 0.0, ["only"]),
    ],
)
async def test_threshold_boundary_controls_primary_admission(
    make_recall, threshold, score, expected
):
    case = make_recall(
        RecallSettings(vector_min_score=threshold), entries=(("long_term", "only", (score,)),)
    )
    plan = await case.assembly.plan(case.ctx, case.request)
    assert [m.memory_id for u in plan.units for m in u.primary_memories] == expected
    assert not plan.degradation_reasons
    if not expected:
        assert plan.rendered_context == "" and plan.tokens_used == 0
        case.authority.load.assert_not_called()
        case.authority.load_recall_batch.assert_not_awaited()
        case.authority.load_bodies.assert_not_awaited()
        assert not any(table == "outbox" for table, _ in case.rows)


async def test_low_scores_are_removed_before_reads_and_persisted_discovery(make_recall):
    case = make_recall(RecallSettings(vector_min_score=0.8))
    discovered = await case.assembly.discover(case.ctx, case.request)
    high_key = case.candidates["high"].memory.model_dump_json()
    for field in (
        "snapshots",
        "manifests",
        "candidate_guards",
        "relation_guards",
        "matched_chunks",
    ):
        assert set(discovered[field]) == {high_key}
    assert discovered["source_ranks"] == {"long_term": {high_key: 1}}
    assert discovered["coverage"]["long_term"] == "complete"
    assert discovered["reasons"] == []
    case.authority.load.assert_not_called()
    case.authority.load_recall_batch.assert_awaited_once_with(
        case.ctx, (case.candidates["high"].memory,)
    )
    plan = await case.assembly.assemble_discovered(
        case.ctx, case.request, json.loads(json.dumps(discovered))
    )
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["high"]
    assert len(plan.units) < 5
    events = [row["event"]["payload"] for (table, _), row in case.rows.items() if table == "outbox"]
    assert [(e["memory"]["memory_id"], e["stage"]) for e in events] == [("high", "read")]
    assert case.assembly.candidates.search.await_count == 1


async def test_full_top_k_of_low_scores_is_normal_empty_without_backfill(make_recall):
    case = make_recall(
        RecallSettings(vector_min_score=0.8),
        entries=tuple(("long_term", f"low_{i}", (0.79,)) for i in range(20)),
    )
    plan = await case.assembly.plan(case.ctx, case.request)
    assert not plan.units and not plan.degradation_reasons and not plan.skipped_group_ids
    assert (
        case.rows[("recall_assembly", case.request.recall_id)]["coverage"]["long_term"]
        == "complete"
    )
    assert case.assembly.candidates.search.await_count == 1
    case.authority.load.assert_not_called()
    case.authority.load_recall_batch.assert_not_awaited()


async def test_repository_policy_filters_candidates_and_preserves_group_limit(make_recall):
    settings = RecallSettings.model_validate_json(
        (Path(__file__).resolve().parents[3] / "configs/recall.azure.json").read_text("utf-8")
    )
    case = make_recall(
        settings,
        entries=tuple(("long_term", f"high_{i}", (0.95 - i * 0.01,)) for i in range(6))
        + tuple(("long_term", f"low_{i}", (0.79,)) for i in range(20)),
    )
    plan = await case.assembly.plan(case.ctx, case.request)
    assert [m.memory_id for u in plan.units for m in u.primary_memories] == [
        "high_0",
        "high_1",
        "high_2",
        "high_3",
        "high_4",
    ]
    assert len(plan.skipped_group_ids) == 1
    assert not plan.degradation_reasons
    assert case.request.long_term_search.memory_top_k == 20
    assert case.request.long_term_search.max_chunk_hits == 100
    read_memories = {
        row["event"]["payload"]["memory"]["memory_id"]
        for (table, _), row in case.rows.items()
        if table == "outbox"
    }
    assert read_memories == {f"high_{i}" for i in range(6)}


async def test_all_low_scores_commit_normal_empty_without_access_events(make_recall):
    case = make_recall(
        RecallSettings(vector_min_score=0.8), entries=(("long_term", "low", (0.79,)),)
    )
    plan = await case.assembly.plan(case.ctx, case.request)
    original = RecallRequest(
        query=plan.request.query,
        selection=plan.request.selection,
        sources="long_term",
        token_budget=plan.request.token_budget,
    )
    record = RecallRecord(
        recall_id=plan.request.recall_id,
        scope=plan.scope,
        state="running",
        stage="finalize",
        revision=2,
        deadline_at=plan.request.deadline_at,
        result_available=False,
    )
    case.authority.revalidate_context.side_effect = lambda tx, ctx, expected: expected.expected
    store = InMemoryRecallRecords()
    with store.transaction() as raw:
        tx = StorageTransaction(raw, "test-cursor")
        for (table, key), row in case.rows.items():
            tx.write(table, key, row)
        tx.write(
            "recall_requests",
            record.recall_id,
            {
                "record": record.model_dump(mode="json"),
                "request": original.model_dump(mode="json"),
                "context": case.ctx.model_dump(mode="json"),
                "signature": fingerprint(original.model_dump(mode="json")),
                "pack": None,
            },
        )
        commit = ContextCommitRequest(
            operation_id=case.ctx.operation_id,
            expected_recall_revision=2,
            plan=plan,
            final_guards=(),
        )
        case.assembly.commit(tx, case.ctx, commit)
        for hook in tx.before_commit:
            hook()
    with store.transaction() as raw:
        tx = StorageTransaction(raw, "test-cursor")
        case.assembly.commit(tx, case.ctx, commit)
        saved = tx.read("recall_requests", record.recall_id)
        pack = ContextPack.model_validate(saved["pack"])
        assert saved["record"]["state"] == "completed"
        assert saved["record"]["revision"] == 3
        assert saved["record"]["result_available"] is True
        assert pack.outcome == "empty" and not pack.groups
        assert not pack.degradation_reasons
        assert pack.coverage.long_term == "complete"
        assert pack.rendered_context == "" and pack.tokens_used == 0
        assert tx.rows("outbox") == []


@pytest.mark.parametrize("settings", [RecallSettings(), None])
async def test_disabled_threshold_preserves_low_score_candidates(make_recall, settings):
    case = make_recall(settings or RecallSettings(vector_min_score=None))
    plan = await case.assembly.plan(case.ctx, case.request)
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["high", "low"]


async def test_threshold_uses_best_chunk_not_average_or_all_chunk_scores(make_recall):
    case = make_recall(
        RecallSettings(vector_min_score=0.8),
        entries=(("long_term", "mixed_chunks", (0.95, 0.2)),),
    )
    plan = await case.assembly.plan(case.ctx, case.request)
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["mixed_chunks"]
    assert plan.units[0].bodies[0].content in plan.rendered_context


async def test_both_sources_are_filtered_before_rrf(make_recall):
    case = make_recall(
        RecallSettings(vector_min_score=0.8),
        entries=(
            ("working", "working_good", (0.9,)),
            ("working", "working_low", (0.7,)),
            ("long_term", "long_good", (0.85,)),
            ("long_term", "long_low", (0.6,)),
        ),
        sources="both",
    )
    plan = await case.assembly.plan(case.ctx, case.request)
    assert {m.memory_id for u in plan.units for m in u.primary_memories} == {
        "working_good",
        "long_good",
    }
    assert {(e.memory.memory_id, e.source, e.source_rank) for e in plan.rank_evidence} == {
        ("working_good", "working", 1),
        ("long_good", "long_term", 1),
    }
    assert not plan.degradation_reasons


async def test_low_score_conflict_companion_is_kept_but_not_primary(make_recall):
    case = make_recall(RecallSettings(vector_min_score=0.8))
    case.conflicts.append(
        ConflictGroup(
            group_id="conflict",
            members=tuple(c.memory for c in case.candidates.values()),
            explanation="both required",
        )
    )
    plan = await case.assembly.plan(case.ctx, case.request)
    assert len(plan.units) == 1
    assert {b.memory.memory_id for b in plan.units[0].bodies} == {"high", "low"}
    assert [m.memory_id for m in plan.units[0].primary_memories] == ["high"]


@pytest.mark.parametrize(
    "coverage,reason", [("partial", "hit_limit"), ("unavailable", "dependency")]
)
async def test_threshold_does_not_hide_incomplete_search(make_recall, coverage, reason):
    case = make_recall(RecallSettings(vector_min_score=0.8))
    original = case.assembly.candidates.search.side_effect

    def incomplete(ctx, request):
        result = original(ctx, request).model_dump()
        result.update(coverage=coverage, stop_reason=reason)
        if coverage == "unavailable":
            result["candidates"] = ()
        return MemorySearchResult.model_validate(result)

    case.assembly.candidates.search.side_effect = incomplete
    if coverage == "unavailable":
        with pytest.raises(FoundationError) as error:
            await case.assembly.plan(case.ctx, case.request)
        assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    else:
        plan = await case.assembly.plan(case.ctx, case.request)
        assert plan.degradation_reasons == ("long_term_hit_limit",)


async def test_all_low_scores_with_partial_search_are_not_normal_empty(make_recall):
    case = make_recall(
        RecallSettings(vector_min_score=0.8), entries=(("long_term", "low", (0.79,)),)
    )
    original = case.assembly.candidates.search.side_effect

    def incomplete(ctx, request):
        result = original(ctx, request).model_dump()
        result.update(coverage="partial", stop_reason="hit_limit")
        return MemorySearchResult.model_validate(result)

    case.assembly.candidates.search.side_effect = incomplete
    discovered = await case.assembly.discover(case.ctx, case.request)
    assert discovered["snapshots"] == {}
    assert discovered["coverage"]["long_term"] == "partial"
    assert discovered["reasons"] == ["long_term_hit_limit"]
    with pytest.raises(FoundationError) as error:
        await case.assembly.assemble_discovered(case.ctx, case.request, discovered)
    assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    assert not any(table == "outbox" for table, _ in case.rows)


async def test_pending_index_with_only_low_scores_is_not_normal_empty(make_recall):
    case = make_recall(
        RecallSettings(vector_min_score=0.8),
        entries=(("working", "low", (0.7,)),),
        sources="working",
    )
    case.authority.projection_readiness.side_effect = None
    case.authority.projection_readiness.return_value = ProjectionReadiness(
        source="working", ready_count=1, pending_count=1, failed_count=0, complete=False
    )
    with pytest.raises(FoundationError) as error:
        await case.assembly.plan(case.ctx, case.request)
    assert error.value.code == ErrorCode.REQUEST_IN_PROGRESS


async def test_changed_threshold_rejects_old_discovery_policy(make_recall):
    old = make_recall(RecallSettings(vector_min_score=0.7))
    discovered = await old.assembly.discover(old.ctx, old.request)
    new = make_recall(RecallSettings(vector_min_score=0.8))
    assert recall_binding(new.recall) != recall_binding(old.recall)
    with pytest.raises(FoundationError, match="policy binding mismatch"):
        await new.assembly.assemble_discovered(old.ctx, old.request, discovered)


def test_explicitly_disabled_threshold_keeps_existing_policy_binding(make_recall):
    before = make_recall()
    after = make_recall(RecallSettings(vector_min_score=None))
    assert "vector_min_score" not in after.recall.settings.model_dump()
    assert "vector_min_score" not in after.recall.settings.model_dump(mode="json")
    assert before.recall.policy_version == after.recall.policy_version
    assert recall_binding(before.recall) == recall_binding(after.recall)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "invalid"])
def test_invalid_threshold_configuration_is_rejected(value):
    with pytest.raises(ValidationError):
        RecallSettings(vector_min_score=value)
