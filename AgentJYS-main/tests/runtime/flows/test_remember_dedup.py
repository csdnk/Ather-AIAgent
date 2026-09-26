"""Duplicate creation regressions across providers, batches and occurrence scopes."""

import asyncio

import pytest
from remember_helpers import app as app
from remember_helpers import context, drain, facts, save, source

from aether_agent_memory.remember.basic.comparison import ComparisonDecision
from aether_agent_memory.remember.basic.dedup import canonical_text
from aether_agent_memory.remember.contracts.models import (
    CandidateFact,
    ExtractionResult,
    RememberRequest,
    TextInput,
)
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError, later


class AlwaysCreate:
    async def compare(self, ctx, candidate, existing):
        return ComparisonDecision(outcome="create", reason="adversarial_create")


class Semantic:
    async def extract(self, ctx, request):
        return ExtractionResult(
            candidates=(
                CandidateFact(
                    text=request.text,
                    sources=(request.source,),
                    kind="semantic",
                    evidence_status="supported",
                    fact_key=request.source.source_id,
                ),
            ),
            model_id="fixture",
            policy_version=request.policy_version,
        )


def test_source_redelivery_new_operation_reuses_working_and_tasks(app):
    request = RememberRequest(
        selection=ScopeSelector(),
        source=source("message_42"),
        content=TextInput(kind="text", text="同一消息只能接收一次"),
    )
    a = asyncio.run(app.remember.save(context(app), request))
    b = asyncio.run(app.remember.save(context(app), request))
    assert a.memories == b.memories and a.source == b.source and a.task_ids == b.task_ids
    assert a.operation_id != b.operation_id
    with app.foundation.uow.transaction() as tx:
        assert len(tx.rows("remember_current")) == 1
    with pytest.raises(FoundationError) as error:
        asyncio.run(
            app.remember.save(
                context(app),
                request.model_copy(update={"content": TextInput(kind="text", text="偷偷改变正文")}),
            )
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_document_identity_ignores_delivery_time_but_occurrences_do_not(app):
    request = RememberRequest(
        selection=ScopeSelector(),
        source=source("doc").model_copy(update={"kind": "document"}),
        content=TextInput(kind="text", text="评审文档"),
    )
    a = asyncio.run(app.remember.save(context(app), request))
    later_source = request.source.model_copy(
        update={"occurred_at": later(request.source.occurred_at, 60)}
    )
    b = asyncio.run(
        app.remember.save(context(app), request.model_copy(update={"source": later_source}))
    )
    assert a.memories == b.memories
    request = request.model_copy(
        update={"source": request.source.model_copy(update={"kind": "conversation"})}
    )
    c = asyncio.run(app.remember.save(context(app), request))
    d = asyncio.run(
        app.remember.save(
            context(app),
            request.model_copy(
                update={
                    "source": request.source.model_copy(
                        update={"occurred_at": later_source.occurred_at}
                    )
                }
            ),
        )
    )
    assert c.memories != d.memories


def test_exact_semantic_override_model_create_even_without_vectors(app):
    app.remember.extraction = Semantic()
    app.remember.comparison = AlwaysCreate()
    a = save(app, "必须保留审计日志30天")
    drain(app)

    class BrokenSearch:
        async def search(self, *args, **kwargs):
            raise AssertionError("deterministic duplicates must not require vectors")

    app.remember.vector_search = BrokenSearch()
    b = save(app, "必须保留审计日志30天")
    drain(app)
    assert facts(app, a) == facts(app, b)
    assert len(app.remember.get(context(app), facts(app, a)[0].memory_id).sources) == 2


def test_batch_identical_semantic_different_fact_keys_creates_once(app):
    class Repeated:
        async def extract(self, ctx, request):
            return ExtractionResult(
                candidates=tuple(
                    CandidateFact(
                        text=request.text,
                        kind="semantic",
                        fact_key=f"key_{i}",
                        sources=(request.source,),
                        evidence_status="supported",
                    )
                    for i in range(3)
                ),
                model_id="fixture",
                policy_version=request.policy_version,
            )

    app.remember.extraction = Repeated()
    app.remember.comparison = AlwaysCreate()
    receipt = save(app, "不得在日志中记录密码")
    drain(app)
    assert len(facts(app, receipt)) == 1
    with app.foundation.uow.transaction() as tx:
        assert len(tx.rows("remember_current")) == 2


def test_normalization_keeps_original_and_all_evidence(app):
    app.remember.extraction = Semantic()
    app.remember.comparison = AlwaysCreate()
    a = save(app, "Café\r\n日志保留30天")
    drain(app)
    b = save(app, " Cafe\u0301\n日志保留30天 ")
    drain(app)
    assert facts(app, a) == facts(app, b)
    item = app.remember.get(context(app), facts(app, a)[0].memory_id)
    assert item.content == "Café\r\n日志保留30天" and len(item.sources) == 2


def test_negation_numbers_and_scope_are_not_equivalence(app):
    app.remember.extraction = Semantic()
    receipts = [
        save(app, text, session=session)
        for text, session in [
            ("保留30天", "s1"),
            ("保留90天", "s1"),
            ("不保留30天", "s1"),
            ("保留30天", "s2"),
        ]
    ]
    drain(app)
    assert len({facts(app, r)[0].memory_id for r in receipts}) == 4
    assert canonical_text("a  b") != canonical_text("a b")


def test_source_identity_is_scoped_to_user_and_session(app):
    request = RememberRequest(
        selection=ScopeSelector(),
        source=source("same_id"),
        content=TextInput(kind="text", text="秘密"),
    )
    a = asyncio.run(app.remember.save(context(app), request))
    b = asyncio.run(app.remember.save(context(app, "bob"), request))
    c = asyncio.run(
        app.remember.save(
            context(app),
            request.model_copy(update={"selection": ScopeSelector(session_id="other")}),
        )
    )
    assert len({r.memories[0].memory_id for r in (a, b, c)}) == 3


@pytest.mark.parametrize("same_batch", [False, True])
def test_model_equivalence_merges_paraphrases_after_verification(app, same_batch):
    from aether_agent_memory.remember.basic.comparison import EquivalenceVerdict

    class Paraphrases(Semantic):
        async def extract(self, ctx, request):
            pieces = request.text.split("\n")
            return ExtractionResult(
                candidates=tuple(
                    CandidateFact(
                        text=text,
                        sources=(request.source,),
                        kind="semantic",
                        evidence_status="supported",
                    )
                    for text in pieces
                ),
                model_id="fixture",
                policy_version=request.policy_version,
            )

    class Compare:
        async def compare(self, ctx, candidate, existing):
            if existing:
                return ComparisonDecision(
                    outcome="equivalent",
                    target_id=existing[0].ref.memory_id,
                    reason="same explicit constraint",
                )
            return ComparisonDecision(outcome="create", reason="first fact")

    class Verify:
        async def verify(self, ctx, candidate, target):
            return EquivalenceVerdict(
                equivalent=True,
                same_identity=True,
                preserves_conditions=True,
                candidate_quote=candidate.text,
                existing_quote=target.content,
                reason="fixture confirmed paraphrase",
            )

    app.remember.extraction, app.remember.comparison = Paraphrases(), Compare()
    app.remember.equivalence_verifier = Verify()
    if same_batch:
        a = save(app, "日志中禁止记录密码。\n密码不得写入日志。")
        drain(app)
        assert len(facts(app, a)) == 1
    else:
        a = save(app, "日志中禁止记录密码。")
        drain(app)
        b = save(app, "密码不得写入日志。")
        drain(app)
        assert facts(app, a) == facts(app, b)
        assert len(app.remember.get(context(app), facts(app, a)[0].memory_id).sources) == 2
        from aether_agent_memory.remember.contracts.models import DeleteRequest

        canonical = facts(app, a)[0]
        app.remember.revoke_source(
            context(app),
            a.source.source_id,
            DeleteRequest(expected_revision=1, reason="withdraw original source"),
        )
        drain(app)
        remaining = app.remember.get(context(app), canonical.memory_id)
        assert remaining.sources == (b.source,)
        guard_context = context(app)
        with app.foundation.uow.transaction() as tx:
            relation = tx.read("remember_relations", canonical.memory_id)
            assert relation["revalidation_reason"] == "verified_equivalent_support"
            guarded = app.remember.final_guard(tx, guard_context, (canonical,), "recall")
            assert guarded.items[0].decision == "allowed"
    with app.foundation.uow.transaction() as tx:
        assert len(tx.rows("remember_equivalent_forms")) == 1


def test_unverified_semantic_similarity_keeps_both_claims(app):
    from aether_agent_memory.remember.basic.comparison import EquivalenceVerdict

    app.remember.extraction = Semantic()
    a = save(app, "审计日志保留30天")
    drain(app)

    class Compare:
        async def compare(self, ctx, candidate, existing):
            return ComparisonDecision(
                outcome="equivalent",
                target_id=existing[0].ref.memory_id,
                reason="incorrect model similarity",
            )

    class Verify:
        async def verify(self, ctx, candidate, target):
            return EquivalenceVerdict(
                equivalent=False,
                same_identity=True,
                preserves_conditions=False,
                candidate_quote=candidate.text,
                existing_quote=target.content,
                reason="quantity changed",
            )

    app.remember.comparison, app.remember.equivalence_verifier = Compare(), Verify()
    b = save(app, "审计日志保留90天")
    drain(app)
    assert facts(app, a) != facts(app, b)


def test_existing_source_and_exact_indexes_backfill_without_new_memories(app):
    from aether_agent_memory.remember.basic.dedup import source_identity

    request = RememberRequest(
        selection=ScopeSelector(),
        source=source("old_message"),
        content=TextInput(kind="text", text="old durable input"),
    )
    first = asyncio.run(app.remember.save(context(app), request))
    with app.foundation.uow.transaction() as tx:
        tx.write("remember_migrations", "source_identity_v2", False)
        tx.write(
            "remember_source_receipts", source_identity(first.memories[0].scope, request), None
        )
    second = asyncio.run(app.remember.save(context(app), request))
    assert second.memories == first.memories
    with app.foundation.uow.transaction() as tx:
        assert len(tx.rows("remember_current")) == 1


def test_concurrent_source_delivery_uses_one_working(app):
    from concurrent.futures import ThreadPoolExecutor

    request = RememberRequest(
        selection=ScopeSelector(),
        source=source("concurrent_message"),
        content=TextInput(kind="text", text="delivered concurrently"),
    )
    contexts = [context(app) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(
            pool.map(lambda ctx: asyncio.run(app.remember.save(ctx, request)), contexts)
        )
    assert len({r.memories[0].memory_id for r in receipts}) == 1
    assert len({r.source.source_id for r in receipts}) == 1
