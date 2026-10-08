"""AET-20 RC-REL-01–07 at the public Recall plan/commit boundaries.

Real Azure/Temporal fixtures run A and RF; the existing strict B authority
fixture controls relation membership, individual eligibility and revisions.
These are consumer tests, not proof of real B relation publication.
"""

import asyncio

import pytest
from test_flows import app as azure_app
from test_generation_assembly import assembly_setup

from aether_agent_memory.recall.basic.generation import GenerationRecall
from aether_agent_memory.recall.contracts.foundation import ContextCommitRequest
from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.contracts.foundation import (
    FullBodyReadResult,
    MemoryRelationSnapshot,
)
from aether_agent_memory.remember.contracts.models import ConflictGroup
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash

pytestmark = pytest.mark.integration
app = azure_app


def group(body, names=("m1", "m3"), group_id="required_group"):
    return ConflictGroup(
        group_id=group_id,
        members=tuple(body.snapshots[name].ref for name in names),
        explanation="Both facts and their sources are required together",
    )


def related_setup(app, *, only_group=False):
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)
    body.conflicts = [group(body)]
    if only_group:
        request = request.model_copy(
            update={
                "long_term_search": request.long_term_search.model_copy(update={"memory_top_k": 1})
            }
        )
    return ctx, assembly, body, request


def fail_member(body, fault):
    """Faults originate in the independent B provider, never in A's plan."""
    if fault in {"denied", "not_ready", "unverifiable"}:
        original = body.load

        def excluded(ctx, refs):
            batch = original(ctx, refs)
            return batch.model_copy(
                update={
                    "items": tuple(s for s in batch.items if s.ref.memory_id != "m3"),
                    "eligibility": batch.eligibility.model_copy(
                        update={
                            "items": tuple(
                                v.model_copy(
                                    update={
                                        "decision": "unverifiable"
                                        if fault == "unverifiable"
                                        else "excluded",
                                        "reason": fault,
                                    }
                                )
                                if v.ref.memory_id == "m3"
                                else v
                                for v in batch.eligibility.items
                            )
                        }
                    ),
                }
            )

        body.load = excluded
    else:
        guard = body.bodies["m3"].guard
        body.bodies["m3"] = FullBodyReadResult(
            memory=body.snapshots["m3"].ref,
            outcome=fault,
            path="none",
            reason_code="controlled_" + fault,
        )

        # Metadata/relations remain independently readable when the body fails.
        def readable_relations(ctx, refs):
            guards = tuple(
                guard if ref.memory_id == "m3" else body.bodies[ref.memory_id].guard for ref in refs
            )
            return MemoryRelationSnapshot(
                guards=guards,
                conflicts=tuple(g for g in body.conflicts if any(r in g.members for r in refs)),
            )

        body.relations = readable_relations


@pytest.mark.p0
def test_rc_rel_01_unhit_member_is_complete_authorized_and_does_not_consume_primary_k(app):
    ctx, assembly, body, request = related_setup(app)
    plan = asyncio.run(assembly.plan(ctx, request))
    unit = next(u for u in plan.units if u.conflict)
    assert [b.memory.memory_id for b in unit.bodies] == ["m1", "m3"]
    assert [m.memory_id for m in unit.primary_memories] == ["m1"]
    assert [m.memory_id for u in plan.units for m in u.primary_memories] == ["m1", "m2"]
    assert all(b.guard.memory == b.memory for b in unit.bodies)
    assert all(b.guard.authorization_epoch == ctx.principal.auth_epoch for b in unit.bodies)
    assert unit.conflict == body.conflicts[0]
    assert unit.conflict.explanation in plan.rendered_context
    assert all(b.content in plan.rendered_context and b.sources for b in unit.bodies)
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m1", "m3", "m2"]


@pytest.mark.p0
@pytest.mark.parametrize("fault", ["denied", "not_ready", "missing", "unavailable", "unverifiable"])
def test_rc_rel_02_incomplete_group_never_leaks_and_independent_content_survives(app, fault):
    ctx, assembly, body, request = related_setup(app)
    fail_member(body, fault)
    plan = asyncio.run(assembly.plan(ctx, request))
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]
    assert plan.skipped_group_ids == ("required_group",)
    assert body.snapshots["m1"].content not in plan.rendered_context
    assert body.snapshots["m3"].content not in plan.rendered_context
    if fault in {"missing", "unavailable"}:
        assert {"incomplete_group", "body_" + fault} <= set(plan.degradation_reasons)
    elif fault == "unverifiable":
        assert {"qualification_unverifiable", "incomplete_group"} <= set(plan.degradation_reasons)
    else:
        assert plan.degradation_reasons == ()


@pytest.mark.p0
@pytest.mark.parametrize("fault", ["denied", "not_ready", "missing", "unavailable", "unverifiable"])
def test_rc_rel_03_only_incomplete_group_cannot_release_its_hit_member(app, fault):
    ctx, assembly, body, request = related_setup(app, only_group=True)
    fail_member(body, fault)
    if fault in {"denied", "not_ready"}:
        plan = asyncio.run(assembly.plan(ctx, request))
        # Current policy distinguishes explicit exclusions from inability to verify.
        assert not plan.units and not plan.rendered_context and not plan.degradation_reasons
        assert plan.skipped_group_ids == ("required_group",)
    else:
        with pytest.raises(FoundationError) as error:
            asyncio.run(assembly.plan(ctx, request))
        assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
        reason = "qualification_unverifiable" if fault == "unverifiable" else "body_" + fault
        assert reason in str(error.value)


@pytest.mark.p1
def test_rc_rel_04_shared_group_appears_once_with_both_primary_candidates(app):
    ctx, assembly, body, request = related_setup(app)
    body.conflicts = [group(body, ("m1", "m2"))]
    plan = asyncio.run(assembly.plan(ctx, request))
    assert len(plan.units) == 1
    unit = plan.units[0]
    assert [r.memory_id for r in unit.primary_memories] == ["m1", "m2"]
    assert [b.memory.memory_id for b in unit.bodies] == ["m1", "m2"]
    assert all(plan.rendered_context.count(b.content) == 1 for b in unit.bodies)
    assert plan.rendered_context.count(unit.conflict.explanation) == 1
    assert plan.tokens_used == app.recall.tokenizer.count(plan.rendered_context)


@pytest.mark.p0
@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "wrong_ref", "wrong_version", "auth_epoch"]
)
def test_rc_rel_05_plan_rejects_inexact_relation_snapshot(app, fault):
    ctx, assembly, body, request = related_setup(app)
    original = body.relations

    def invalid(ctx, refs):
        snapshot = original(ctx, refs)
        guards = snapshot.guards
        if fault == "missing":
            guards = guards[:-1]
        elif fault == "duplicate":
            guards = (*guards, guards[0])
        elif fault == "auth_epoch":
            guards = (guards[0].model_copy(update={"authorization_epoch": 99}), *guards[1:])
        else:
            ref = guards[0].memory.model_copy(
                update={"memory_id": "wrong_ref"} if fault == "wrong_ref" else {"version": 99}
            )
            guards = (guards[0].model_copy(update={"memory": ref}), *guards[1:])
        return snapshot.model_copy(update={"guards": guards})

    body.relations = invalid
    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, request))
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION


@pytest.mark.p0
@pytest.mark.parametrize("field", ["object_revision", "relations_revision", "authorization_epoch"])
def test_rc_rel_05_changed_member_revision_cannot_reuse_relation_snapshot(app, field):
    ctx, assembly, body, request = related_setup(app)
    original = body.load_bodies

    async def changed(ctx, refs):
        results = await original(ctx, refs)
        return tuple(
            r.model_copy(update={"guard": r.guard.model_copy(update={field: 99})})
            if r.memory.memory_id == "m3"
            else r
            for r in results
        )

    body.load_bodies = changed
    if field == "authorization_epoch":
        with pytest.raises(FoundationError) as error:
            asyncio.run(assembly.plan(ctx, request))
        assert error.value.code == ErrorCode.CONTRACT_VIOLATION
    else:
        plan = asyncio.run(assembly.plan(ctx, request))
        assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]
        assert {"body_stale", "incomplete_group"} <= set(plan.degradation_reasons)


@pytest.mark.p0
@pytest.mark.parametrize("new_member", ["fits", "over_budget", "denied"])
def test_rc_rel_06_post_pack_new_member_invalidates_commit_with_old_budget_and_guards(
    app, new_member
):
    ctx, assembly, body, _ = related_setup(app, only_group=True)
    app.recall.model_space = "test_space"
    recall = GenerationRecall(app.recall, assembly.candidates, body, body)
    recall.settings = recall.settings.model_copy(update={"candidate_limit": 1})
    request = RecallRequest(query="query", sources="long_term", token_budget=512)
    with app.foundation.uow.transaction() as tx:
        record, _ = recall.accept_in(tx, ctx, request)
    recall.stage(ctx, record.recall_id, "assemble")
    plan = asyncio.run(
        recall.assembly.plan(ctx, recall.plan_request(ctx, request, record.recall_id))
    )
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m1", "m3"]
    assert plan.tokens_used <= request.token_budget
    body.conflicts = [group(body, ("m1", "m3", "m2"))]
    if new_member == "denied":
        body.deleted.add("m2")
    elif new_member == "over_budget":
        text = "新增必需成员超出预算。" * 1024
        assert app.recall.tokenizer.count(text) > request.token_budget
        before = body.bodies["m2"]
        body.bodies["m2"] = FullBodyReadResult.model_validate(
            {
                **before.model_dump(),
                "content": text,
                "guard": before.guard.model_copy(update={"body_hash": text_hash(text)}),
                "location": before.location.model_copy(update={"content_hash": text_hash(text)}),
            }
        )
    # A real B relation commit changes revisions on existing members as well.
    for name in ("m1", "m3"):
        before = body.bodies[name]
        body.bodies[name] = before.model_copy(
            update={
                "guard": before.guard.model_copy(
                    update={"relations_revision": before.guard.relations_revision + 1}
                )
            }
        )
    status = recall.status(ctx, record.recall_id)
    commit = ContextCommitRequest(
        operation_id=ctx.operation_id,
        expected_recall_revision=status.revision,
        plan=plan,
        final_guards=tuple(b.guard for u in plan.units for b in u.bodies),
    )
    with pytest.raises(FoundationError) as error, app.foundation.uow.transaction() as tx:
        recall.assembly.commit(tx, ctx, commit)
    assert error.value.code == ErrorCode.RESULT_INVALIDATED
    assert not recall.status(ctx, record.recall_id).result_available
    with pytest.raises(FoundationError) as result_error:
        recall.result(ctx, record.recall_id)
    assert result_error.value.code == ErrorCode.REQUEST_IN_PROGRESS


@pytest.mark.p1
@pytest.mark.parametrize("shape", ["overlap", "ambiguous", "cycle"])
def test_rc_rel_07_overlapping_ambiguous_or_circular_membership_is_bounded_and_never_partial(
    app, shape
):
    ctx, assembly, body, request = related_setup(app)
    groups = [group(body)]
    if shape == "overlap":
        groups.append(group(body, ("m3", "m2"), "second_group"))
    elif shape == "ambiguous":
        groups.append(group(body, ("m1", "m3"), "second_group"))
    else:
        groups.extend(
            [group(body, ("m3", "m2"), "second_group"), group(body, ("m2", "m1"), "third_group")]
        )
    body.conflicts = groups
    if shape == "ambiguous":
        plan = asyncio.run(asyncio.wait_for(assembly.plan(ctx, request), timeout=30))
        assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]
        assert "overlapping_conflicts" in plan.degradation_reasons
        assert body.snapshots["m1"].content not in plan.rendered_context
        assert body.snapshots["m3"].content not in plan.rendered_context
    else:
        with pytest.raises(FoundationError) as error:
            asyncio.run(asyncio.wait_for(assembly.plan(ctx, request), timeout=30))
        assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
        assert "overlapping_conflicts" in str(error.value)
