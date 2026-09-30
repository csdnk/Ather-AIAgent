"""Regression for dashboard reads stalling writers and historical queue polling."""

import copy
import runpy
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from test_foundation import app as app
from test_foundation import submit

from aether_agent_memory.runtime.contracts.models import EventEnvelope, TaskRecord


def test_dialogue_timeout_identifies_request_without_retry_or_credential():
    script = Path(__file__).resolve().parents[3] / "scripts/p3/dialogue_demo.py"
    demo_type = runpy.run_path(str(script))["DialogueDemo"]
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("provider message containing sensitive data", request=request)

    with (
        httpx.Client(
            base_url="http://127.0.0.1:18080",
            transport=httpx.MockTransport(timeout),
            headers={"Authorization": "Bearer test-secret"},
        ) as client,
        pytest.raises(TimeoutError) as error,
    ):
        demo_type(client).call("就绪检查", "GET", "/p3/ready")
    assert "就绪检查: GET /p3/ready" in str(error.value)
    assert "test-secret" not in str(error.value) and "sensitive data" not in str(error.value)
    assert len(calls) == 1


def test_slow_trace_reader_does_not_block_span_writer(app, monkeypatch):
    log = app.telemetry
    ctx = app.identity.context("alice")
    with log.span(ctx, "remember.test"):
        pass
    reader_started, release_reader = threading.Event(), threading.Event()
    original = log.reader

    @contextmanager
    def slow_reader():
        with original() as db:
            db.execute("SELECT count(*) FROM node_logs").fetchone()
            reader_started.set()
            assert release_reader.wait(5)
            yield db

    monkeypatch.setattr(log, "reader", slow_reader)

    def write():
        with log.span(ctx, "remember.concurrent_write"):
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        catalog = pool.submit(log.traces, ctx)
        try:
            assert reader_started.wait(2)
            # This must finish while the catalog still holds its read snapshot.
            pool.submit(write).result(timeout=2)
            assert not catalog.done()
            assert log.dropped == 0
        finally:
            release_reader.set()
        assert catalog.result(timeout=2)["items"]


def test_dialogue_recall_wait_reports_expected_and_actual(monkeypatch):
    script = Path(__file__).resolve().parents[3] / "scripts/p3/dialogue_demo.py"
    demo = runpy.run_path(str(script))["DialogueDemo"](None)
    pack = {"outcome": "available", "rendered_context": "我的饮品偏好是可乐。"}
    monkeypatch.setattr(demo, "recall", lambda query: pack)

    def one_probe(label, probe):
        result = probe()
        if result:
            return result
        raise TimeoutError(label + "超时")

    monkeypatch.setattr(demo, "wait", one_probe)
    assert demo.wait_for_recall("跨会话长期召回", "我的饮品偏好是什么？", "可乐") == pack
    with pytest.raises(TimeoutError) as error:
        demo.wait_for_recall("跨会话长期召回", "我的饮品偏好是什么？", "无糖咖啡")
    assert "预期召回：'无糖咖啡'" in str(error.value)
    assert "实际召回：'我的饮品偏好是可乐。'" in str(error.value)
    assert "outcome=available" in str(error.value)


def test_dialogue_recall_transport_timeout_stays_distinct(monkeypatch):
    script = Path(__file__).resolve().parents[3] / "scripts/p3/dialogue_demo.py"
    demo = runpy.run_path(str(script))["DialogueDemo"](None)

    def timeout(query):
        raise TimeoutError("POST /p3/recall 请求超时") from httpx.ReadTimeout("private")

    monkeypatch.setattr(demo, "recall", timeout)
    with pytest.raises(TimeoutError, match="^POST /p3/recall 请求超时$"):
        demo.wait_for_recall("跨会话长期召回", "饮品偏好", "可乐")


def test_active_projection_avoids_terminal_history_and_index_follows_rollback(app, monkeypatch):
    submit(app)
    with app.uow.transaction() as tx:
        original = tx.rows("tasks")[0][1]
        original["record"]["state"] = "succeeded"
        tx.write("tasks", original["record"]["task_id"], original)
        for index in range(1500):
            row = copy.deepcopy(original)
            row["record"]["task_id"] = f"history_{index}"
            tx.write("tasks", row["record"]["task_id"], row)
        assert tx.active_task_rows() == []
    calls = []
    validate = TaskRecord.model_validate

    def counted(value, *args, **kwargs):
        calls.append(value["task_id"])
        return validate(value, *args, **kwargs)

    monkeypatch.setattr(TaskRecord, "model_validate", counted)
    with app.uow.transaction() as tx:
        assert not tx.active_task_rows()
    assert calls == []
    # The queue projection is transactional, including failed writes and restart.
    with pytest.raises(RuntimeError), app.uow.transaction() as tx:
        row = copy.deepcopy(original)
        row["record"]["state"] = "pending"
        tx.write("tasks", row["record"]["task_id"], row)
        assert len(tx.active_task_rows()) == 1
        raise RuntimeError("rollback")
    with app.uow.transaction() as tx:
        assert tx.active_task_rows() == []
    submit(app, key="after-history")
    with app.uow.transaction() as tx:
        assert len(tx.active_task_rows()) == 1
    assert not any(task_id.startswith("history_") for task_id in calls)


def test_delivery_projection_skips_acknowledged_event_history(app, monkeypatch):
    submit(app)
    with app.uow.transaction() as tx:
        original = tx.rows("deliveries")[0][1]
        original["state"] = "acknowledged"
        for key, _ in tx.rows("deliveries"):
            tx.write("deliveries", key, original)
        for index in range(1500):
            tx.write("deliveries", f"delivered_{index}", original)
        assert tx.pending_delivery_rows() == []

    def unexpected(*args, **kwargs):
        raise AssertionError("completed event was revalidated during idle dispatch")

    monkeypatch.setattr(EventEnvelope, "model_validate", unexpected)
    with app.uow.transaction() as tx:
        assert not tx.pending_delivery_rows()
