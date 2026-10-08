"""AET-21 budget behavior through real Recall planning/commit.

Azure/Temporal run A and RF. The existing strict B fixture supplies complete
bodies and relations; token counts use real library implementations.
"""

import asyncio

import pytest
import tiktoken
from test_flows import app as azure_app
from test_generation_assembly import assembly_setup
from test_recall_body import replace_body
from tokenizers import Tokenizer

from aether_agent_memory.recall.basic.tokenization import ModelTokenizer
from aether_agent_memory.recall.contracts.models import EmbeddingItem, EmbeddingResult
from aether_agent_memory.recall.embedding.spaces import EmbeddingSpaces
from aether_agent_memory.remember.contracts.models import ConflictGroup
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash
from recall_budget_support import write_hf_tokenizer

app = azure_app
pytestmark = pytest.mark.integration


def only_first(request):
    return request.model_copy(
        update={"long_term_search": request.long_term_search.model_copy(update={"memory_top_k": 1})}
    )


class HFQueryEncoder:
    """Controlled embedding provider that actually tokenizes its query with HF."""

    def __init__(self, path):
        self.tokenizer = Tokenizer.from_file(str(path))

    async def embed(self, ctx, request):
        return EmbeddingResult(
            operation_id=request.operation_id,
            usage=request.usage,
            model_space=request.model_space,
            dimensions=2,
            items=tuple(
                EmbeddingItem(
                    index=index,
                    input_hash=text_hash(text),
                    vector=(float(len(self.tokenizer.encode(text).ids)), 0.0),
                )
                for index, text in enumerate(request.texts)
            ),
        )


@pytest.mark.p0
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_rc_bud_01_exact_whole_rendered_pack_boundary(app, offset):
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)
    request = only_first(request)
    measured = asyncio.run(assembly.plan(ctx, request))
    encoding = tiktoken.get_encoding("o200k_base")
    total = len(encoding.encode(measured.rendered_context, disallowed_special=()))
    assert total > 1
    tight = request.model_copy(
        update={"recall_id": "boundary_request", "token_budget": total + offset}
    )
    if offset < 0:
        with pytest.raises(FoundationError) as error:
            asyncio.run(assembly.plan(ctx, tight))
        assert error.value.code == ErrorCode.BUDGET_TOO_SMALL
    else:
        plan = asyncio.run(assembly.plan(ctx, tight))
        assert plan.units[0].bodies == measured.units[0].bodies
        assert plan.units[0].primary_memories == measured.units[0].primary_memories
        assert plan.rendered_context == measured.rendered_context
        assert plan.tokens_used == total <= plan.request.token_budget


@pytest.mark.p0
def test_rc_bud_02_oversized_first_group_continues_with_safe_plan_without_degradation(app):
    ctx, assembly, body, request = assembly_setup(app, token_budget=128)
    replace_body(app, body, "m1", "最高排名但不能截断的完整正文。" * 1024)
    plan = asyncio.run(assembly.plan(ctx, request))
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]
    assert plan.units[0].bodies[0].content == body.bodies["m2"].content
    assert body.bodies["m1"].content not in plan.rendered_context
    assert plan.rendered_context.startswith("[1] ")
    assert plan.degradation_reasons == () and len(plan.skipped_group_ids) == 1
    expected = len(tiktoken.get_encoding("o200k_base").encode(plan.rendered_context))
    assert plan.tokens_used == expected <= 128


@pytest.mark.p0
def test_rc_bud_03_all_readable_complete_groups_too_large_are_budget_failure_not_empty(app):
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)
    baseline = asyncio.run(assembly.plan(ctx, request))
    assert [b.memory.memory_id for u in baseline.units for b in u.bodies] == ["m1", "m2"]
    assert all(b.outcome == "read" for u in baseline.units for b in u.bodies)
    tiny = request.model_copy(update={"recall_id": "all_too_large", "token_budget": 1})
    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, tiny))
    assert error.value.code == ErrorCode.BUDGET_TOO_SMALL


@pytest.mark.p0
def test_rc_bud_04_source_reference_and_delimiter_overhead_cannot_be_omitted(app):
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)
    request = only_first(request)
    plan = asyncio.run(assembly.plan(ctx, request))
    delivered = plan.units[0].bodies[0]
    for source in delivered.sources:
        assert f"{source.source_id}@{source.source_version}" in plan.rendered_context
    assert plan.rendered_context.startswith("[1] ")
    assert plan.rendered_context.endswith("\n")
    encoding = tiktoken.get_encoding("o200k_base")
    total = len(encoding.encode(plan.rendered_context, disallowed_special=()))
    body_only = len(encoding.encode(delivered.content, disallowed_special=()))
    assert total > body_only
    assert plan.tokens_used == total
    tight = request.model_copy(update={"recall_id": "body_only_budget", "token_budget": body_only})
    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, tight))
    assert error.value.code == ErrorCode.BUDGET_TOO_SMALL


@pytest.mark.p0
@pytest.mark.parametrize("fits_without", ["member", "explanation"])
def test_rc_bud_05_conflict_budget_cannot_split_members_or_drop_relationship_explanation(
    app, fits_without
):
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)
    relation = ConflictGroup(
        group_id="required_group",
        members=(body.snapshots["m1"].ref, body.snapshots["m3"].ref),
        explanation="这两个条件必须同时成立，不能只保留其中之一。" * 30,
    )
    body.conflicts = [relation]
    baseline = asyncio.run(assembly.plan(ctx, request))
    assert baseline.units[0].conflict == relation
    encoding = tiktoken.get_encoding("o200k_base")
    without_explanation = baseline.rendered_context.split(relation.explanation)[0]
    first_body = baseline.units[0].bodies[0]
    sources = ", ".join(f"{s.source_id}@{s.source_version}" for s in first_body.sources)
    standalone = "[1] " + first_body.content + "\nSources: " + sources + "\n"
    budget = len(encoding.encode(standalone if fits_without == "member" else without_explanation))
    assert 0 < budget < len(encoding.encode(without_explanation + relation.explanation + "\n"))
    tight = request.model_copy(update={"recall_id": "atomic_group_budget", "token_budget": budget})
    plan = asyncio.run(assembly.plan(ctx, tight))
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]
    assert plan.skipped_group_ids == (relation.group_id,)
    assert body.bodies["m1"].content not in plan.rendered_context
    assert body.bodies["m3"].content not in plan.rendered_context
    assert relation.explanation not in plan.rendered_context
    assert plan.degradation_reasons == ()
    assert plan.tokens_used == len(encoding.encode(plan.rendered_context)) <= budget


@pytest.mark.p0
@pytest.mark.parametrize("text", ["中文咖啡", "English coffee", "👩🏽‍💻🙂", "café e\u0301\n中文🙂"])
def test_rc_bud_06_unicode_pack_fits_token_boundary_even_when_bytes_exceed_it(app, text):
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)
    replace_body(app, body, "m1", text)
    request = only_first(request)
    baseline = asyncio.run(assembly.plan(ctx, request))
    total = len(tiktoken.get_encoding("o200k_base").encode(baseline.rendered_context))
    assert total < len(baseline.rendered_context.encode("utf-8"))
    tight = request.model_copy(update={"recall_id": "unicode_boundary", "token_budget": total})
    plan = asyncio.run(assembly.plan(ctx, tight))
    assert plan.tokens_used == total
    assert plan.units[0].bodies[0].content == text
    assert text in plan.rendered_context


@pytest.mark.p1
@pytest.mark.parametrize("pack_tokenizer", ["o200k_base", "huggingface"])
def test_rc_bud_07_pack_tokenizer_is_fixed_independently_of_embedding_configuration(
    app, tmp_path, pack_tokenizer
):
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)
    replace_body(app, body, "m1", "👩🏽‍💻🙂 中文 hello world")
    path = tmp_path / "pack_tokenizer.json"
    write_hf_tokenizer(path)
    # Publish a distinct fixture model space with a real HF query tokenizer.
    # The fixed query is one HF token, preserving the fixture's known vector order.
    space = assembly.candidates.spaces.resolve("test_space").model_copy(
        update={"model_space": "hf_embedding_space", "tokenizer_id": "fixture_hf_wordlevel"}
    )
    assembly.candidates.spaces = EmbeddingSpaces((space,))
    assembly.candidates.embedding = HFQueryEncoder(path)
    # The test-owned Milvus collection must use the same deployment binding.
    vector_provider = assembly.candidates.vectors.vectors
    vector_provider.model_space = space.model_space
    for manifest, _ in body.proofs.values():
        manifest.update(model_space=space.model_space, embedding_tokenizer=space.tokenizer_id)
    for name, snapshot in body.snapshots.items():
        body.snapshots[name] = snapshot.model_copy(update={"model_space": space.model_space})
    with app.foundation.uow.transaction() as tx:
        for key, row in tx.rows("generation_vectors"):
            row["hit"]["model_space"] = space.model_space
            tx.write("generation_vectors", key, row)
    app.recall.tokenizer = ModelTokenizer(pack_tokenizer, str(path))
    request = only_first(request)
    request = request.model_copy(
        update={
            "context_tokenizer": app.recall.tokenizer.identifier,
            "long_term_search": request.long_term_search.model_copy(
                update={"model_space": space.model_space}
            ),
        }
    )
    baseline = asyncio.run(assembly.plan(ctx, request))
    rendered = baseline.rendered_context
    o200k_total = len(tiktoken.get_encoding("o200k_base").encode(rendered))
    hf_total = len(Tokenizer.from_file(str(path)).encode(rendered, add_special_tokens=False).ids)
    assert o200k_total != hf_total
    expected = o200k_total if pack_tokenizer == "o200k_base" else hf_total
    assert baseline.request.context_tokenizer == app.recall.tokenizer.identifier
    assert all(b.guard is not None for u in baseline.units for b in u.bodies)
    assert baseline.tokens_used == expected
    for offset in (-1, 0, 1):
        tight = request.model_copy(
            update={
                "recall_id": "tokenizer_boundary_" + str(offset),
                "token_budget": expected + offset,
            }
        )
        if offset < 0:
            with pytest.raises(FoundationError) as error:
                asyncio.run(assembly.plan(ctx, tight))
            assert error.value.code == ErrorCode.BUDGET_TOO_SMALL
        else:
            plan = asyncio.run(assembly.plan(ctx, tight))
            assert plan.rendered_context == rendered and plan.tokens_used == expected


@pytest.mark.p1
def test_rc_bud_08_counting_failure_cannot_produce_a_deliverable_plan(app, monkeypatch):
    ctx, assembly, body, request = assembly_setup(app)

    def failed_count(self, text, **kwargs):
        raise RuntimeError("controlled tokenizer unavailable")

    monkeypatch.setattr(tiktoken.Encoding, "encode", failed_count)
    with pytest.raises(RuntimeError, match="controlled tokenizer unavailable"):
        asyncio.run(assembly.plan(ctx, request))
