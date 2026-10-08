"""Offline real Recall/Remember assembly; external Redis/Ceph/vector I/O is isolated."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aether_agent_memory.recall.basic.assembly import ContextAssembly
from aether_agent_memory.recall.basic.service import Recall
from aether_agent_memory.recall.contracts.foundation import (
    MemoryCandidate,
    MemorySearchRequest,
    MemorySearchResult,
    RecallPlanRequest,
)
from aether_agent_memory.remember.basic.boundary import RememberBoundary
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.foundation import MemoryRecord, ProjectionManifest
from aether_agent_memory.remember.contracts.models import ConflictGroup
from aether_agent_memory.runtime.contracts.models import ErrorCode, ScopeSelector
from aether_agent_memory.runtime.flows.host import ThreeFlows
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.events import Events
from unit.test_body_address_reads import body_case as body_case


def example(name):
    path = Path(__file__).resolve().parents[2] / "contracts/p3/fixtures/cases.json"
    cases = json.loads(path.read_text("utf-8"))
    return copy.deepcopy(next(c["payload"] for c in cases if c["model"] == name and c["valid"]))


def forbidden_legacy(*args):
    raise AssertionError("Recall must not use legacy sync load or hydration")


@pytest.fixture
def assembly_case(body_case):
    p = body_case
    p.state.cold = OSError("cold body offline")
    p.reader.load = p.reader.load_async = p.reader.decode = forbidden_legacy
    p.reader.bodies.hydrate = p.reader.bodies.read_local = forbidden_legacy
    p.reader.model_space = "space_1"
    boundary = RememberBoundary(p.reader)
    records = [p.record, p.add("independent"), p.add("conflict_member")]
    candidates = []
    for rank, record in enumerate(records, 1):
        payload = example("recall.MemoryCandidate")
        ref = record.ref.model_dump(mode="json")
        digest = record.body_location.content_hash
        payload["memory"] = ref
        payload["rank"] = rank
        payload["best_score"] = 1.0 / rank
        manifest = payload["manifest"]
        manifest.update(memory=ref, body_hash=digest)
        manifest["vector_location"]["content_hash"] = digest
        manifest["chunks"][0].update(end_char=4, input_hash=digest)
        proof = ProjectionManifest.model_validate(manifest)
        ready = MemoryRecord.model_validate(
            {
                **record.model_dump(mode="json"),
                "projection": proof.model_dump(mode="json"),
                "projection_state": "ready",
            }
        )
        with p.transaction() as tx:
            target = memory_ref(record.ref, versioned=True)
            tx.put_if_revision(target, ready.model_dump(mode="json"), tx.revision(target))
            tx.write(
                "remember_manifests", p.reader.refkey(record.ref), proof.model_dump(mode="json")
            )
        payload["guard"] = (
            boundary.relations(p.ctx, (record.ref,)).guards[0].model_dump(mode="json")
        )
        payload["hits"][0].update(
            memory=ref,
            body_hash=digest,
            input_hash=digest,
            memory_source="working",
            rank=rank,
            score=1.0 / rank,
        )
        candidates.append(MemoryCandidate.model_validate(payload))

    class CandidateSearch:
        selected = candidates[:2]

        async def search(self, ctx, request):
            return MemorySearchResult(
                request=request,
                scope=ctx.principal.home_scope,
                candidates=tuple(self.selected),
                examined_chunk_hits=len(self.selected),
                rounds_used=1,
                stop_reason="exhausted",
                coverage="complete",
            )

    search = CandidateSearch()
    base = Recall(
        p.reader.uow,
        p.reader.identity,
        Events(p.reader.uow, p.reader.identity),
        boundary,
        None,
        None,
        "space_1",
        tokenizer=SimpleNamespace(identifier="test_chars", count=len),
    )
    # Legacy metadata access remains part of the compatibility port but must never
    # be consulted by generation Recall for the address-backed body snapshot.
    base.memories = SimpleNamespace(
        projection_readiness=boundary.projection_readiness,
        load=forbidden_legacy,
    )
    request = RecallPlanRequest(
        recall_id="address_recall",
        query="body",
        selection=ScopeSelector(),
        sources=("working",),
        token_budget=1024,
        context_tokenizer=base.tokenizer.identifier,
        policy_version=base.policy_version,
        deadline_at=p.ctx.deadline_at,
        working_search=MemorySearchRequest(
            operation_id="address_recall",
            purpose="recall",
            query="body",
            selection=ScopeSelector(),
            memory_source="working",
            model_space="space_1",
            memory_top_k=3,
            chunk_page_size=3,
            max_chunk_hits=3,
            max_rounds=1,
            deadline_at=p.ctx.deadline_at,
        ),
    )
    p.base, p.boundary, p.search, p.request = base, boundary, search, request
    p.candidates = candidates
    p.assembly = ContextAssembly(base, search, boundary, boundary)
    return p


def add_conflict(p):
    group = ConflictGroup(
        group_id="conflict",
        members=(p.ref, p.candidates[2].memory),
        explanation="Both versions of the fact must be read together.",
    )
    with p.transaction() as tx:
        tx.write("remember_conflicts", group.group_id, group.model_dump(mode="json"))


async def test_candidates_read_hot_addresses_without_cold_or_legacy_load(assembly_case):
    p = assembly_case
    plan = await p.assembly.plan(p.ctx, p.request)
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["memory", "independent"]
    assert plan.rendered_context == "[1] body\n[2] body\n"
    assert all(b.path == "cache" for u in plan.units for b in u.bodies)
    assert p.state.cold_calls == 0 and not p.reader.bodies.verified


async def test_conflict_completion_uses_hot_address_boundary(assembly_case):
    p = assembly_case
    add_conflict(p)
    # Seed a serialized discovery page, as supplied to a later Temporal activity;
    # this exercises the independent relation-member read before discovery changes.
    discovered = {
        "snapshots": {},
        "conflicts": {},
        "reasons": [],
        "excluded": [],
        "coverage": {"working": "complete", "long_term": "not_requested"},
        "manifests": {},
        "candidate_guards": {},
        "relation_guards": {},
        "source_ranks": {"working": {}},
        "matched_chunks": {},
    }
    for candidate in p.candidates[:2]:
        ref = candidate.memory
        key = ref.model_dump_json()
        with p.transaction() as tx:
            record = MemoryRecord.model_validate(tx.get(memory_ref(ref, versioned=True)))
        discovered["snapshots"][key] = p.reader.authority_snapshot(record, "body").model_dump(
            mode="json"
        )
        discovered["manifests"][key] = candidate.manifest.model_dump(mode="json")
        relations = p.boundary.relations(p.ctx, (ref,))
        discovered["candidate_guards"][key] = candidate.guard.model_dump(mode="json")
        discovered["relation_guards"][key] = relations.guards[0].model_dump(mode="json")
        discovered["source_ranks"]["working"][key] = candidate.rank
        for group in relations.conflicts:
            discovered["conflicts"][group.group_id] = group.model_dump(mode="json")
    plan = await p.assembly.assemble_discovered(p.ctx, p.request, discovered)
    group = next(unit for unit in plan.units if unit.conflict)
    assert {b.memory.memory_id for b in group.bodies} == {"conflict_member", "memory"}
    assert [ref.memory_id for ref in group.primary_memories] == ["memory"]
    assert all(body.path == "cache" for body in group.bodies)
    assert p.state.cold_calls == 0 and not p.reader.bodies.verified


async def test_discovery_replay_and_final_reads_record_each_read_once(assembly_case):
    p = assembly_case
    add_conflict(p)
    discovered = await p.assembly.discover(p.ctx, p.request)
    await p.assembly.discover(p.ctx, p.request)
    restored = ContextAssembly(p.base, p.search, p.boundary, p.boundary)
    plan = await restored.assemble_discovered(p.ctx, p.request, json.loads(json.dumps(discovered)))
    assert len(plan.units) == 2
    with p.transaction() as tx:
        events = [row["event"]["payload"] for _, row in tx.rows("outbox")]
        assert sorted((e["memory"]["memory_id"], e["stage"]) for e in events) == [
            ("conflict_member", "read"),
            ("independent", "read"),
            ("memory", "read"),
        ]
        assert tx.read("recall_assembly", p.request.recall_id)["plan"] == plan.model_dump(
            mode="json"
        )


async def test_pending_index_without_candidates_is_not_empty_success(assembly_case):
    p = assembly_case
    p.add("pending")
    p.search.selected = []
    with pytest.raises(FoundationError) as failure:
        await p.assembly.plan(p.ctx, p.request)
    assert failure.value.code == ErrorCode.REQUEST_IN_PROGRESS


async def test_pending_index_preserves_verified_partial_results(assembly_case):
    p = assembly_case
    p.add("pending")
    plan = await p.assembly.plan(p.ctx, p.request)
    assert len(plan.units) == 2 and "working_index_pending" in plan.degradation_reasons
    with p.transaction() as tx:
        assert tx.read("recall_assembly", p.request.recall_id)["coverage"]["working"] == "partial"


async def test_unavailable_conflict_member_skips_whole_group(assembly_case):
    p = assembly_case
    add_conflict(p)
    with p.transaction() as tx:
        target = memory_ref(p.candidates[2].memory, versioned=True)
        record = tx.get(target)
        tx.put_if_revision(target, {**record, "cache_location": None}, tx.revision(target))
    plan = await p.assembly.plan(p.ctx, p.request)
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["independent"]
    assert "incomplete_group" in plan.degradation_reasons
    with p.transaction() as tx:
        events = [row["event"]["payload"] for _, row in tx.rows("outbox")]
        assert sorted(e["memory"]["memory_id"] for e in events) == ["independent", "memory"]


async def test_failed_address_reads_cannot_become_empty_success(assembly_case):
    p = assembly_case
    p.state.raw = None
    with pytest.raises(FoundationError) as failure:
        await p.assembly.plan(p.ctx, p.request)
    assert failure.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    with p.transaction() as tx:
        assert tx.rows("outbox") == []


async def test_boundary_rejects_duplicate_refs_before_read(assembly_case):
    p = assembly_case
    with pytest.raises(FoundationError) as failure:
        await p.boundary.load_recall_batch(p.ctx, (p.ref, p.ref))
    assert failure.value.code == ErrorCode.INVALID_ARGUMENT
    assert p.state.redis_calls == p.state.cold_calls == 0


def test_bootstrap_rejects_body_provider_without_recall_batch_capability(assembly_case):
    p = assembly_case
    host = object.__new__(ThreeFlows)
    host.recall, host.model_space, host.native_embedding = p.base, "space_1", None
    legacy = SimpleNamespace(
        load=forbidden_legacy,
        working=forbidden_legacy,
        final_guard=forbidden_legacy,
        projection_readiness=p.boundary.projection_readiness,
    )
    with pytest.raises(ValueError, match="complete B provider contracts"):
        host.enable_generation_recall(
            memories=legacy,
            qualification=p.boundary,
            bodies=SimpleNamespace(load_bodies=p.boundary.load_bodies),
            guards=p.boundary,
            space=SimpleNamespace(model_space="incompatible_space"),
        )
