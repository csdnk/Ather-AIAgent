"""Atomic result/outbox commit and authenticated replay using real RF transactions."""
# 提交与重取测试：使用真实 RF 事务验证结果/Outbox 原子性、幂等及最终权限复核。
# 这里的 HTTP 测试使用 TestClient；真实 TCP HTTP 另由 validate_native_http.py 验证。

import asyncio

import pytest
from fastapi.testclient import TestClient
from test_flows import app as app
from test_flows import context
from test_generation_assembly import assembly_setup

from aether_agent_memory.recall.basic.generation import GenerationRecall
from aether_agent_memory.recall.contracts.foundation import ContextCommitRequest
from aether_agent_memory.recall.contracts.models import RecallRecord, RecallRequest
from aether_agent_memory.runtime.contracts.models import ErrorCode, ScopeSelector
from aether_agent_memory.runtime.flows.http import create_app
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint


def service(app):
    # 在测试应用中显式切换新 Recall 服务，复用已有 RF 生命周期和严格 B 替身。
    ctx, assembly, body, request = assembly_setup(app)
    app.recall.model_space = "test_space"
    app.recall = GenerationRecall(app.recall, assembly.candidates, body, body)
    return ctx, app.recall, body


def request():
    return RecallRequest(query="query", selection=ScopeSelector(), sources="long_term")


def prepared(app):
    # 构造已有计划且处于 running 的 RF 请求，以便精确测试条件提交和失败回滚。
    ctx, assembly, body, req = assembly_setup(app)
    plan = asyncio.run(assembly.plan(ctx, req))
    with app.foundation.uow.transaction() as tx:
        record = RecallRecord(
            recall_id=req.recall_id,
            scope=plan.scope,
            state="running",
            stage="finalize",
            revision=2,
            deadline_at=ctx.deadline_at,
            result_available=False,
        )
        tx.write(
            "recall_requests",
            req.recall_id,
            {
                "record": record.model_dump(mode="json"),
                "context": ctx.model_dump(mode="json"),
                "request": request().model_dump(mode="json"),
                "signature": fingerprint(request().model_dump(mode="json")),
                "pack": None,
            },
        )
    commit = ContextCommitRequest(
        operation_id=ctx.operation_id,
        expected_recall_revision=2,
        plan=plan,
        final_guards=tuple(b.guard for u in plan.units for b in u.bodies),
    )
    return ctx, assembly, body, commit


def test_commit_and_replay_do_not_duplicate_packed_events(app):
    ctx, assembly, body, commit = prepared(app)
    for _ in range(2):
        with app.foundation.uow.transaction() as tx:
            assembly.commit(tx, ctx, commit)
    with app.foundation.uow.transaction() as tx:
        row = tx.read("recall_requests", commit.plan.request.recall_id)
        assert row["record"]["revision"] == 3 and row["record"]["state"] == "completed"
        events = [r["event"]["payload"] for _, r in tx.rows("outbox")]
        assert len([e for e in events if e["stage"] == "read"]) == 2
        assert len([e for e in events if e["stage"] == "packed"]) == 2


@pytest.mark.parametrize(
    "fault", ["deleted", "body", "relation", "generation", "missing", "revision", "tamper"]
)
def test_commit_rejects_stale_or_forged_plan_without_partial_effects(app, fault):
    # 分别改变删除状态、正文/关系修订、发布批次或请求凭据，任何失效都不得写入结果。
    # 先前真实发生的 read 事件保留；本次失败的 packed 事件必须全部回滚。
    ctx, assembly, body, commit = prepared(app)
    if fault == "deleted":
        body.deleted.add("m1")
    elif fault in {"body", "relation"}:
        current = body.bodies["m1"]
        field = "object_revision" if fault == "body" else "relations_revision"
        body.bodies["m1"] = current.model_copy(
            update={"guard": current.guard.model_copy(update={field: 99})}
        )
    elif fault == "generation":
        next(iter(body.proofs.values()))[0]["generation"] = "replacement"
    elif fault == "missing":
        body.guard_fault = "missing"
    elif fault == "revision":
        commit = commit.model_copy(update={"expected_recall_revision": 99})
    else:
        changed = commit.plan.model_copy(update={"degradation_reasons": ("forged",)})
        commit = commit.model_copy(update={"plan": changed})
    with pytest.raises(FoundationError), app.foundation.uow.transaction() as tx:
        assembly.commit(tx, ctx, commit)
    with app.foundation.uow.transaction() as tx:
        row = tx.read("recall_requests", commit.plan.request.recall_id)
        assert row["pack"] is None and row["record"]["revision"] == 2
        assert all(r["event"]["payload"]["stage"] == "read" for _, r in tx.rows("outbox"))


def test_last_transaction_hook_rolls_back_result_and_events(app):
    # 让第二次 B 复核在事务末尾失败，证明已执行的写入仍不会部分提交。
    ctx, assembly, body, commit = prepared(app)
    original = body.revalidate_context

    def fail_second(tx, ctx, req):
        if body.guarded:
            tx.abort(ErrorCode.RESULT_INVALIDATED, "changed at final hook")
        return original(tx, ctx, req)

    body.revalidate_context = fail_second
    with pytest.raises(FoundationError), app.foundation.uow.transaction() as tx:
        assembly.commit(tx, ctx, commit)
    with app.foundation.uow.transaction() as tx:
        assert tx.read("recall_requests", commit.plan.request.recall_id)["pack"] is None
        assert len(tx.rows("outbox")) == 2


def test_service_status_result_and_repeated_request(app):
    ctx, recall, body = service(app)
    pack = asyncio.run(recall.recall(ctx, request()))
    assert recall.status(ctx, pack.recall_id).state == "completed"
    assert recall.result(ctx, pack.recall_id) == pack
    assert asyncio.run(recall.recall(ctx, request())) == pack
    body.deleted.add("m1")
    with pytest.raises(FoundationError) as failure:
        recall.result(ctx, pack.recall_id)
    assert failure.value.code == "RESULT_INVALIDATED"


def test_retrieve_after_revocation_or_other_tenant_denied(app):
    ctx, recall, body = service(app)
    pack = asyncio.run(recall.recall(ctx, request()))
    with pytest.raises(FoundationError):
        recall.result(context(app, "carol"), pack.recall_id)
    with app.foundation.uow.transaction() as tx:
        row = tx.read("identities", "alice")
        row["enabled"] = False
        tx.write("identities", "alice", row)
    with pytest.raises(FoundationError) as failure:
        recall.result(ctx, pack.recall_id)
    assert failure.value.code == "FORBIDDEN"


def test_http_new_service_same_wire_format(app):
    service(app)
    with TestClient(create_app(app, run_worker=False)) as client:
        response = client.post(
            "/p3/recall",
            json=request().model_dump(mode="json"),
            headers={"Authorization": "Bearer alice", "X-Operation-ID": "http_new"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["groups"] and payload["tokens_used"] <= payload["token_budget"]
        headers = {"Authorization": "Bearer alice"}
        assert (
            client.get(f"/p3/recalls/{payload['recall_id']}", headers=headers).json()["state"]
            == "completed"
        )
        assert (
            client.get(f"/p3/recalls/{payload['recall_id']}/result", headers=headers).json()
            == payload
        )
        assert (
            client.get(
                f"/p3/recalls/{payload['recall_id']}/result",
                headers={"Authorization": "Bearer carol"},
            ).status_code
            == 403
        )


def test_opt_in_refuses_incomplete_b_providers(app):
    _, assembly, _, _ = assembly_setup(app)
    space = assembly.candidates.spaces.resolve("test_space")
    with pytest.raises(ValueError, match="complete B"):
        app.enable_generation_recall(
            memories=object(), qualification=object(), bodies=object(), guards=object(), space=space
        )


def test_explicit_host_opt_in_and_unknown_dependency_probe(app):
    ctx, assembly, body, _ = assembly_setup(app)
    app.model_space = app.recall.model_space = "test_space"
    app.embedding = assembly.candidates.embedding
    body.final_guard = app.remember.final_guard
    app.enable_generation_recall(
        memories=body,
        qualification=assembly.candidates.qualification,
        bodies=body,
        guards=body,
        space=assembly.candidates.spaces.resolve("test_space"),
    )
    pack = asyncio.run(app.recall.recall(ctx, request()))
    assert pack.groups
    health = asyncio.run(app.health.report(ctx))
    assert health["dependencies"]["recall_generation"]["state"] == "unknown"
    # Trace has class-level instrumentation even when no external B probe is supplied.
    trace = app.foundation.diagnostics.trace(ctx, ctx.trace_id, limit=500)
    assert any("assembly" in r["node"] for r in trace["records"])


def test_concurrent_duplicate_and_cancel_are_not_success(app):
    ctx, recall, body = service(app)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        original = body.load_bodies

        async def waiting(ctx, refs):
            entered.set()
            await release.wait()
            return await original(ctx, refs)

        body.load_bodies = waiting
        first = asyncio.create_task(recall.recall(ctx, request()))
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(FoundationError) as conflict:
            await recall.recall(ctx, request())
        assert conflict.value.code == "REQUEST_IN_PROGRESS"
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

    asyncio.run(scenario())
    with app.foundation.uow.transaction() as tx:
        records = tx.rows("recall_requests")
        assert len(records) == 1 and records[0][1]["record"]["state"] == "failed"
        assert not records[0][1]["record"]["result_available"]
        assert all(row["event"]["payload"]["stage"] != "packed" for _, row in tx.rows("outbox"))


def test_same_operation_different_query_conflicts(app):
    ctx, recall, body = service(app)
    asyncio.run(recall.recall(ctx, request()))
    with pytest.raises(FoundationError) as failure:
        asyncio.run(recall.recall(ctx, request().model_copy(update={"query": "different"})))
    assert failure.value.code == "IDEMPOTENCY_CONFLICT"


def test_default_host_cannot_downgrade_generation_result_guard(app, tmp_path):
    from aether_agent_memory.runtime.flows.host import ThreeFlows

    ctx, recall, body = service(app)
    pack = asyncio.run(recall.recall(ctx, request()))
    restarted = ThreeFlows(
        app.foundation.uow.path, tmp_path / "restart", embedding_profile="lexical"
    )
    try:
        fresh = context(restarted)
        with pytest.raises(FoundationError) as failure:
            restarted.recall.result(fresh, pack.recall_id)
        assert failure.value.code == "DEPENDENCY_UNAVAILABLE"
    finally:
        restarted.close()


@pytest.mark.parametrize("fault", ["operation", "query", "budget", "scope", "deadline"])
def test_saved_plan_must_match_original_rf_request(app, fault):
    # 模拟计划与 RF 原请求错绑；即使计划自身签名正确，也不能替其他请求提交。
    ctx, assembly, body, commit = prepared(app)
    with app.foundation.uow.transaction() as tx:
        row = tx.read("recall_requests", commit.plan.request.recall_id)
        if fault == "operation":
            row["context"]["operation_id"] = "other_operation"
        elif fault == "query":
            row["request"]["query"] = "other_query"
            row["signature"] = fingerprint(row["request"])
        elif fault == "budget":
            row["request"]["token_budget"] = 100
            row["signature"] = fingerprint(row["request"])
        elif fault == "scope":
            row["record"]["scope"]["session_id"] = "other_session"
        else:
            from aether_agent_memory.runtime.foundation.common import later

            row["record"]["deadline_at"] = later(ctx.deadline_at, 1)
        tx.write("recall_requests", commit.plan.request.recall_id, row)
    with (
        pytest.raises(FoundationError, match="original RF request"),
        app.foundation.uow.transaction() as tx,
    ):
        assembly.commit(tx, ctx, commit)
