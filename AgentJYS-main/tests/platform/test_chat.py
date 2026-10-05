"""Conversation ownership and durable turn IDs on the real isolated database."""

import importlib.util
from uuid import uuid4

import pytest
from test_directory import directory  # noqa: F401


def test_chat_component_exists():
    assert importlib.util.find_spec("aether_platform.chat") is not None


def test_invalidated_recall_gets_new_identity_but_ambiguous_retry_keeps_identity(
    directory,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    from aether_platform.chat import Conversations
    from aether_platform.p3 import P3Error

    db, _, _ = directory
    chat = Conversations(db)
    chat.migrate()
    actor = db.authenticate("https://id.test", "a1")
    conversation = chat.create(actor)["id"]
    turn_id = str(uuid4())
    operations = []
    errors = iter(["CONNECTION_UNCONFIRMED", "RESULT_INVALIDATED", "CONNECTION_UNCONFIRMED"])

    class Client:
        def __init__(self, *args, **kwargs):
            self.http = self

        def identity(self):
            return {}

        def close(self):
            pass

        def recall(self, query, operation_id):
            operations.append(operation_id)
            raise P3Error(next(errors))

    monkeypatch.setattr("aether_platform.chat.P3Client", Client)
    for _ in range(3):
        attempt = chat.begin(actor, conversation, turn_id, "原来的项目安排")
        chat.generate(
            actor,
            conversation,
            turn_id,
            {"api_key": "test", "endpoint": "test"},
            attempt["attempt"],
            {"base_url": "http://127.0.0.1:19020"},
            "test",
        )
    assert operations[0] == operations[1]
    assert operations[2] != operations[1]


def test_conversation_and_turns_cannot_cross_user_or_tenant(directory):  # noqa: F811
    from aether_platform.chat import Conversations
    from aether_platform.directory import AccessDeniedError

    db, _, _ = directory
    chat = Conversations(db)
    chat.migrate()
    a = db.authenticate("https://id.test", "a1")
    own = chat.create(a)
    turn = str(uuid4())
    chat.begin(a, own["id"], turn, "你好")
    chat.finish(a, own["id"], turn, "你好，请问有什么可以帮你？")
    assert len(chat.messages(a, own["id"])["messages"]) == 2
    assert chat.begin(a, own["id"], turn, "你好")["status"] == "complete"
    for other in ("a2", "b1", "aa", "root"):
        actor = db.authenticate("https://id.test", other)
        with pytest.raises(AccessDeniedError):
            chat.messages(actor, own["id"])
    with pytest.raises(ValueError):
        chat.begin(a, own["id"], turn, "不同的消息")


def test_rename_archive_and_reload_keep_only_own_history(directory):  # noqa: F811
    from aether_platform.chat import Conversations

    db, _, _ = directory
    chat = Conversations(db)
    chat.migrate()
    a = db.authenticate("https://id.test", "a1")
    conversation = chat.create(a)
    chat.rename(a, conversation["id"], "需求讨论")
    assert Conversations(db).list(a)[0]["title"] == "需求讨论"
    assert chat.list(db.authenticate("https://id.test", "a2")) == []
    chat.archive(a, conversation["id"])
    assert chat.list(a) == []


def test_expired_worker_cannot_publish_over_new_attempt(directory):  # noqa: F811
    from aether_platform.chat import Conversations

    db, _, _ = directory
    chat = Conversations(db)
    chat.migrate()
    actor = db.authenticate("https://id.test", "a1")
    conversation = chat.create(actor)["id"]
    turn_id = str(uuid4())
    first = chat.begin(actor, conversation, turn_id, "保留同一条消息")
    with db.connection() as conn:
        conn.execute(
            "UPDATE chat_turns SET started_at=now()-interval '11 minutes' WHERE id=%s", (turn_id,)
        )
    assert chat.messages(actor, conversation)["messages"][-1]["status"] == "failed"
    second = chat.begin(actor, conversation, turn_id, "保留同一条消息")
    chat.finish(actor, conversation, turn_id, "过期回复", attempt=first["attempt"])
    assert chat.messages(actor, conversation)["messages"][-1]["status"] == "pending"
    chat.finish(actor, conversation, turn_id, "重试回复", attempt=second["attempt"])
    assert chat.messages(actor, conversation)["messages"][-1]["content"] == "重试回复"


def test_retry_old_turn_excludes_later_messages_from_model_context(directory, monkeypatch):  # noqa: F811
    import json as json_module
    from contextlib import contextmanager

    import httpx

    from aether_platform.chat import Conversations

    db, _, _ = directory
    chat = Conversations(db)
    chat.migrate()
    actor = db.authenticate("https://id.test", "a1")
    conversation = chat.create(actor)["id"]
    first, later = str(uuid4()), str(uuid4())
    chat.begin(actor, conversation, first, "第一条问题")
    chat.finish(actor, conversation, first, "", "temporary error")
    chat.begin(actor, conversation, later, "后来的问题")
    chat.finish(actor, conversation, later, "后来的回答")
    retried = chat.begin(actor, conversation, first, "第一条问题")
    captured = []

    class ModelClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @contextmanager
        def stream(self, method, url, *, headers, json):
            captured.extend(json["messages"])
            assert "max_completion_tokens" in json
            assert json["stream"] is True
            yield httpx.Response(
                200,
                request=httpx.Request("POST", "https://model.test"),
                text="data: "
                + json_module.dumps(
                    {"choices": [{"delta": {"content": "第一条回答"}, "finish_reason": "stop"}]}
                )
                + "\n\ndata: [DONE]\n\n",
            )

    monkeypatch.setattr("aether_platform.chat.httpx.Client", ModelClient)
    chat.generate(
        actor,
        conversation,
        first,
        {"endpoint": "https://model.test", "model": "test", "api_key": "test"},
        retried["attempt"],
    )
    assert captured[-1] == {"role": "user", "content": "第一条问题"}
    assert not any("后来" in row["content"] for row in captured)
    assert chat.messages(actor, conversation)["messages"][1]["content"] == "第一条回答"
