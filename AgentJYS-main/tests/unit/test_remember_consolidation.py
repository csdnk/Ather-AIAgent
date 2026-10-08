from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_official_langmem import snapshot

from aether_agent_memory.remember.basic.consolidation import prepare_consolidation
from aether_agent_memory.remember.basic.official_langmem import ConsolidationResult
from aether_agent_memory.remember.basic.policy import RememberPolicy


class Store:
    def __init__(self):
        self.rows = {}

    @contextmanager
    def transaction(self):
        yield self

    def read(self, table, key):
        return self.rows.get((table, key))

    def write(self, table, key, value):
        self.rows[(table, key)] = value


def owner():
    store = Store()
    result = ConsolidationResult(proposals=(), model_id="model", policy_version="p")
    return SimpleNamespace(
        uow=store,
        policy=RememberPolicy(),
        tokenizer=SimpleNamespace(count=len),
        extraction=SimpleNamespace(consolidate=AsyncMock(return_value=result)),
        related=AsyncMock(return_value=()),
        tasks=SimpleNamespace(guard=lambda *args: None),
        checkpoint_binding=lambda: "processing-v1",
        space_key=lambda scope: "space",
        refkey=lambda ref: ref.memory_id,
        comparison_source_binding=lambda tx, items: "sources-v1",
        consume_call=lambda task: None,
    )


async def test_empty_batch_does_not_read_history_or_invoke_model():
    app = owner()
    result = await prepare_consolidation(app, None, SimpleNamespace(task_id="t"), ())
    assert result["proposals"] == ()
    app.related.assert_not_awaited()
    app.extraction.consolidate.assert_not_awaited()


async def test_complete_messages_reach_official_manager_without_chunking():
    app = owner()
    text = "This is one complete message. " * 200
    message = snapshot("new", text)
    result = await prepare_consolidation(app, None, SimpleNamespace(task_id="t"), (message,))
    assert result["proposals"] == ()
    assert app.extraction.consolidate.call_args.args[1] == (message,)
    assert app.extraction.consolidate.call_args.args[1][0].content == text


async def test_context_budget_rejects_whole_input_instead_of_truncating():
    app = owner()
    message = snapshot("new", "a" * 40000)
    with pytest.raises(Exception, match="complete.*budget"):
        await prepare_consolidation(app, None, SimpleNamespace(task_id="t"), (message,))
    app.extraction.consolidate.assert_not_awaited()


async def test_confirmed_zero_output_is_checkpointed_but_changed_space_reruns():
    app = owner()
    message = snapshot("new", "An ordinary greeting.")
    task = SimpleNamespace(task_id="t")
    await prepare_consolidation(app, None, task, (message,))
    await prepare_consolidation(app, None, task, (message,))
    assert app.extraction.consolidate.await_count == 1
    app.uow.write("remember_space_seq", "space", 1)
    await prepare_consolidation(app, None, task, (message,))
    assert app.extraction.consolidate.await_count == 2


async def test_every_message_in_32_record_batch_participates_in_old_memory_discovery():
    app = owner()
    messages = tuple(snapshot(str(i), f"Fact from complete message {i}.") for i in range(32))
    await prepare_consolidation(app, None, SimpleNamespace(task_id="t"), messages)
    queried = {call.args[1].text for call in app.related.await_args_list}
    assert queried == {message.content for message in messages}


async def test_concurrent_insertion_during_discovery_invalidates_before_model_call():
    app = owner()

    async def concurrent_insert(*args):
        app.uow.write("remember_space_seq", "space", 1)
        return ()

    app.related.side_effect = concurrent_insert
    with pytest.raises(Exception, match="space changed during discovery"):
        await prepare_consolidation(
            app, None, SimpleNamespace(task_id="t"), (snapshot("new", "Fact"),)
        )
    app.extraction.consolidate.assert_not_awaited()


def artifact(app, message, text):
    from aether_agent_memory.runtime.foundation.requests import text_hash

    app.uow.write(
        "remember_artifacts",
        message.ref.memory_id,
        {
            "memory": message.ref.model_dump(mode="json"),
            "source_hash": message.content_hash,
            "published": True,
            "declared_use": "supported_summary",
            "location": {
                "kind": "artifact",
                "provider_id": "object",
                "provider_instance_id": "obj",
                "namespace": "n",
                "object_key": "view",
                "generation": "one",
                "content_hash": text_hash(text),
            },
        },
    )
    app.bodies = SimpleNamespace(read=AsyncMock(return_value=(text, None)))


async def test_invalid_compressed_quote_retries_once_with_complete_original():
    from aether_agent_memory.remember.basic.extraction import EvidenceValidationError

    app = owner()
    message = snapshot("new", "Alice said her preferred operating system is Linux.")
    artifact(app, message, "Alice prefers Linux.")
    success = app.extraction.consolidate.return_value
    app.extraction.consolidate.side_effect = [
        EvidenceValidationError("quote_not_in_source", "src_new", "Alice prefers Linux.", "fact"),
        success,
    ]
    await prepare_consolidation(app, None, SimpleNamespace(task_id="t"), (message,))
    assert app.extraction.consolidate.await_count == 2
    assert app.extraction.consolidate.call_args.kwargs["representations"] == []
    assert app.extraction.consolidate.call_args.args[1] == (message,)


async def test_original_fallback_obeys_budget_without_second_model_call():
    from aether_agent_memory.remember.basic.extraction import EvidenceValidationError

    app = owner()
    message = snapshot("new", "a" * 40000)
    artifact(app, message, "compressed")
    app.extraction.consolidate.side_effect = EvidenceValidationError(
        "quote_not_in_source", "src_new", "compressed", "fact"
    )
    with pytest.raises(Exception, match="complete.*budget"):
        await prepare_consolidation(app, None, SimpleNamespace(task_id="t"), (message,))
    assert app.extraction.consolidate.await_count == 1


async def test_large_processed_overlap_uses_qualified_representation():
    app = owner()
    large = snapshot("large", "x" * 40000)
    artifact(app, large, "Complete compressed context.")
    app.uow.write("remember_batches", "t", {"context_refs": [large.ref.model_dump(mode="json")]})
    app.load_async = AsyncMock(return_value=SimpleNamespace(items=(large,)))
    app.source_access = SimpleNamespace(originals=AsyncMock(return_value=(large,)))
    result = await prepare_consolidation(
        app, None, SimpleNamespace(task_id="t"), (snapshot("new", "Hi"),)
    )
    assert result["context_refs"] == (large.ref,)
    assert app.extraction.consolidate.call_args.kwargs["representations"] == [
        {"source_id": "src_large", "text": "Complete compressed context."}
    ]


async def test_large_old_evidence_resolves_original_working_artifact():
    from aether_agent_memory.remember.contracts.models import MemoryKind

    app = owner()
    large = snapshot("large", "x" * 40000)
    old = snapshot("old", "A durable fact.", MemoryKind.SEMANTIC).model_copy(
        update={"sources": large.sources}
    )
    evidence = old.model_copy(update={"content": large.content, "content_hash": large.content_hash})
    artifact(app, large, "Old authorized evidence.")
    app.uow.write("remember_sources", "src_large", {"working_id": "large"})
    app.current = lambda tx, key: large
    app.related.return_value = (old,)
    app.source_access = SimpleNamespace(originals=AsyncMock(return_value=(evidence,)))
    result = await prepare_consolidation(
        app, None, SimpleNamespace(task_id="t"), (snapshot("new", "Hi"),)
    )
    assert result["existing"] == (old,)
    assert app.extraction.consolidate.call_args.kwargs["representations"] == [
        {"source_id": "src_large", "text": "Old authorized evidence."}
    ]


async def test_oversized_optional_old_evidence_does_not_block_small_new_message():
    from aether_agent_memory.remember.contracts.models import MemoryKind

    app = owner()
    old = snapshot("old", "A durable fact.", MemoryKind.SEMANTIC)
    large = snapshot("original", "x" * 40000)
    app.related.return_value = (old,)
    app.source_access = SimpleNamespace(originals=AsyncMock(return_value=(large,)))
    result = await prepare_consolidation(
        app, None, SimpleNamespace(task_id="t"), (snapshot("new", "Hi"),)
    )
    assert result["existing"] == (old,)
    assert app.extraction.consolidate.call_args.kwargs["existing_evidence"] == ()
    assert result["discovery"]["omitted_evidence_sources"] == ["src_original"]


async def test_invalid_original_quote_does_not_loop_after_fallback():
    from aether_agent_memory.remember.basic.extraction import EvidenceValidationError

    app = owner()
    message = snapshot("new", "Original sentence.")
    artifact(app, message, "Compressed sentence.")
    app.extraction.consolidate.side_effect = EvidenceValidationError(
        "quote_not_in_source", "src_new", "invented", "fact"
    )
    with pytest.raises(EvidenceValidationError):
        await prepare_consolidation(app, None, SimpleNamespace(task_id="t"), (message,))
    assert app.extraction.consolidate.await_count == 2


async def test_unknown_source_is_rejected_without_compression_fallback():
    from aether_agent_memory.remember.basic.extraction import EvidenceValidationError

    app = owner()
    message = snapshot("new", "Original sentence.")
    artifact(app, message, "Compressed sentence.")
    app.extraction.consolidate.side_effect = EvidenceValidationError(
        "unknown_source_id", "forged", "invented", "fact"
    )
    with pytest.raises(EvidenceValidationError):
        await prepare_consolidation(app, None, SimpleNamespace(task_id="t"), (message,))
    assert app.extraction.consolidate.await_count == 1


async def test_oversized_optional_context_is_omitted_as_a_complete_unit():
    app = owner()
    large = snapshot("large", "x" * 40000)
    app.uow.write("remember_batches", "t", {"context_refs": [large.ref.model_dump(mode="json")]})
    app.load_async = AsyncMock(return_value=SimpleNamespace(items=(large,)))
    app.source_access = SimpleNamespace(originals=AsyncMock(return_value=(large,)))
    result = await prepare_consolidation(
        app, None, SimpleNamespace(task_id="t"), (snapshot("new", "Hi"),)
    )
    assert result["context_refs"] == ()
    assert app.extraction.consolidate.call_args.kwargs["context_items"] == ()


def support_owner(rate, *, exact=False):
    from aether_agent_memory.remember.basic.comparison import ComparisonDecision
    from aether_agent_memory.remember.basic.official_langmem import ConsolidationProposal
    from aether_agent_memory.remember.basic.pipeline import RememberPipeline
    from aether_agent_memory.remember.contracts.models import CandidateFact, FactEvidence

    app = owner()
    app.policy = RememberPolicy(memory_support_sample_rate=rate)
    app.support_verifier = SimpleNamespace(verify=AsyncMock(return_value=True))
    app.validate_candidate = RememberPipeline.validate_candidate.__get__(app)
    message = snapshot("new", "Alice said she uses Linux for work.")
    candidate = CandidateFact(
        text=message.content if exact else "Alice uses Linux for work.",
        sources=message.sources,
        evidence_status="supported",
        evidence=(
            FactEvidence(
                source=message.sources[0],
                start_char=0,
                end_char=len(message.content),
                quote=message.content,
            ),
        ),
    )
    app.extraction.consolidate.return_value = ConsolidationResult(
        proposals=(
            ConsolidationProposal(
                candidate=candidate,
                decision=ComparisonDecision(outcome="create", reason="new fact"),
            ),
        ),
        model_id="model",
        policy_version="p",
    )
    return app, message


@pytest.mark.parametrize("rate", [0, 1])
async def test_official_extra_support_review_is_disabled_even_for_old_rate_one(rate):
    app, message = support_owner(rate)
    task = SimpleNamespace(task_id="sample-task")
    first = await prepare_consolidation(app, None, task, (message,))
    second = await prepare_consolidation(app, None, task, (message,))
    assert len(first["candidates"]) == 1
    assert second["support_audit"] == first["support_audit"]
    assert first["support_audit"][0]["status"] == "not_checked"
    assert first["support_audit"][0]["supported"] is None
    assert first["support_audit"][0]["reason"] == "additional_entailment_review_disabled"
    app.support_verifier.verify.assert_not_awaited()
    assert not any(table == "remember_quality_samples" for table, _ in app.uow.rows)


async def test_disabling_review_does_not_relabel_historical_rejection():
    app, message = support_owner(1)
    task = SimpleNamespace(task_id="t")
    await prepare_consolidation(app, None, task, (message,))
    table, key = next(
        (table, key) for table, key in app.uow.rows if table == "remember_official_support"
    )
    app.uow.write(table, key, {"supported": False})
    result = await prepare_consolidation(app, None, task, (message,))
    assert result["candidates"] == ()
    assert result["support_audit"][0]["status"] == "rejected"
    assert len(result["rejections"]) == 1
    app.support_verifier.verify.assert_not_awaited()


async def test_unavailable_extra_reviewer_does_not_block_consolidation():
    app, message = support_owner(1)
    app.support_verifier.verify.side_effect = AssertionError("removed extra review invoked")
    task = SimpleNamespace(task_id="t")
    result = await prepare_consolidation(app, None, task, (message,))
    assert result["support_audit"][0]["supported"] is None
    assert app.extraction.consolidate.await_count == 1
    app.support_verifier.verify.assert_not_awaited()


async def test_exact_source_quote_records_program_check_without_a_model_verdict():
    app, message = support_owner(1, exact=True)
    result = await prepare_consolidation(app, None, SimpleNamespace(task_id="t"), (message,))
    app.support_verifier.verify.assert_not_awaited()
    assert result["support_audit"][0]["status"] == "not_checked"
    assert result["support_audit"][0]["source_check"] == "exact_quote"
    assert result["support_audit"][0]["supported"] is None


async def test_disabled_extra_review_still_requires_exact_original_evidence():
    app, message = support_owner(0)
    proposal = app.extraction.consolidate.return_value.proposals[0]
    forged = proposal.candidate.evidence[0].model_copy(update={"quote": "x" * len(message.content)})
    forged_proposal = proposal.model_copy(
        update={"candidate": proposal.candidate.model_copy(update={"evidence": (forged,)})}
    )
    app.extraction.consolidate.return_value = app.extraction.consolidate.return_value.model_copy(
        update={"proposals": (forged_proposal,)}
    )
    with pytest.raises(Exception, match="evidence slice mismatch"):
        await prepare_consolidation(app, None, SimpleNamespace(task_id="t"), (message,))
    app.support_verifier.verify.assert_not_awaited()


async def test_unreviewed_extraction_view_is_used_without_full_original_model_input():
    app = owner()
    message = snapshot("new", "a" * 40000)
    artifact(app, message, "Selected source sentence.")
    app.uow.read("remember_artifacts", message.ref.memory_id)["declared_use"] = "extraction_view"
    await prepare_consolidation(app, None, SimpleNamespace(task_id="t"), (message,))
    assert app.extraction.consolidate.call_args.kwargs["representations"] == [
        {"source_id": "src_new", "text": "Selected source sentence."}
    ]


@pytest.mark.parametrize("rate", [0, 1])
async def test_legacy_extraction_also_disables_extra_support_review(rate):
    from aether_agent_memory.remember.basic.pipeline import RememberPipeline

    app, message = support_owner(rate)
    candidate = app.extraction.consolidate.return_value.proposals[0].candidate
    app.extraction.extract_batch = AsyncMock()
    app.extract_inputs = AsyncMock(return_value=[candidate])
    task = SimpleNamespace(task_id="legacy", kind="remember.extract")
    first = await RememberPipeline.candidates(app, None, task, (message,))
    second = await RememberPipeline.candidates(app, None, task, (message,))
    assert first == second == (candidate,)
    app.support_verifier.verify.assert_not_awaited()
    app.extract_inputs.assert_awaited_once()
    audit = app.uow.read("remember_candidates", "legacy")["support_audit"][0]
    assert audit["status"] == "not_checked"
    assert audit["supported"] is None


@pytest.mark.parametrize(
    "table,saved",
    [
        ("remember_official_support", {"supported": False}),
        ("remember_support_verifications", {"status": "rejected"}),
    ],
)
async def test_old_failed_support_verdict_is_not_bypassed_by_disabling_sampling(table, saved):
    from aether_agent_memory.remember.basic.consolidation import candidate_support_audit

    app, _ = support_owner(0)
    candidate = app.extraction.consolidate.return_value.proposals[0].candidate
    app.uow.write(table, "checkpoint", saved)
    audit = await candidate_support_audit(
        app,
        None,
        SimpleNamespace(task_id="t"),
        candidate,
        binding="binding",
        table=table,
        key="checkpoint",
    )
    assert audit["status"] == "rejected"
    assert audit["supported"] is False
    app.support_verifier.verify.assert_not_awaited()
