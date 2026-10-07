"""Chat files use persisted document receipts, never client-supplied extracted text."""

import json
from unittest.mock import Mock

import pytest
from psycopg.types.json import Jsonb
from test_directory import directory  # noqa: F401

from aether_platform.chat import Conversations
from aether_platform.memory import MemoryService
from aether_platform.p3 import P3Error


def uploaded(db, actor, upload_id="upload1"):
    MemoryService(db)
    scope = dict(
        tenant_id=actor.tenant_id,
        user_id=actor.id,
        application_id="agent-platform",
        agent_id="aether",
    )
    ref = dict(memory_id="memory1", version=1, scope=scope)
    source = dict(source_id="source1", source_version=1, content_hash="a" * 64, locator="document")
    receipt = dict(saved=True, memories=[ref], source=source)
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO memory_commands(id,user_id,tenant_id,action,fingerprint,result) "
            "VALUES (%s,%s,%s,'document','test',%s)",
            ("document_" + upload_id, actor.id, actor.tenant_id, Jsonb(receipt)),
        )
    return receipt


def test_turn_binds_owned_document_receipts_and_retries_exactly(directory):  # noqa: F811
    db, _, _ = directory
    chat = Conversations(db)
    chat.migrate()
    actor = db.authenticate("https://id.test", "a1")
    uploaded(db, actor)
    cid = chat.create(actor)["id"]
    attachments = [dict(upload_id="upload1", name="计划.pdf")]
    chat.begin(actor, cid, "turn1", "何时交付？", attachments=attachments)
    assert chat.messages(actor, cid)["messages"][0]["attachments"] == attachments
    assert not chat.begin(actor, cid, "turn1", "何时交付？", attachments=attachments)["started"]
    with pytest.raises(ValueError):
        chat.begin(actor, cid, "turn1", "何时交付？", attachments=[])
    other = db.authenticate("https://id.test", "a2")
    with pytest.raises(ValueError):
        chat.begin(other, chat.create(other)["id"], "turn2", "何时交付？", attachments=attachments)


def test_document_context_reads_p3_source_even_without_recall_hits():
    from aether_platform.chat_documents import read_attachment, validate_attachment

    scope = dict(tenant_id="a", user_id="u", application_id="agent-platform", agent_id="aether")
    ref = dict(memory_id="m", version=1, scope=scope)
    source = dict(source_id="s", source_version=1, content_hash="a" * 64, locator="document")
    binding = dict(upload_id="up", name="计划.pdf", memory=ref, source=source)
    p3 = Mock(actor=Mock(id="u", tenant_id="a"))

    def call(method, path, **kw):
        if path == "/p3/remember/m":
            return dict(ref=ref, status="active", sources=[source], object_revision=1)
        if path == "/p3/sources/s":
            return dict(ref=source, valid=True, scope=scope)
        assert path == "/p3/sources/read-range"
        return dict(source=source, content="周五交付", total_chars=4, is_complete=True)

    p3.call.side_effect = call
    content, evidence = read_attachment(p3, binding)
    assert "周五交付" in content and not evidence["truncated"]
    # P3's asynchronous summary increments Working version without changing source.
    p3.call.side_effect = lambda method, path, **kw: (
        dict(ref={**ref, "version": 2}, status="active", sources=[source])
        if path == "/p3/remember/m"
        else call(method, path, **kw)
    )
    assert "周五交付" in read_attachment(p3, binding)[0]
    p3.call.side_effect = lambda *a, **kw: dict(ref=ref, status="deleted", sources=[source])
    with pytest.raises(P3Error, match="RESULT_INVALIDATED"):
        validate_attachment(p3, evidence)


def test_conversation_attachment_survives_text_history_window():
    from aether_platform.chat_documents import recent_attachments

    attachment = {"upload_id": "original", "name": "计划.pdf"}
    rows = [{"role": "user", "attachments": [attachment]}]
    rows += [{"role": "user", "attachments": []}, {"role": "assistant"}] * 20
    assert recent_attachments(rows) == [attachment]


@pytest.mark.parametrize("code", ["MEMORY_GONE", "NOT_FOUND", "FORBIDDEN"])
def test_revoked_attachment_is_normalized_to_invalidation(code):
    from aether_platform.chat_documents import validate_attachment

    p3 = Mock()
    p3.call.side_effect = P3Error(code)
    with pytest.raises(P3Error, match="RESULT_INVALIDATED"):
        validate_attachment(p3, {"memory": {"memory_id": "m"}, "source": {}})


def test_unconfirmed_upload_and_cross_tenant_receipt_are_rejected(directory):  # noqa: F811
    from aether_platform.chat_documents import bind_attachments

    db, _, _ = directory
    actor = db.authenticate("https://id.test", "a1")
    uploaded(db, actor)
    with db.connection() as conn:
        conn.execute("UPDATE memory_commands SET result=NULL WHERE id='document_upload1'")
        with pytest.raises(ValueError, match="not confirmed"):
            bind_attachments(conn, actor, [{"upload_id": "upload1", "name": "a.pdf"}])
        conn.execute("UPDATE memory_commands SET tenant_id='tenant_b'")
        with pytest.raises(ValueError, match="not confirmed"):
            bind_attachments(conn, actor, [{"upload_id": "upload1", "name": "a.pdf"}])


@pytest.mark.parametrize("revoke", [False, True])
@pytest.mark.parametrize("recall_error", [None, "EXECUTION_INTERRUPTED", "AUTHENTICATION_REQUIRED"])
def test_model_reads_document_source_with_empty_recall_and_clears_revoked_output(
    directory,  # noqa: F811
    monkeypatch,
    revoke,
    recall_error,
):
    import httpx
    from test_chat_streaming import SETTINGS, model_stream

    db, _, _ = directory
    chat = Conversations(db)
    chat.migrate()
    actor = db.authenticate("https://id.test", "a1")
    receipt = uploaded(db, actor)
    cid = chat.create(actor)["id"]
    attachments = [{"upload_id": "upload1", "name": "计划.pdf"}]
    chat.begin(actor, cid, "turn1", "何时交付？", attachments=attachments)
    state = {"revoked": False}

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
            if recall_error:
                raise P3Error(recall_error)
            return {"recall_id": "empty_recall"}

        def call(self, method, path, **kwargs):
            if path.startswith("/p3/recalls/"):
                return dict(
                    recall_id="empty_recall",
                    outcome="empty",
                    groups=[],
                    rendered_context="",
                    degradation_reasons=[],
                )
            if state["revoked"]:
                raise P3Error("MEMORY_GONE")
            if path.startswith("/p3/remember/"):
                return dict(
                    ref=receipt["memories"][0], status="active", sources=[receipt["source"]]
                )
            assert path == "/p3/sources/read-range"
            return dict(
                source=receipt["source"],
                content="周五交付" + "正文" * 7000,
                total_chars=14004,
                is_complete=True,
            )

    monkeypatch.setattr("aether_platform.chat.P3Client", P3)
    monkeypatch.setattr(chat, "save_pending", Mock())
    model_stream(monkeypatch, lambda: state.update(revoked=revoke))
    stream_client = httpx.Client
    captured = []

    def capture(**kwargs):
        client = stream_client(**kwargs)
        client.event_hooks["request"].append(
            lambda request: captured.append(json.loads(request.content))
        )
        return client

    monkeypatch.setattr("aether_platform.chat.httpx.Client", capture)
    chat.generate(actor, cid, "turn1", SETTINGS, p3_settings={"base_url": "http://127.0.0.1:19020"})
    if recall_error == "AUTHENTICATION_REQUIRED":
        assert not captured
        assert chat.messages(actor, cid)["messages"][-1]["status"] == "failed"
        return
    assert "周五交付" in captured[0]["messages"][0]["content"]
    assert captured[0]["messages"][-1]["content"] == "何时交付？"
    messages = Conversations(db).messages(actor, cid)["messages"]
    assert messages[0]["attachments"] == attachments
    if revoke:
        assert messages[-1]["status"] == "failed" and messages[-1]["content"] == ""
        assert messages[-1]["memory_evidence"] is None
    else:
        assert messages[-1]["status"] == "complete"
        if recall_error:
            assert messages[-1]["memory_evidence"]["outcome"] == "unavailable"
            assert messages[-1]["memory_evidence"]["recall_id"] is None
            assert "历史记忆检索暂未完成" in captured[0]["messages"][0]["content"]
        assert messages[-1]["attachment_evidence"][0]["truncated"] is True
        assert messages[-1]["attachment_evidence"][0]["included_chars"] == 12000
