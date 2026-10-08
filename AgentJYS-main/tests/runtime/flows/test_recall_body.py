"""AET-19 RC-BODY-01–08, using existing real Azure/Temporal fixtures.

Controlled B responses exercise the consumer; real Remember APIs exercise
lifecycle authority. Faults are injected only at provider/clock boundaries.
"""

import asyncio
import copy

import pytest
from remember_helpers import app as remember_app
from remember_helpers import context, drain, facts, save, source
from test_flows import app as planning_app
from test_generation_assembly import assembly_setup
from test_remember_lcm_complete import enroll

from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.basic.boundary import RememberBoundary
from aether_agent_memory.remember.contracts.foundation import FullBodyReadResult
from aether_agent_memory.remember.contracts.models import CorrectionRequest, DeleteRequest
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.requests import text_hash

pytestmark = [pytest.mark.integration, pytest.mark.p0]
app = planning_app
authority_app = remember_app


def replace_body(app, body, name, content):
    """Publish consistent independent B fixture evidence for a complete body."""
    digest = text_hash(content)
    for manifest, guard in body.proofs.values():
        if guard["memory"]["memory_id"] == name:
            manifest["body_hash"] = manifest["vector_location"]["content_hash"] = digest
            guard["body_hash"] = digest
    snapshot = body.snapshots[name]
    body.snapshots[name] = snapshot.model_copy(update={"content": content, "content_hash": digest})
    before = body.bodies[name]
    body.bodies[name] = FullBodyReadResult.model_validate(
        {
            **before.model_dump(),
            "content": content,
            "guard": before.guard.model_copy(update={"body_hash": digest}),
            "location": before.location.model_copy(update={"content_hash": digest}),
        }
    )
    with app.foundation.uow.transaction() as tx:
        for key, row in tx.rows("generation_vectors"):
            if row["hit"]["memory"]["memory_id"] == name:
                row["hit"]["body_hash"] = digest
                tx.write("generation_vectors", key, row)


class CaptureReranker:
    identifier = "fixture:full_body_capture"

    def __init__(self):
        self.documents = []

    async def rerank(self, ctx, query, documents):
        self.documents.extend(documents)
        return tuple(float(len(documents) - i) for i in range(len(documents)))


def use_reranker(app, scorer):
    app.recall.settings = RecallSettings(rerank_policy="required", reranker_model="fixture")
    app.recall.reranker = scorer


def test_rc_body_01_only_middle_chunk_hit_delivers_exact_complete_body(app):
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)
    text = "BEGIN\n权威正文开头。\nMIDDLE\n命中部分。\nEND\n权威正文结尾。"
    replace_body(app, body, "m1", text)
    # Extend the fixture to three chunks; only index 1 will reach memory TopK.
    for manifest, _ in list(body.proofs.values()):
        if manifest["memory"]["memory_id"] == "m1":
            last = copy.deepcopy(manifest["chunks"][0])
            last.update(chunk_index=2, vector_id=fingerprint(["last_chunk"]))
            manifest["chunks"].append(last)
            manifest["expected_chunk_count"] = 3
    with app.foundation.uow.transaction() as tx:
        rows = [
            (key, row)
            for key, row in tx.rows("generation_vectors")
            if row["hit"]["memory"]["memory_id"] == "m1"
        ]
        for key, row in rows:
            score = 0.99 if row["hit"]["chunk_index"] == 1 else 0.1
            row["hit"]["score"] = score
            row["vector"] = [score, 0.0]
            tx.write("generation_vectors", key, row)
        key, row = rows[0]
        last = copy.deepcopy(row)
        last_id = fingerprint(["last_chunk"])
        last["hit"].update(chunk_index=2, vector_id=last_id, score=0.05)
        last["vector"] = [0.05, 0.0]
        tx.write("generation_vectors", last_id, last)
    body.proofs[last_id] = copy.deepcopy(body.proofs[key])
    search = request.long_term_search.model_copy(update={"memory_top_k": 1, "chunk_page_size": 1})
    found = asyncio.run(assembly.candidates.search(ctx, search))
    assert len(found.candidates) == 1
    assert [h.chunk_index for h in found.candidates[0].hits] == [1]
    loaded = []
    original = body.load_bodies

    async def capture(ctx, refs):
        loaded.extend(refs)
        return await original(ctx, refs)

    body.load_bodies = capture
    scorer = CaptureReranker()
    use_reranker(app, scorer)
    plan = asyncio.run(assembly.plan(ctx, request.model_copy(update={"long_term_search": search})))
    delivered = plan.units[0].bodies[0]
    assert delivered.memory == found.candidates[0].memory
    assert loaded == [found.candidates[0].memory]
    assert delivered.content == text and delivered.guard.body_hash == text_hash(text)
    source_refs = ", ".join(f"{s.source_id}@{s.source_version}" for s in delivered.sources)
    assert plan.rendered_context == "[1] " + text + "\nSources: " + source_refs + "\n"
    assert scorer.documents == [text]


def test_rc_body_02_raw_index_payload_canary_never_replaces_authoritative_body(app, monkeypatch):
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)
    # Wrap the real Milvus SDK, preserving its actual targets and scores.
    client = assembly.candidates.vectors.vectors.client
    original = client.search
    injected = []

    def canary_search(*args, **kwargs):
        result = original(*args, **kwargs)
        for page in result:
            for hit in page:
                hit["entity"]["text"] = "INDEX_FAKE_BODY_CANARY"
                injected.append(hit["entity"]["target"])
        return result

    monkeypatch.setattr(client, "search", canary_search)
    scorer = CaptureReranker()
    use_reranker(app, scorer)
    plan = asyncio.run(assembly.plan(ctx, request))
    assert injected, "the payload injection must actually reach the search result"
    assert "INDEX_FAKE_BODY_CANARY" not in plan.rendered_context
    assert all("INDEX_FAKE_BODY_CANARY" not in d for d in scorer.documents)
    assert scorer.documents == [body.bodies[n].content for n in ("m1", "m2")]


@pytest.mark.parametrize("fault", ["content", "hash", "version", "id", "object_revision"])
def test_rc_body_04_wrong_body_binding_never_enters_plan_or_model(app, fault):
    ctx, assembly, body, request = assembly_setup(app)
    before = body.bodies["m1"]
    if fault == "content":
        changed = before.model_copy(update={"content": "CORRUPTED_CANARY"})
    elif fault == "hash":
        changed = before.model_copy(
            update={"guard": before.guard.model_copy(update={"body_hash": "f" * 64})}
        )
    elif fault in {"version", "id"}:
        ref = before.memory.model_copy(
            update={"version": before.memory.version + 1}
            if fault == "version"
            else {"memory_id": "other"}
        )
        changed = before.model_copy(
            update={"memory": ref, "guard": before.guard.model_copy(update={"memory": ref})}
        )
    else:
        changed = before.model_copy(
            update={
                "guard": before.guard.model_copy(
                    update={"object_revision": before.guard.object_revision + 1}
                )
            }
        )
    body.bodies["m1"] = changed
    scorer = CaptureReranker()
    use_reranker(app, scorer)
    if fault == "object_revision":
        plan = asyncio.run(assembly.plan(ctx, request))
        assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]
        assert "body_stale" in plan.degradation_reasons
        assert scorer.documents == [body.bodies["m2"].content]
    else:
        with pytest.raises(FoundationError) as error:
            asyncio.run(assembly.plan(ctx, request))
        assert error.value.code == ErrorCode.CONTRACT_VIOLATION
        assert scorer.documents == []


@pytest.mark.parametrize("outcome", ["missing", "unavailable", "excluded", "stale"])
def test_rc_body_05_only_complete_safe_material_can_survive_read_failure(app, outcome):
    ctx, assembly, body, request = assembly_setup(app)
    body.bodies["m1"] = FullBodyReadResult(
        memory=body.snapshots["m1"].ref,
        outcome=outcome,
        path="none",
        reason_code="controlled_read_failure",
    )
    plan = asyncio.run(assembly.plan(ctx, request))
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]
    assert body.snapshots["m1"].content not in plan.rendered_context
    assert plan.degradation_reasons == (() if outcome == "excluded" else ("body_" + outcome,))


def prepared_memory(app, text="我喜欢无糖咖啡"):
    receipt = save(app, text)
    drain(app)
    ref = facts(app, receipt)[0]
    return receipt, ref, RememberBoundary(app.remember)


def test_rc_body_03_correction_after_discovery_never_pairs_old_rank_with_new_body(
    authority_app, monkeypatch
):
    app = authority_app
    _, ref, boundary = prepared_memory(app)
    body_port = app.recall.assembly.bodies
    original = body_port.load_bodies
    observed = []
    new_text = "更正后用户只喝无糖红茶。"

    async def corrected_read(ctx, refs):
        assert ref in refs, "correction must happen after the old Ref was selected"
        item = app.remember.get(ctx, ref.memory_id)
        await app.remember.correct_async(
            ctx,
            ref.memory_id,
            CorrectionRequest(
                expected_version=item.ref.version,
                content=new_text,
                source=source("correction"),
                reason="correct the current fact",
            ),
        )
        result = await original(ctx, refs)
        observed.extend(result)
        return result

    monkeypatch.setattr(body_port, "load_bodies", corrected_read)
    try:
        pack = asyncio.run(
            app.recall.recall(context(app), RecallRequest(query="咖啡", sources="long_term"))
        )
    except FoundationError as error:
        assert error.code in {ErrorCode.RESULT_INVALIDATED, ErrorCode.DEPENDENCY_UNAVAILABLE}
    else:
        assert not pack.groups and new_text not in pack.rendered_context
    assert observed and all(r.outcome != "read" for r in observed if r.memory == ref)
    current = app.remember.get(context(app), ref.memory_id)
    assert current.ref.version > ref.version and current.content == new_text
    assert asyncio.run(boundary.load_bodies(context(app), (ref,)))[0].outcome != "read"


@pytest.mark.parametrize("fault", ["missing", "denied", "timeout", "corrupt"])
def test_rc_body_05_object_fault_cannot_read_stale_cache_or_report_normal_empty(
    authority_app, monkeypatch, fault
):
    app = authority_app
    _, ref, boundary = prepared_memory(app)
    before = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert before.outcome == "read"
    bodies = app.remember.bodies

    # Redis is an external replica: an unverified canary must not mask the
    # authority failure. The real object client is the only other fault seam.
    async def corrupt_cache(*args, **kwargs):
        return "STALE_CACHE_CANARY"

    monkeypatch.setattr(bodies.cache, "get", corrupt_cache)

    def object_failure(*args, **kwargs):
        if fault == "corrupt":
            from io import BytesIO

            raw = b"CORRUPTED_OBJECT_CANARY"
            return {
                "Body": BytesIO(raw),
                "ContentLength": len(raw),
                "ResponseMetadata": {"HTTPStatusCode": 200},
                "Metadata": {"sha256": text_hash(raw.decode("utf-8"))},
            }
        if fault in {"missing", "denied"}:
            from botocore.exceptions import ClientError

            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey" if fault == "missing" else "AccessDenied"},
                    "ResponseMetadata": {"HTTPStatusCode": 404 if fault == "missing" else 403},
                },
                "GetObject",
            )
        raise TimeoutError("controlled object timeout")

    monkeypatch.setattr(bodies.p2.transport.client, "get_object", object_failure)
    if fault == "corrupt":
        with pytest.raises(FoundationError) as error:
            asyncio.run(boundary.load_bodies(context(app), (ref,)))
        assert error.value.code == ErrorCode.CONTRACT_VIOLATION
    else:
        response = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
        assert response.outcome == "unavailable" and response.content is None
        assert response.reason_code == "body_dependency_unavailable"


def test_rc_body_06_no_default_ttl_remains_readable_across_domain_time_window(
    authority_app, monkeypatch
):
    app = authority_app
    _, ref, boundary = prepared_memory(app)
    item = app.remember.get(context(app), ref.memory_id)
    assert item.expires_at is None
    before = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    timestamp = later(app.foundation.identity.clock(), 86400 * 30)
    monkeypatch.setattr(app.foundation.identity, "clock", lambda: timestamp)
    after = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert after.outcome == "read" and after.content == before.content and after.memory == ref


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_rc_body_06_explicit_expiration_blocks_new_read_and_saved_result_at_boundary(
    authority_app, monkeypatch, offset
):
    app = authority_app
    _, ref, boundary = prepared_memory(app)
    expiry = later(app.foundation.identity.clock(), 60)
    enroll(app, ref, enabled=False, legal_hold=True, expires_at=expiry)
    pack = asyncio.run(
        app.recall.recall(context(app), RecallRequest(query="咖啡", sources="long_term"))
    )
    assert pack.groups
    timestamp = later(expiry, offset)
    monkeypatch.setattr(app.foundation.identity, "clock", lambda: timestamp)
    result = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    if offset < 0:
        assert result.outcome == "read"
        assert app.recall.result(context(app), pack.recall_id).groups
    else:
        assert result.outcome == "excluded" and result.reason_code == "expired"
        with pytest.raises(FoundationError) as error:
            app.recall.result(context(app), pack.recall_id)
        assert error.value.code in {ErrorCode.RESULT_INVALIDATED, ErrorCode.MEMORY_GONE}
        fresh = asyncio.run(
            app.recall.recall(context(app), RecallRequest(query="咖啡", sources="long_term"))
        )
        assert not fresh.groups


@pytest.mark.parametrize("change", ["delete", "revoke_source"])
def test_rc_body_07_logical_revocation_blocks_residual_vectors_cache_and_saved_result(
    authority_app, change
):
    app = authority_app
    receipt, ref, boundary = prepared_memory(app)
    ctx = context(app)
    pack = asyncio.run(app.recall.recall(ctx, RecallRequest(query="咖啡", sources="long_term")))
    assert pack.groups
    if change == "delete":
        item = app.remember.get(context(app), ref.memory_id)
        app.remember.delete(
            context(app),
            ref.memory_id,
            DeleteRequest(expected_revision=item.object_revision, reason="logical removal"),
        )
    else:
        src = app.remember.source(context(app), receipt.source.source_id)
        app.remember.revoke_source(
            context(app),
            src.source_id,
            DeleteRequest(expected_revision=src.source_version, reason="source withdrawn"),
        )
    # Do not drain: asynchronous index/cache cleanup has not been delivered.
    result = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert result.outcome == "excluded" and result.content is None
    with pytest.raises(FoundationError) as error:
        app.recall.result(context(app), pack.recall_id)
    assert error.value.code in {ErrorCode.RESULT_INVALIDATED, ErrorCode.MEMORY_GONE}
    fresh = asyncio.run(
        app.recall.recall(context(app), RecallRequest(query="咖啡", sources="long_term"))
    )
    assert not fresh.groups


@pytest.mark.parametrize("failure", ["rerank", "guard", "commit"])
def test_rc_body_08_successful_reads_survive_downstream_failure_without_packed_events(
    authority_app, monkeypatch, failure
):
    app = authority_app
    prepared_memory(app)
    observed = []

    def collect(tx, event):
        observed.append(event)

    app.foundation.events.subscribe("recall.access", "body_test_observer", collect)
    ctx = context(app)
    guards = app.recall.assembly.guards
    original_guard = guards.revalidate_context
    guard_calls = []

    def controlled_guard(tx, ctx, request):
        guard_calls.append(request)
        if failure == "guard" or failure == "commit" and len(guard_calls) > 1:
            raise FoundationError(
                ErrorCode.RESULT_INVALIDATED, "controlled current B guard failure"
            )
        return original_guard(tx, ctx, request)

    if failure != "rerank":
        monkeypatch.setattr(guards, "revalidate_context", controlled_guard)

    class Scorer(CaptureReranker):
        async def rerank(self, ctx, query, documents):
            if failure == "rerank":
                raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "controlled model failure")
            return await super().rerank(ctx, query, documents)

    scorer = Scorer()
    use_reranker(app, scorer)
    request = RecallRequest(query="咖啡", sources="long_term")
    with pytest.raises(FoundationError) as error:
        asyncio.run(app.recall.recall(ctx, request))
    assert error.value.code == (
        ErrorCode.DEPENDENCY_UNAVAILABLE if failure == "rerank" else ErrorCode.RESULT_INVALIDATED
    )
    # Deliver through the real event subscription API. No outbox table probing
    # or manual application of events is used to manufacture the observation.
    drain(app)
    relevant = [event for event in observed if event.trace_id == ctx.trace_id]
    assert relevant, "successful reads disappeared when the downstream stage failed"
    assert all(event.payload["stage"] == "read" for event in relevant)
    identities = [(event.payload["recall_id"], str(event.payload["memory"])) for event in relevant]
    assert len(identities) == len(set(identities)), "read facts must not be counted twice"
    recall_id = relevant[0].payload["recall_id"]
    status = app.recall.status(context(app), recall_id)
    assert status.state == "failed" and not status.result_available
    with pytest.raises(FoundationError):
        app.recall.result(context(app), recall_id)
    if failure == "guard":
        assert scorer.documents == []
    elif failure == "commit":
        assert scorer.documents and len(guard_calls) >= 2
