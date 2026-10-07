"""Admission purpose and precompression policy preserve the complete original."""

import asyncio

import pytest
from remember_helpers import app as app
from remember_helpers import context, source

from aether_agent_memory.remember.contracts.models import (
    CorrectionRequest,
    LifecycleRequest,
    RememberRequest,
    SourceSearchRequest,
    TextInput,
)
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError


def save_mode(app, mode="automatic"):
    text = "背景材料无需背诵。\n\n" * 500 + "霁川计划负责人是顾思远。"
    request = RememberRequest(
        source=source(),
        selection=ScopeSelector(session_id="sources"),
        content=TextInput(kind="text", text=text),
        memory_mode=mode,
    )
    return asyncio.run(app.remember.save(context(app), request)), text


def test_reference_only_searches_original_without_auto_work(app):
    receipt, text = save_mode(app, "reference_only")
    assert receipt.task_ids == ()
    with app.foundation.uow.transaction() as tx:
        pending = tx.read("remember_pending", receipt.memories[0].memory_id)
        assert pending["state"] == "reference_only"
        assert (
            tx.read("remember_source_policy", receipt.source.source_id)["memory_mode"]
            == "reference_only"
        )
    result = asyncio.run(
        app.remember.search_sources(
            context(app), SourceSearchRequest(sources=(receipt.source,), query="霁川 顾思远")
        )
    )
    assert "顾思远" in result.passages[0].content
    assert result.passages[0].start_char > 1000
    with pytest.raises(FoundationError):
        app.remember.reprocess(context(app), receipt.memories[0].memory_id)
    corrected = asyncio.run(
        app.remember.correct_async(
            context(app),
            receipt.memories[0].memory_id,
            CorrectionRequest(
                expected_version=1,
                content=text + "补充内容。",
                source=source("correction"),
                reason="correct reference",
            ),
        )
    )
    assert corrected.task_ids == ()
    assert app.remember.consolidate(context(app), ScopeSelector(session_id="sources")) == ()
    app.remember.periodic()
    app.remember.lifecycle(
        context(app),
        corrected.memories[0].memory_id,
        LifecycleRequest(expected_version=2, target="archived", reason="archive reference"),
    )
    app.remember.lifecycle(
        context(app),
        corrected.memories[0].memory_id,
        LifecycleRequest(expected_version=2, target="active", reason="restore reference"),
    )
    with app.foundation.uow.transaction() as tx:
        assert (
            tx.read("remember_source_policy", corrected.source.source_id)["memory_mode"]
            == "reference_only"
        )
        assert not tx.rows("tasks")


def test_precompression_disabled_schedules_full_original_directly(app):
    app.remember.policy = app.remember.policy.model_copy(update={"precompression_enabled": False})
    receipt, text = save_mode(app)
    with app.foundation.uow.transaction() as tx:
        pending = tx.read("remember_pending", receipt.memories[0].memory_id)
        assert pending["state"] == "scheduled"
        assert "compression_task_id" not in pending
        assert [tx.read("tasks", task)["record"]["kind"] for task in receipt.task_ids] == [
            "remember.extract"
        ]
        item = app.remember.current(tx, receipt.memories[0].memory_id)
        assert (
            tx.read("remember_working_representations", item.ref.memory_id)["representation"]
            == "source_reference"
        )
    inputs = asyncio.run(app.remember.source_access.originals(context(app), (item,)))
    assert inputs[0].content == text


def test_source_search_http_identity_limits_and_source_version(app):
    from fastapi.testclient import TestClient

    from aether_agent_memory.runtime.flows.http import create_app
    from temporal_test_support import http_execution

    receipt, _ = save_mode(app, "reference_only")
    http_execution(app, app.execution.endpoint)
    body = {"sources": [receipt.source.model_dump(mode="json")], "query": "霁川 顾思远"}
    endpoint = "/p3/remember/sources/search"
    with TestClient(create_app(app)) as client:
        assert client.post(endpoint, json=body).status_code == 401
        headers = {"Authorization": "Bearer alice"}
        reply = client.post(endpoint, json=body, headers=headers)
        assert reply.status_code == 200, reply.text
        assert reply.headers["cache-control"] == "no-store"
        assert "顾思远" in reply.json()["passages"][0]["content"]
        assert (
            client.post(endpoint, json=body, headers={"Authorization": "Bearer carol"}).status_code
            == 403
        )
        assert (
            client.post(endpoint, json={**body, "max_passages": 13}, headers=headers).status_code
            == 422
        )
        body["sources"][0]["source_version"] += 1
        assert client.post(endpoint, json=body, headers=headers).status_code >= 400


def test_source_search_revoked_during_real_original_io_returns_no_content(app):
    receipt, _ = save_mode(app, "reference_only")
    original = app.remember.source_access.read

    async def revoked(*args, **kwargs):
        result = await original(*args, **kwargs)
        with app.foundation.uow.transaction() as tx:
            row = tx.read("remember_sources", receipt.source.source_id)
            tx.write(
                "remember_sources",
                receipt.source.source_id,
                {**row, "valid": False, "revision": row["revision"] + 1},
            )
        return result

    app.remember.source_access.read = revoked
    with pytest.raises(FoundationError):
        asyncio.run(
            app.remember.search_sources(
                context(app), SourceSearchRequest(sources=(receipt.source,), query="顾思远")
            )
        )
