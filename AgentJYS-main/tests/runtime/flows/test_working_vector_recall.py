"""Working vector routing, readiness and source isolation through real local flows."""

import pytest
from remember_helpers import app as pipeline_app  # noqa: F401
from test_flows import app as app
from test_flows import context, drain, recall, save

from aether_agent_memory.recall.contracts.models import VectorSearchResult
from aether_agent_memory.runtime.contracts.models import ErrorCode, ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError


@pytest.mark.parametrize("fixture", ["app", "pipeline_app"])
def test_published_working_is_recalled_from_its_vector_and_source_isolated(request, fixture):
    host = request.getfixturevalue(fixture)
    receipt = save(host)
    drain(host)
    working = recall(host, sources="working", selection=ScopeSelector(session_id="session_1"))
    long_term = recall(host, sources="long_term")
    assert working.outcome == "available"
    assert {item.memory for group in working.groups for item in group.items} == set(
        receipt.memories
    )
    assert not set(receipt.memories).intersection(
        item.memory for group in long_term.groups for item in group.items
    )
    assert (
        recall(host, sources="working", selection=ScopeSelector(session_id="other")).outcome
        == "empty"
    )


def remove_generation_binding(app, monkeypatch, fields):
    original = app.vectors.search

    async def incomplete(ctx, request):
        result = await original(ctx, request)
        return result.model_copy(
            update={
                "candidates": tuple(
                    candidate.model_copy(
                        update={
                            "target": candidate.target.model_copy(
                                update={field: None for field in fields}
                            )
                        }
                    )
                    for candidate in result.candidates
                )
            }
        )

    monkeypatch.setattr(app.vectors, "search", incomplete)


@pytest.mark.parametrize("missing", [("generation",), ("body_hash",), ("generation", "body_hash")])
def test_basic_working_requires_published_generation_binding(app, monkeypatch, missing):
    save(app)
    drain(app)
    remove_generation_binding(app, monkeypatch, missing)
    with pytest.raises(FoundationError) as error:
        recall(app, sources="working", selection=ScopeSelector(session_id="session_1"))
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION
    with app.foundation.uow.transaction() as tx:
        assert all(row["pack"] is None for _, row in tx.rows("recall_requests"))


@pytest.mark.parametrize("generationless", [False, True])
def test_basic_working_manifest_changed_during_rerank_cannot_commit(
    app, monkeypatch, generationless
):
    from aether_agent_memory.recall.basic.config import RecallSettings

    receipt = save(app)
    drain(app)
    if generationless:
        remove_generation_binding(app, monkeypatch, ("generation", "body_hash"))
    app.recall.settings = RecallSettings(rerank_policy="required", reranker_model="fixture")

    class Invalidate:
        async def rerank(self, ctx, query, documents):
            with app.foundation.uow.transaction() as tx:
                item = app.remember.current(tx, receipt.memories[0].memory_id)
                app.remember.change(tx, item, projection_state="stale")
            return [1.0] * len(documents)

    app.recall.reranker = Invalidate()
    with pytest.raises(FoundationError) as error:
        recall(app, sources="working", selection=ScopeSelector(session_id="session_1"))
    assert error.value.code == (
        ErrorCode.CONTRACT_VIOLATION if generationless else ErrorCode.RESULT_INVALIDATED
    )


@pytest.mark.parametrize("fixture", ["app", "pipeline_app"])
def test_old_working_policy_result_is_explicitly_invalidated(request, fixture):
    host = request.getfixturevalue(fixture)
    save(host)
    drain(host)
    pack = recall(host, sources="working", selection=ScopeSelector(session_id="session_1"))
    with host.foundation.uow.transaction() as tx:
        row = tx.read("recall_requests", pack.recall_id)
        row["pack"]["policy_version"] = "old_working_lexical_policy"
        tx.write("recall_requests", pack.recall_id, row)
    with pytest.raises(FoundationError) as error:
        host.recall.result(context(host), pack.recall_id)
    assert error.value.code == ErrorCode.RESULT_INVALIDATED


def test_saved_working_without_projection_is_not_successful_empty_or_lexical(app):
    save(app, "当前位置是办公室")
    with pytest.raises(FoundationError) as error:
        recall(
            app,
            "当前位置是办公室",
            sources="working",
            selection=ScopeSelector(session_id="session_1"),
        )
    assert error.value.code in {ErrorCode.REQUEST_IN_PROGRESS, ErrorCode.DEPENDENCY_UNAVAILABLE}


def test_working_vector_dependency_failure_cannot_fall_back_to_matching_text(app, monkeypatch):
    save(app)
    drain(app)

    def unavailable(**kwargs):
        raise OSError("controlled Milvus search outage")

    monkeypatch.setattr(app.vectors.client, "search", unavailable)
    with pytest.raises(FoundationError) as error:
        recall(app, sources="working", selection=ScopeSelector(session_id="session_1"))
    assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE


def test_working_source_must_bind_task_or_session(app):
    save(app)
    drain(app)
    with pytest.raises(FoundationError) as error:
        recall(app, sources="working")
    assert error.value.code == ErrorCode.INVALID_ARGUMENT


@pytest.mark.parametrize("state", ["pending", "failed"])
@pytest.mark.parametrize("coverage", ["complete", "partial", "unavailable"])
def test_working_readiness_preserves_structured_vector_failure(app, monkeypatch, state, coverage):
    receipt = save(app)
    if state == "failed":
        with app.foundation.uow.transaction() as tx:
            item = app.remember.current(tx, receipt.memories[0].memory_id)
            app.remember.change(tx, item, projection_state="failed")

    async def unavailable(ctx, request):
        return VectorSearchResult(
            candidates=(),
            coverage=coverage,
            reason=None if coverage == "complete" else "backend_" + coverage,
        )

    monkeypatch.setattr(app.vectors, "search", unavailable)
    with pytest.raises(FoundationError) as error:
        recall(app, sources="working", selection=ScopeSelector(session_id="session_1"))
    expected = (
        ErrorCode.REQUEST_IN_PROGRESS
        if state == "pending" and coverage == "complete"
        else ErrorCode.DEPENDENCY_UNAVAILABLE
    )
    assert error.value.code == expected
    assert "index_" + state in str(error.value)
    if coverage != "complete":
        assert "backend_" + coverage in str(error.value)
