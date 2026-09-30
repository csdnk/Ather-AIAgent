import asyncio
from hashlib import sha256

import pytest

from aether_agent_memory.operate.basic.executor import LocalCacheExecutor
from aether_agent_memory.operate.contracts.models import ActionRecord, ActionState, Tier
from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.contracts.models import (
    CorrectionRequest,
    DeleteRequest,
    LifecycleRequest,
    MemoryRef,
    RememberRequest,
    SourceInput,
    TextInput,
)
from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope, ScopeSelector
from aether_agent_memory.runtime.flows.host import ThreeFlows
from aether_agent_memory.runtime.foundation.common import FoundationError, now


def context(app, user="alice", operation=None):
    return app.foundation.identity.context(user, timeout_seconds=300, operation_id=operation)


def source(name="input"):
    return SourceInput(
        kind="conversation", external_id=name, external_version="1", occurred_at=now()
    )


@pytest.fixture
def app(tmp_path, temporal_server):
    host = ThreeFlows(tmp_path / "p3.db", tmp_path / "cache", embedding_profile="lexical")
    people = [
        Principal(
            principal_id=user,
            home_scope=Scope(
                tenant_id=tenant, application_id="app", user_id=user, agent_id="agent"
            ),
            permissions=tuple(Permission),
            auth_epoch=1,
        )
        for user, tenant in [("alice", "t1"), ("bob", "t1"), ("carol", "t2"), ("david", "t2")]
    ]
    host.foundation.identity.provision(
        [(sha256(p.principal_id.encode()).hexdigest(), p) for p in people]
    )
    from temporal_test_support import seed_driver

    seed_driver(host, temporal_server)
    yield host
    host.close()


def save(app, text="我喜欢无糖咖啡", session="session_1", user="alice", operation=None):
    return asyncio.run(
        app.remember.save(
            context(app, user, operation),
            RememberRequest(
                source=source(),
                selection=ScopeSelector(session_id=session),
                content=TextInput(kind="text", text=text),
            ),
        )
    )


def drain(app):
    asyncio.run(app.drain(timeout_seconds=60))


def facts(app, receipt):
    ctx = context(app)
    task = app.foundation.diagnostics.task(ctx, receipt.task_ids[0])
    assert task.state == "succeeded", task
    with app.foundation.uow.transaction() as tx:
        return [MemoryRef.model_validate(ref) for ref in tx.get(task.result_ref)["memories"]]


def recall(
    app,
    query="我喜欢无糖咖啡",
    sources="long_term",
    user="alice",
    operation=None,
    budget=1024,
    selection=None,
):
    return asyncio.run(
        app.recall.recall(
            context(app, user, operation),
            RecallRequest(
                query=query,
                selection=selection or ScopeSelector(),
                sources=sources,
                token_budget=budget,
            ),
        )
    )


def test_write_recall_and_automatic_real_cache(app):
    receipt = save(app)
    drain(app)
    memory = facts(app, receipt)[0]
    assert app.remember.get(context(app), memory.memory_id).projection_state == "ready"
    warm = asyncio.run(app.executor.observe(context(app), memory, "original"))
    assert warm.tier == Tier.WARM
    pack = recall(app)
    assert pack.outcome == "available"
    assert pack.groups[0].items[0].memory == memory
    drain(app)
    observation = asyncio.run(app.executor.observe(context(app), memory, "original"))
    assert observation.tier == Tier.HOT and observation.readable
    assert app.executor.path(memory, Tier.HOT).read_text(encoding="utf-8") == "我喜欢无糖咖啡"
    with app.foundation.uow.transaction() as tx:
        actions = [ActionRecord.model_validate(row) for _, row in tx.rows("operate_actions")]
    assert all(
        a.state == ActionState.SUCCEEDED and a.intent.provider_mode == "real" for a in actions
    )


def test_correct_filters_old_vectors_and_invalidates_old_pack(app):
    receipt = save(app)
    drain(app)
    memory = facts(app, receipt)[0]
    old = recall(app)
    updated = app.remember.correct(
        context(app),
        memory.memory_id,
        CorrectionRequest(
            expected_version=1,
            content="我现在喜欢红茶",
            source=source("correction"),
            reason="explicit correction",
        ),
    )
    drain(app)
    pack = recall(app, "我现在喜欢红茶")
    assert pack.groups and all(item.memory.version == 2 for g in pack.groups for item in g.items)
    assert updated.memories[0].version == 2
    with pytest.raises(FoundationError) as failure:
        app.recall.result(context(app), old.recall_id)
    assert failure.value.code == "RESULT_INVALIDATED"
    with app.foundation.uow.transaction() as tx:
        assert any(row["target"]["memory"]["version"] == 1 for _, row in tx.rows("recall_vectors"))
    working = recall(app, sources="working", selection=ScopeSelector(session_id="session_1"))
    assert working.outcome == "empty"


def test_delete_blocks_old_pack_and_cleans_cache(app):
    receipt = save(app)
    drain(app)
    memory = facts(app, receipt)[0]
    old = recall(app)
    current = app.remember.get(context(app), memory.memory_id)
    deleted = app.remember.delete(
        context(app),
        memory.memory_id,
        DeleteRequest(expected_revision=current.object_revision, reason="delete preference"),
    )
    assert deleted.blocked and deleted.cleanup_state == "pending"
    drain(app)
    assert recall(app).outcome == "empty"
    assert (
        recall(app, sources="working", selection=ScopeSelector(session_id="session_1")).outcome
        == "empty"
    )
    with pytest.raises(FoundationError):
        app.recall.result(context(app), old.recall_id)
    assert not any(app.executor.path(memory, tier).exists() for tier in Tier)
    with pytest.raises(ValueError):
        app.executor.ensure(current)


@pytest.mark.parametrize("user", ["bob", "carol", "david"])
def test_scope_isolation_across_all_flows(app, user):
    receipt = save(app)
    drain(app)
    memory = facts(app, receipt)[0]
    pack = recall(app)
    assert recall(app, user=user).outcome == "empty"
    with pytest.raises(FoundationError):
        app.remember.get(context(app, user), memory.memory_id)
    with pytest.raises(FoundationError):
        app.recall.result(context(app, user), pack.recall_id)
    with app.foundation.uow.transaction() as tx:
        action_id = tx.rows("operate_actions")[0][0]
    with pytest.raises(FoundationError):
        asyncio.run(app.operate.reconcile(context(app, user), action_id))


def test_vector_outage_is_degraded_or_failed_not_fake_empty(app):
    save(app)
    drain(app)
    app.vectors.available = False
    with pytest.raises(FoundationError) as failure:
        recall(app, sources="both", selection=ScopeSelector(session_id="session_1"))
    assert failure.value.code == "DEPENDENCY_UNAVAILABLE"
    with pytest.raises(FoundationError) as failure:
        recall(app)
    assert failure.value.code == "DEPENDENCY_UNAVAILABLE"


def test_context_budget_is_measured_and_too_small_fails(app):
    save(app)
    drain(app)
    pack = recall(app, budget=100)
    assert pack.tokens_used == app.recall.tokenizer.count(pack.rendered_context) <= 100
    assert pack.tokenizer_id == "tiktoken_o200k_base"
    with pytest.raises(FoundationError) as failure:
        recall(app, budget=1)
    assert failure.value.code == "BUDGET_TOO_SMALL"


def test_duplicate_read_events_do_not_double_heat(app):
    save(app)
    drain(app)
    first = recall(app, operation="same_recall")
    second = recall(app, operation="same_recall")
    assert first == second
    drain(app)
    with app.foundation.uow.transaction() as tx:
        views = [v for _, v in tx.rows("operate_views")]
        assert sum(v["successful_reads"] for v in views) == 1


def test_lost_execution_response_queries_original_action(app):
    app.executor.drop_next_response = True
    save(app)
    drain(app)
    with app.foundation.uow.transaction() as tx:
        actions = [ActionRecord.model_validate(row) for _, row in tx.rows("operate_actions")]
        assert len(actions) == 1 and actions[0].state == ActionState.SUCCEEDED
        bound = [r for _, r in tx.rows("tasks") if r["record"]["kind"] == "operate.evaluate"]
        assert any(r.get("original_operation_id") == actions[0].intent.action_id for r in bound)
        assert any(r["record"]["query_attempt"] > 0 for r in bound)
    with app.executor.db() as db:
        assert db.execute("SELECT count(*) FROM actions").fetchone()[0] == 1


def test_archive_and_reactivate(app):
    receipt = save(app)
    drain(app)
    memory = facts(app, receipt)[0]
    app.remember.lifecycle(
        context(app),
        memory.memory_id,
        LifecycleRequest(expected_version=1, target="archived", reason="archive"),
    )
    drain(app)
    assert recall(app).outcome == "empty"
    app.remember.lifecycle(
        context(app),
        memory.memory_id,
        LifecycleRequest(expected_version=1, target="active", reason="restore"),
    )
    drain(app)
    assert recall(app).outcome == "available"


def test_delete_source_blocks_working_and_derived(app):
    receipt = save(app)
    drain(app)
    app.remember.delete_source(
        context(app),
        receipt.source.source_id,
        DeleteRequest(expected_revision=1, reason="remove source"),
    )
    drain(app)
    assert (
        recall(app, sources="both", selection=ScopeSelector(session_id="session_1")).outcome
        == "empty"
    )


def test_inflight_extraction_cannot_resurrect_deleted_working(app):
    receipt = save(app)
    original = app.remember.extraction

    class DeleteDuringExtract:
        async def extract(self, ctx, request):
            item = app.remember.get(context(app), receipt.memories[0].memory_id)
            app.remember.delete(
                context(app),
                item.ref.memory_id,
                DeleteRequest(expected_revision=item.object_revision, reason="concurrent delete"),
            )
            return await original.extract(ctx, request)

    app.remember.extraction = DeleteDuringExtract()
    drain(app)
    assert (
        recall(app, sources="both", selection=ScopeSelector(session_id="session_1")).outcome
        == "empty"
    )


def test_save_idempotency_same_operation_rejects_different_input(app):
    request = RememberRequest(
        source=source(), selection=ScopeSelector(), content=TextInput(kind="text", text="稳定内容")
    )
    one = asyncio.run(app.remember.save(context(app, operation="save_once"), request))
    assert asyncio.run(app.remember.save(context(app, operation="save_once"), request)) == one
    changed = request.model_copy(update={"content": TextInput(kind="text", text="另一个内容")})
    with pytest.raises(FoundationError) as failure:
        asyncio.run(app.remember.save(context(app, operation="save_once"), changed))
    assert failure.value.code == "IDEMPOTENCY_CONFLICT"


def test_read_only_user_can_recall_and_feed_internal_operate(app):
    save(app)
    drain(app)
    principal = context(app).principal.model_copy(
        update={"permissions": (Permission.READ,), "auth_epoch": 2}
    )
    app.foundation.identity.provision([(sha256(b"alice").hexdigest(), principal)])
    pack = recall(app)
    assert pack.outcome == "available"
    drain(app)
    with app.foundation.uow.transaction() as tx:
        assert any(
            row["state"] == "succeeded" and row["intent"]["decision"]["target_tier"] == "hot"
            for _, row in tx.rows("operate_actions")
        )
    with pytest.raises(FoundationError):
        save(app, "unauthorized write")


def test_delete_original_working_also_blocks_derived_facts(app):
    receipt = save(app)
    drain(app)
    memory = facts(app, receipt)[0]
    working = app.remember.get(context(app), receipt.memories[0].memory_id)
    app.remember.delete(
        context(app),
        working.ref.memory_id,
        DeleteRequest(expected_revision=working.object_revision, reason="remove conversation"),
    )
    drain(app)
    assert (
        recall(app, sources="both", selection=ScopeSelector(session_id="session_1")).outcome
        == "empty"
    )
    with pytest.raises(FoundationError):
        app.remember.get(context(app), memory.memory_id)


def test_restart_preserves_context_and_executor_evidence(app, temporal_server):
    receipt = save(app)
    drain(app)
    pack = recall(app)
    drain(app)
    restarted = ThreeFlows(app.foundation.uow.path, app.executor.root, embedding_profile="lexical")
    from temporal_test_support import seed_driver

    seed_driver(restarted, temporal_server)
    try:
        assert restarted.recall.result(context(restarted), pack.recall_id) == pack
        assert facts(restarted, receipt)
        before = sorted(path.name for path in (app.executor.root / "receipts").glob("*.json"))
        drain(restarted)
        assert before == sorted(
            path.name for path in (app.executor.root / "receipts").glob("*.json")
        )
    finally:
        restarted.close()


def test_lost_executor_history_stays_unknown(app, tmp_path, monkeypatch):
    app.executor.drop_next_response = True
    app.foundation.tasks.query_max_attempts = 2
    original = app.executor.submit

    async def lose_history(ctx, intent):
        try:
            return await original(ctx, intent)
        finally:
            app.operate.executor = LocalCacheExecutor(tmp_path / "new_empty_executor")

    monkeypatch.setattr(app.executor, "submit", lose_history)
    save(app)
    drain(app)
    with app.foundation.uow.transaction() as tx:
        assert any(raw["state"] == "unknown" for _, raw in tx.rows("operate_actions"))
        assert any(row["record"]["state"] == "attention_required" for _, row in tx.rows("tasks"))


def test_tampered_embedding_never_marks_projection_ready(app):
    receipt = save(app)
    original = app.remember.embedding

    class Tampered:
        async def embed(self, ctx, request):
            result = await original.embed(ctx, request)
            return result.model_copy(update={"operation_id": "wrong_binding"})

    app.remember.embedding = Tampered()
    drain(app)
    memory = facts(app, receipt)[0]
    assert app.remember.get(context(app), memory.memory_id).projection_state != "ready"


def test_langmem_adapter_rejects_unsubstantiated_candidate():
    from types import SimpleNamespace

    from aether_agent_memory.remember.basic.extraction import LangMemExtraction, SupportedFact
    from aether_agent_memory.remember.contracts.models import ExtractionRequest, SourceRef

    class Manager:
        async def ainvoke(self, value):
            return [SimpleNamespace(content=SupportedFact(text="我喜欢红茶", evidence_quote="我"))]

    request = ExtractionRequest(
        source=SourceRef(
            source_id="s",
            source_version=1,
            content_hash=sha256("我喜欢咖啡".encode()).hexdigest(),
            locator="source:s",
        ),
        text="我喜欢咖啡",
        existing=(),
        policy_version="extractive_v1",
    )
    with pytest.raises(ValueError):
        asyncio.run(LangMemExtraction(Manager(), "test_manager").extract(None, request))


def test_cache_verification_preserves_exact_crlf_bytes(app):
    receipt = save(app, "第一行\r\n第二行")
    drain(app)
    memory = facts(app, receipt)[0]
    observed = asyncio.run(app.executor.observe(context(app), memory, "original"))
    assert observed.readable
    assert app.executor.path(memory, observed.tier).read_bytes() == "第一行\r\n第二行".encode()


def test_cli_requires_explicit_temporal_configuration(tmp_path):
    import subprocess
    import sys

    command = [sys.executable, "-m", "aether_agent_memory.runtime.flows"]
    created = subprocess.run(
        command
        + [
            "init",
            "--directory",
            str(tmp_path / "deployment"),
            "--embedding-profile",
            "lexical",
            "--temporal-endpoint",
            "localhost:7233",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert created.returncode == 0, created.stderr
    checked = subprocess.run(
        command + ["check-config", "--config", str(tmp_path / "deployment" / "service.yaml")],
        capture_output=True,
        timeout=15,
    )
    assert checked.returncode == 0, checked.stderr
    old = subprocess.run(
        command + ["--db", str(tmp_path / "old.db"), "worker"], capture_output=True, timeout=15
    )
    assert old.returncode != 0


def test_component_directories_do_not_join_each_others_workflows(app, tmp_path, temporal_server):
    from temporal_test_support import seed_driver

    first = save(app, operation="same-operation")
    drain(app)
    second = ThreeFlows(
        tmp_path / "second" / "p3.db", tmp_path / "second" / "cache", embedding_profile="lexical"
    )
    second.foundation.identity.provision([(sha256(b"alice").hexdigest(), context(app).principal)])
    seed_driver(second, temporal_server)
    try:
        receipt = save(second, operation="same-operation")
        assert first.task_ids == receipt.task_ids
        drain(second)
        assert facts(second, receipt)
    finally:
        second.close()
