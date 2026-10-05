"""First real model content is visible before completion or memory persistence."""

import json
from uuid import uuid4

import httpx
import pytest
from test_directory import directory  # noqa: F401

from aether_platform.chat import Conversations
from aether_platform.p3 import P3Error

SETTINGS = {"endpoint": "https://model.test", "model": "test", "api_key": "test"}


def setup_chat(directory):  # noqa: F811
    db, _, _ = directory
    chat = Conversations(db)
    chat.migrate()
    actor = db.authenticate("https://id.test", "a1")
    conversation = chat.create(actor)["id"]
    turn = str(uuid4())
    chat.begin(actor, conversation, turn, "项目什么时候交付？")
    return chat, actor, conversation, turn


def model_stream(monkeypatch, after_first, *, complete=True):
    original = httpx.Client

    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            yield (
                "data: "
                + json.dumps(
                    {"choices": [{"delta": {"content": "根据资料"}, "finish_reason": None}]}
                )
                + "\n\n"
            ).encode()
            after_first()
            if complete:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "choices": [
                                {"delta": {"content": "，周五交付。"}, "finish_reason": "stop"}
                            ]
                        }
                    )
                    + "\n\n"
                ).encode()
                yield b"data: [DONE]\n\n"

    def respond(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks())

    monkeypatch.setattr(
        "aether_platform.chat.httpx.Client",
        lambda **kw: original(**kw, transport=httpx.MockTransport(respond)),
    )


def test_first_content_is_persisted_while_model_is_still_streaming(directory, monkeypatch):  # noqa: F811
    chat, actor, conversation, turn = setup_chat(directory)
    observed = []

    def first():
        row = chat.messages(actor, conversation)["messages"][-1]
        observed.append(row)
        assert row["content"] == "根据资料"
        assert row["status"] == "pending"
        assert row["phase"] == "generating"
        assert row["first_token_ms"] >= 0

    model_stream(monkeypatch, first)
    chat.generate(actor, conversation, turn, SETTINGS)
    assert len(observed) == 1
    assert chat.messages(actor, conversation)["messages"][-1]["content"] == "根据资料，周五交付。"


def test_truncated_stream_is_not_reported_as_a_complete_answer(directory, monkeypatch):  # noqa: F811
    chat, actor, conversation, turn = setup_chat(directory)
    model_stream(monkeypatch, lambda: None, complete=False)
    chat.generate(actor, conversation, turn, SETTINGS)
    row = chat.messages(actor, conversation)["messages"][-1]
    assert row["status"] == "failed"
    assert row["content"] == "根据资料"


def test_retry_clears_partial_content_and_old_worker_cannot_append(directory):  # noqa: F811
    chat, actor, conversation, turn = setup_chat(directory)
    chat.progress(actor, conversation, turn, "旧内容", phase="generating", attempt=1)
    chat.finish(actor, conversation, turn, "旧内容", "连接断开")
    second = chat.begin(actor, conversation, turn, "项目什么时候交付？")
    assert chat.messages(actor, conversation)["messages"][-1]["content"] == ""
    assert not chat.progress(actor, conversation, turn, "过期内容", phase="generating", attempt=1)
    assert chat.progress(
        actor, conversation, turn, "新内容", phase="generating", attempt=second["attempt"]
    )


def test_queued_save_is_durable_and_recovered_without_regenerating_answer(directory, monkeypatch):  # noqa: F811
    chat, actor, conversation, turn = setup_chat(directory)
    chat.progress(
        actor,
        conversation,
        turn,
        "答案",
        phase="generating",
        attempt=1,
        evidence={"status": "save_queued", "operation_id": "save_test", "sources": []},
    )
    chat.finish(actor, conversation, turn, "答案")
    calls = []

    class P3:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def selection(self, session):
            return {}

        def call(self, method, path, **kwargs):
            calls.append((method, path))
            if "operation-requests" in path:
                return {"state": "found", "job_id": "existing_job"}
            return {"phase": "saved", "saved": True, "memories": [], "task_ids": []}

    monkeypatch.setattr("aether_platform.chat.P3Client", P3)
    restarted = Conversations(chat.directory)
    restarted.save_pending(actor, conversation, {"base_url": "http://127.0.0.1:19020"}, "token")
    row = restarted.messages(actor, conversation)["messages"][-1]
    assert row["content"] == "答案" and row["status"] == "complete"
    assert row["memory_evidence"]["saved"] is True
    assert all(method == "GET" for method, _ in calls)


def test_memory_queue_timeout_does_not_fail_generated_answer(directory, monkeypatch):  # noqa: F811
    chat, actor, conversation, turn = setup_chat(directory)
    checks = []

    class P3:
        def __init__(self, *args, **kwargs):
            self.http = self

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def identity(self):
            pass

        def selection(self, session):
            return {}

        def source_excerpts(self, pack):
            return []

        def recall(self, *args):
            return {"recall_id": "recall_test"}

        def call(self, method, path, **kwargs):
            if "recalls/" in path:
                checks.append("validated")
                return {
                    "recall_id": "recall_test",
                    "outcome": "empty",
                    "degradation_reasons": [],
                    "groups": [],
                    "rendered_context": "",
                }
            row = chat.messages(actor, conversation)["messages"][-1]
            assert row["status"] == "complete" and row["content"]
            if "operation-requests" in path:
                return {"state": "unconfirmed"}
            raise P3Error("REQUEST_IN_PROGRESS", job_id="job_slow")

    monkeypatch.setattr("aether_platform.chat.P3Client", P3)
    model_stream(monkeypatch, lambda: checks.append("first_seen"))
    chat.generate(
        actor, conversation, turn, SETTINGS, p3_settings={"base_url": "http://127.0.0.1:19020"}
    )
    row = chat.messages(actor, conversation)["messages"][-1]
    assert row["status"] == "complete"
    assert row["memory_status"] == "saving"
    assert checks.index("validated") < checks.index("first_seen")


def test_unconfirmed_save_is_queried_without_resubmitting(directory, monkeypatch):  # noqa: F811
    chat, actor, conversation, turn = setup_chat(directory)
    chat.finish(
        actor,
        conversation,
        turn,
        "答案",
        evidence={
            "status": "saving",
            "operation_id": "save_unknown",
            "sources": [],
        },
    )
    calls = []

    class P3:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def call(self, method, path, **kwargs):
            calls.append(method)
            return {"state": "unconfirmed"}

    monkeypatch.setattr("aether_platform.chat.P3Client", P3)
    chat.save_pending(actor, conversation, {"base_url": "http://127.0.0.1:19020"}, "token")
    assert calls == ["GET"]
    assert chat.messages(actor, conversation)["messages"][-1]["status"] == "complete"


def test_account_revocation_stops_further_stream_publication(directory, monkeypatch):  # noqa: F811
    from aether_platform.directory import AccessDeniedError

    chat, actor, conversation, turn = setup_chat(directory)

    def revoke():
        with chat.directory.connection() as conn:
            conn.execute("UPDATE users SET enabled=false WHERE id=%s", (actor.id,))

    model_stream(monkeypatch, revoke)
    chat.generate(actor, conversation, turn, SETTINGS)
    with pytest.raises(AccessDeniedError):
        chat.messages(actor, conversation)
    with chat.directory.connection() as conn:
        row = conn.execute("SELECT output,status FROM chat_turns WHERE id=%s", (turn,)).fetchone()
    assert row["output"] == "根据资料"
    assert row["status"] != "complete"


def test_invalidated_sources_stop_stream_and_clear_published_snapshot(directory, monkeypatch):  # noqa: F811
    chat, actor, conversation, turn = setup_chat(directory)
    state = {"invalid": False}

    class P3:
        def __init__(self, *args, **kwargs):
            self.http = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def close(self):
            pass

        def identity(self):
            pass

        def source_excerpts(self, pack):
            return []

        def recall(self, *args):
            return {"recall_id": "recall_test"}

        def call(self, *args, **kwargs):
            if state["invalid"]:
                raise P3Error("RESULT_INVALIDATED")
            return {
                "recall_id": "recall_test",
                "outcome": "empty",
                "degradation_reasons": [],
                "groups": [],
                "rendered_context": "",
            }

    def invalidate():
        assert chat.messages(actor, conversation)["messages"][-1]["content"] == "根据资料"
        state["invalid"] = True

    monkeypatch.setattr("aether_platform.chat.P3Client", P3)
    model_stream(monkeypatch, invalidate)
    chat.generate(
        actor, conversation, turn, SETTINGS, p3_settings={"base_url": "http://127.0.0.1:19020"}
    )
    row = chat.messages(actor, conversation)["messages"][-1]
    assert row["status"] == "failed"
    assert row["content"] == ""
    assert row["memory_evidence"] is None
