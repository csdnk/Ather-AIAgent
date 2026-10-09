"""Real-store regression for cold users and independent Working deletion."""

import asyncio

import pytest
from remember_helpers import app as app
from remember_helpers import context, drain, facts, save, source

from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.models import CorrectionRequest, DeleteRequest
from aether_agent_memory.runtime.foundation.common import FoundationError


@pytest.mark.parametrize("foreign,cold", [(False, True), (True, False), (True, True)])
def test_delete_working_preserves_final_memory_and_never_reads_other_user(
    app, foreign, cold, monkeypatch
):
    own = save(app, "岚桥站的验收日期为2026年11月17日，负责人是许澄。")
    drain(app)
    final_refs = facts(app, own)
    assert final_refs
    other = save(app, "南湾站的巡检员是罗闻，巡检安排在周四。", user="bob") if foreign else None
    if other:
        drain(app)
    before = app.remember.get(context(app), own.memories[0].memory_id)
    with app.foundation.uow.transaction() as tx:
        finals = [tx.get(memory_ref(ref, versioned=True)) for ref in final_refs]
    if cold:
        app.remember.bodies.verified.clear()
        app.remember.bodies.verified_bytes = 0
    original_decode = app.remember.decode

    def guarded_decode(tx, raw):
        assert raw["ref"]["scope"]["user_id"] == "alice", "deletion read an unrelated user's body"
        assert raw["ref"]["memory_id"] == before.ref.memory_id, "deletion read a final memory"
        return original_decode(tx, raw)

    with monkeypatch.context() as patch:
        patch.setattr(app.remember, "decode", guarded_decode)
        receipt = app.remember.delete(
            context(app),
            before.ref.memory_id,
            DeleteRequest(expected_revision=before.object_revision, reason="delete only original"),
        )
    assert receipt.blocked
    drain(app)
    check_ctx = context(app)
    with app.foundation.uow.transaction() as tx:
        assert [tx.get(memory_ref(ref, versioned=True)) for ref in final_refs] == finals
        assert all(
            e.decision == "allowed"
            for e in app.remember.final_guard(tx, check_ctx, tuple(final_refs), "recall").items
        )
        assert (
            app.remember.final_guard(tx, check_ctx, own.memories, "recall").items[0].reason
            == "deleted"
        )
        assert tx.read("remember_sources", own.source.source_id)["valid"]
    for ref in final_refs:
        assert asyncio.run(app.remember.read_body(context(app), ref)).outcome == "read"
    if other:
        assert (
            app.remember.get(context(app, "bob"), other.memories[0].memory_id).content
            == "南湾站的巡检员是罗闻，巡检安排在周四。"
        )


def test_long_term_correction_still_works_with_unrelated_cold_body(app):
    own = save(app, "岚桥站的验收日期为2026年11月17日。")
    drain(app)
    final = facts(app, own)[0]
    save(app, "南湾站的巡检员是罗闻。", user="bob")
    drain(app)
    app.remember.bodies.verified.clear()
    app.remember.bodies.verified_bytes = 0
    receipt = asyncio.run(
        app.remember.correct_async(
            context(app),
            final.memory_id,
            CorrectionRequest(
                expected_version=final.version,
                content="岚桥站的验收日期为2026年11月18日。",
                source=source("new_evidence"),
                reason="correct long-term date",
            ),
        )
    )
    assert receipt.memories[0].memory_id == final.memory_id
    assert receipt.memories[0].version == final.version + 1
    drain(app)
    assert (
        app.remember.get(context(app), final.memory_id).content
        == "岚桥站的验收日期为2026年11月18日。"
    )
    with pytest.raises(FoundationError) as error:
        asyncio.run(
            app.remember.correct_async(
                context(app),
                own.memories[0].memory_id,
                CorrectionRequest(
                    expected_version=1,
                    content="replacement",
                    source=source("edit"),
                    reason="edit original",
                ),
            )
        )
    assert error.value.code.value == "INVALID_ARGUMENT"
