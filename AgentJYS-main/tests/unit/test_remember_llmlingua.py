"""Source preserving token deletion, route selection and exact evidence binding."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aether_agent_memory.remember.basic.llmlingua import (
    CompressionView,
    LLMLinguaPreprocessor,
    labeled_view,
    should_precompress,
)
from aether_agent_memory.remember.basic.policy import RememberPolicy


def test_default_route_only_applies_to_individual_long_working():
    policy = RememberPolicy()
    assert policy.long_memory_route == "llmlingua"
    assert should_precompress(policy, "中" * 2667)
    assert not should_precompress(policy, "short " * 100)
    assert not any(should_precompress(policy, "short " * 100) for _ in range(32))
    assert not should_precompress(
        policy.model_copy(update={"long_memory_route": "direct"}), "x" * 9000
    )


def test_deleted_words_map_quote_to_exact_original_with_duplicate_occurrences():
    original = "Alice really uses Vim. Alice really uses Vim."
    words = [("Alice", 1), ("really", 0), ("uses", 1), ("Vim", 1), (".", 1)] * 2
    view = labeled_view(original, words)
    assert view.text == "Alice uses Vim. Alice uses Vim."
    assert view.evidence(original, "Alice uses Vim.") == (
        (0, 22, "Alice really uses Vim."),
        (23, 45, "Alice really uses Vim."),
    )
    with pytest.raises(ValueError):
        view.evidence(original, "   ")
    with pytest.raises(ValueError):
        view.evidence(original, "Alice uses Emacs.")


def test_normalized_or_unknown_token_alignment_fails_closed():
    with pytest.raises(ValueError, match="align"):
        labeled_view("Alice uses Vim", [("alice", 1), ("uses", 1), ("Vim", 1)])
    with pytest.raises(ValueError, match="align"):
        labeled_view("Alice uses Vim", [("[UNK]", 1)])


def test_unlabeled_punctuation_and_source_hash_are_preserved():
    original = "姓名：林澈\r\n预算18万元，不得超出。"
    view = labeled_view(
        original,
        [
            ("姓名", 1),
            ("林", 1),
            ("澈", 1),
            ("预算", 1),
            ("18", 1),
            ("万元", 1),
            ("不", 1),
            ("得", 1),
            ("超出", 1),
        ],
    )
    assert view.text == original
    saved = CompressionView.model_validate(view.model_dump(mode="json"))
    with pytest.raises(ValueError, match="source"):
        saved.evidence(original + "changed", "林澈")


def test_official_adapter_requests_labels_and_uses_no_llm():
    class Compressor:
        def compress_prompt(self, text, **kwargs):
            assert kwargs["rate"] == 0.8
            assert kwargs["force_reserve_digit"] is True
            assert kwargs["drop_consecutive"] is False
            return {
                "fn_labeled_original_prompt": kwargs["word_sep"].join(
                    word + kwargs["label_sep"] + str(label)
                    for word, label in [("Alice", 1), ("really", 0), ("uses", 1), ("Vim", 1)]
                )
            }

    adapter = LLMLinguaPreprocessor(RememberPolicy(), compressor=Compressor())
    assert adapter.compress("Alice really uses Vim").text == "Alice uses Vim"


async def test_mixed_sources_preprocess_only_long_source_and_reuse_range_checkpoint():
    from aether_agent_memory.remember.basic.candidate_consolidation import (
        _ExtractionPart,
        _precompressed_views,
    )

    from .test_official_langmem import snapshot
    from .test_remember_candidate_batching import Store

    long = snapshot("long", "Alice really uses Vim. " * 400)
    short = snapshot("short", "Bob uses Emacs.")
    text = "Alice really uses Vim. "
    processor = SimpleNamespace(
        model_identity={"test": "pinned-model"},
        acompress=AsyncMock(
            return_value=labeled_view(
                text,
                [
                    ("Alice", 1),
                    ("really", 0),
                    ("uses", 1),
                    ("Vim", 1),
                    (".", 1),
                ],
            )
        ),
    )
    from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
    from aether_agent_memory.runtime.foundation.requests import text_hash

    async def persist(ctx, scope, value):
        return ResourceLocation(
            kind="body",
            provider_id="local",
            provider_instance_id="test",
            namespace="remember_bodies",
            object_key="bodies/" + text_hash(value),
            generation=text_hash(value),
            content_hash=text_hash(value),
        )

    owner = SimpleNamespace(
        policy=RememberPolicy(),
        uow=Store(),
        llmlingua_preprocessor=processor,
        bodies=SimpleNamespace(persist=AsyncMock(side_effect=persist)),
        identity=SimpleNamespace(clock=lambda: "2026-10-08T00:00:00.000Z"),
        refkey=lambda ref: ref.model_dump_json(),
    )
    kwargs = {"ctx": None, "task": SimpleNamespace(task_id="task")}
    first = _ExtractionPart(
        (long, short), {"src_long": (0, len(text)), "src_short": (0, len(short.content))}
    )

    def guard(tx):
        return None

    views = await _precompressed_views(owner, first, guard, **kwargs)
    assert set(views) == {"src_long"}
    assert await _precompressed_views(owner, first, guard, **kwargs) == views
    assert processor.acompress.await_count == 1
    second = _ExtractionPart((long,), {"src_long": (len(text), 2 * len(text))})
    await _precompressed_views(owner, second, guard, **kwargs)
    assert processor.acompress.await_count == 2
    owner.policy = owner.policy.model_copy(update={"llmlingua_keep_rate": 0.7})
    await _precompressed_views(owner, second, guard, **kwargs)
    assert processor.acompress.await_count == 3
    from aether_agent_memory.remember.contracts.models import MemoryKind

    owner.policy = owner.policy.model_copy(update={"long_memory_route": "llmlingua"})
    episode = long.model_copy(update={"kind": MemoryKind.EPISODIC})
    assert (
        await _precompressed_views(
            owner, _ExtractionPart((episode,), {"src_long": (0, len(text))}), guard, **kwargs
        )
        == {}
    )
    assert processor.acompress.await_count == 3
    owner.policy = owner.policy.model_copy(update={"long_memory_route": "direct"})
    assert await _precompressed_views(owner, first, guard, **kwargs) == {}
    assert processor.acompress.await_count == 3
    ids = owner.uow.read("remember_precompression_manifest", owner.refkey(long.ref))
    assert len(ids) == 3
    record = owner.uow.read("remember_precompression_artifacts", ids[0])
    assert record["artifact"]["location"]["kind"] == "artifact"
    assert record["consumer"] == "langmem_candidate_extraction"
    assert record["counts_toward_final_compression_factor"] is False
    from aether_agent_memory.remember.basic.candidate_consolidation import (
        precompression_artifact_status,
    )

    owner.final_guard = lambda *args: SimpleNamespace(items=[SimpleNamespace(decision="allowed")])
    assert all(
        row["eligible"] for row in precompression_artifact_status(owner, owner.uow, None, long)
    )
    owner.final_guard = lambda *args: SimpleNamespace(items=[SimpleNamespace(decision="blocked")])
    assert not any(
        row["eligible"] for row in precompression_artifact_status(owner, owner.uow, None, long)
    )
    changed = long.model_copy(update={"ref": long.ref.model_copy(update={"version": 2})})
    assert all(
        row["ineligible_reason"] == "memory_version_changed"
        for row in precompression_artifact_status(owner, owner.uow, None, changed)
    )


def test_preprocessor_identity_changes_invalidate_parent_model_checkpoint():
    from aether_agent_memory.remember.basic.pipeline import checkpoint_provider_identity

    first = LLMLinguaPreprocessor(RememberPolicy())
    second = LLMLinguaPreprocessor(RememberPolicy(llmlingua_keep_rate=0.7))
    assert checkpoint_provider_identity(first) != checkpoint_provider_identity(second)
    injected = SimpleNamespace(model_identity={"model": "one"})
    before = checkpoint_provider_identity(injected)
    injected.model_identity = {"model": "two"}
    assert before != checkpoint_provider_identity(injected)


async def test_compressed_extraction_maps_to_original_and_stage2_only_sees_excerpt():
    from aether_agent_memory.remember.basic.official_langmem import (
        CandidateMemory,
        OfficialLangMemConsolidation,
    )

    from .test_official_langmem import Manager, row, snapshot

    original = "prefix. Alice really uses Vim. suffix."
    start, end = 8, 30
    source = snapshot("long", original)
    view = labeled_view(
        original[start:end], [("Alice", 1), ("really", 0), ("uses", 1), ("Vim", 1), (".", 1)]
    )
    fact = CandidateMemory(
        text="Alice uses Vim.",
        kind="semantic",
        evidence=[{"source_id": "src_long", "quote": "Alice uses Vim."}],
    )
    manager = Manager(lambda _: [row("candidate", fact)])
    adapter = OfficialLangMemConsolidation(manager, "test", extraction_manager=manager)
    result = await adapter.extract_candidates(
        None,
        (source,),
        "v1",
        source_ranges={"src_long": (start, end)},
        source_views={"src_long": view},
    )
    evidence = result.candidates[0].candidate.evidence[0]
    assert (evidence.start_char, evidence.end_char, evidence.quote) == (
        8,
        30,
        "Alice really uses Vim.",
    )
    assert "really" not in manager.calls[0]["messages"][0]["content"]
    decision = adapter.decision_payload(
        result.candidates, (), {result.candidates[0].candidate_id: ()}
    )
    assert "really" in decision["messages"][0]["content"]
    assert "prefix" not in decision["messages"][0]["content"]
    fact.evidence[0].start_char, fact.evidence[0].end_char = 0, 15
    with pytest.raises(ValueError):
        await adapter.extract_candidates(
            None,
            (source,),
            "v1",
            source_ranges={"src_long": (start, end)},
            source_views={"src_long": view},
        )


def test_constraint_alias_accepted_by_raw_guard_and_normalized():
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    from aether_agent_memory.remember.basic.official_langmem import (
        CandidateMemory,
        _ToolProposalGuard,
    )

    args = {
        "text": "Production data must stay private.",
        "kind": "semantic",
        "importance_category": "constraint",
        "evidence": [{"source_id": "s", "quote": "Production data must stay private."}],
    }
    guard = _ToolProposalGuard({}, None, schema=CandidateMemory, allow_updates=False)
    guard.on_llm_end(
        LLMResult(
            generations=[
                [
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[{"id": "call1", "name": "CandidateMemory", "args": args}],
                        )
                    )
                ]
            ]
        )
    )
    assert guard.error is None
    assert CandidateMemory.model_validate(args).importance_category == "explicit_constraint"


def test_final_factor_uses_whole_original_and_distinct_final_bodies_for_both_routes():
    from aether_agent_memory.remember.basic.consolidation_metrics import compression_metrics
    from aether_agent_memory.remember.contracts.models import MemoryKind

    from .test_official_langmem import snapshot

    original = snapshot("long", "原始记忆。" * 600)
    outputs = (
        snapshot("first", "林澈负责项目。", MemoryKind.SEMANTIC),
        snapshot("second", "预算上限18万元。", MemoryKind.SEMANTIC),
    )
    source = original.sources[0]
    associations = {
        item.ref.memory_id: {(source.source_id, source.source_version, source.content_hash)}
        for item in outputs
    }
    for route in ("direct", "llmlingua"):
        policy = RememberPolicy(long_memory_route=route)
        metrics = compression_metrics(
            (original,),
            (*outputs, outputs[0]),
            target_factor=policy.compression_target_ratio,
            long_input_bytes=policy.compression_min_bytes,
            complete=True,
            output_sources=associations,
        )
        whole = metrics["per_working_memory"][0]
        assert whole["compression_factor"] == len(original.content.encode("utf-8")) / sum(
            len(item.content.encode("utf-8")) for item in outputs
        )
        assert whole["result_memory_count"] == 2
        incomplete = compression_metrics(
            (original,),
            outputs,
            target_factor=5,
            long_input_bytes=8000,
            complete=False,
            output_sources=associations,
        )
        assert incomplete["compression_factor"] is None
        assert incomplete["target_met"] is None
