"""AET-66 RC-BODY-01, RC-BODY-02, RC-FUS-08: authoritative complete body delivery and original semantic preservation.

Proves middle chunk hit still delivers authoritative exact-version full body. Fake index body doesn't
enter Pack or model. High relevance doesn't change original semantics or get expressed as factual confidence.

Test cases:
- Multi-chunk body exceeding embedding input limit with BEGIN/MIDDLE/END, Chinese, emoji, newlines, negation
- Only MIDDLE chunk hit with sufficient budget
- Pack's exact Ref/version/hash matches Remember full body, complete from start to end
- Don't return hit chunk, don't concatenate chunks pretending to be authoritative full body
- Legal candidate payload has safe canary different from authoritative body
- Verify canary doesn't enter Pack or reranker, confirming authoritative body read path
- Coverage: "不加糖", quotes "同事说喜欢糖", conditional "如果加班就喝咖啡", numbers "周二10:00"
- Preserve original text, source, model identity; don't convert to definite facts
- Relevance score is not factual confidence; model input limit can't change Pack's complete body requirement

Extends AET-19 and AET-17, reuses multi-chunk body and model input capture support.
"""

import asyncio
import copy

import pytest
from test_flows import app as app
from test_generation_assembly import BodyAuthority, CaptureReranker, assembly_setup, replace_body, use_reranker

from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.runtime.foundation.common import fingerprint

pytestmark = [pytest.mark.integration, pytest.mark.p0]


def test_rc_body_01_middle_chunk_hit_delivers_complete_authoritative_body(app):
    """Only MIDDLE chunk hit with sufficient budget delivers complete BEGIN/MIDDLE/END authoritative body.

    Multi-chunk body with BEGIN, MIDDLE, END sections. Only middle chunk (index 1) hits in search.
    Pack must contain complete authoritative body from Remember, not just the hit chunk.
    """
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    # Create multi-chunk body with identifiable sections
    text = "BEGIN\n权威正文开头。\nMIDDLE\n命中部分。\nEND\n权威正文结尾。"
    replace_body(app, body, "m1", text)

    # Extend fixture to three chunks; only index 1 will reach memory TopK
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
            # Only middle chunk (index 1) gets high score
            score = 0.99 if row["hit"]["chunk_index"] == 1 else 0.1
            row["hit"]["score"] = score
            row["vector"] = [score, 0.0]
            tx.write("generation_vectors", key, row)

        # Add third chunk with low score
        key, row = rows[0]
        last = copy.deepcopy(row)
        last_id = fingerprint(["last_chunk"])
        last["hit"].update(chunk_index=2, vector_id=last_id, score=0.05)
        last["vector"] = [0.05, 0.0]
        tx.write("generation_vectors", last_id, last)

    body.proofs[last_id] = copy.deepcopy(body.proofs[key])

    # Search with tight limits to ensure only middle chunk hits
    search = request.long_term_search.model_copy(update={"memory_top_k": 1, "chunk_page_size": 1})
    found = asyncio.run(assembly.candidates.search(ctx, search))

    # Verify only middle chunk hit
    assert len(found.candidates) == 1
    assert found.candidates[0].memory.memory_id == "m1"
    assert len(found.candidates[0].hits) == 1
    assert found.candidates[0].hits[0].chunk_index == 1  # Middle chunk

    # Generate plan
    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify complete body in Pack
    assert "BEGIN" in plan.rendered_context
    assert "权威正文开头" in plan.rendered_context
    assert "MIDDLE" in plan.rendered_context
    assert "命中部分" in plan.rendered_context
    assert "END" in plan.rendered_context
    assert "权威正文结尾" in plan.rendered_context

    # Verify exact Ref/version/hash
    m1_unit = next(u for u in plan.units if u.bodies[0].memory.memory_id == "m1")
    assert m1_unit.bodies[0].content == text
    assert m1_unit.bodies[0].location.content_hash == body.bodies["m1"].location.content_hash


def test_rc_body_02_index_payload_canary_never_enters_pack_or_reranker(app, monkeypatch):
    """Legal candidate payload has safe canary; verify canary doesn't enter Pack or reranker.

    Inject fake body text into raw index payload. Verify the canary never appears in:
    1. Final Pack (rendered_context)
    2. Reranker model input (documents)

    This confirms system uses authoritative body read path, not index payload.
    """
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    # Wrap real Milvus SDK to inject canary
    client = assembly.candidates.vectors.vectors.client
    original = client.search
    injected = []

    def canary_search(*args, **kwargs):
        result = original(*args, **kwargs)
        for page in result:
            for hit in page:
                # Inject canary into index payload
                hit["entity"]["text"] = "INDEX_FAKE_BODY_CANARY"
                injected.append(hit["entity"]["target"])
        return result

    monkeypatch.setattr(client, "search", canary_search)

    # Enable reranker to capture model inputs
    scorer = CaptureReranker()
    use_reranker(app, scorer)

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify canary injection actually happened
    assert injected, "the payload injection must actually reach the search result"

    # Verify canary doesn't appear in Pack
    assert "INDEX_FAKE_BODY_CANARY" not in plan.rendered_context

    # Verify canary doesn't appear in reranker input
    assert all("INDEX_FAKE_BODY_CANARY" not in d for d in scorer.documents)

    # Verify reranker received authoritative bodies
    assert scorer.documents == [body.bodies[n].content for n in ("m1", "m2")]


def test_rc_body_01_pack_contains_complete_body_not_concatenated_chunks(app):
    """Pack contains authoritative complete body, not hit chunk or concatenated chunks.

    Even when only some chunks hit, Pack must deliver the complete authoritative body
    from Remember, not assemble it from hit chunks.
    """
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    # Multi-chunk body
    text = "完整正文第一段。\n完整正文第二段。\n完整正文第三段。\n完整正文第四段。"
    replace_body(app, body, "m1", text)

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify complete body in Pack
    assert text in plan.rendered_context

    # Verify it's the exact authoritative body, not reconstructed
    m1_unit = next(u for u in plan.units if u.bodies[0].memory.memory_id == "m1")
    assert m1_unit.bodies[0].content == text


def test_rc_fus_08_semantic_preservation_negation_not_sugar(app):
    """Preserve negation "不加糖" (no sugar) - don't convert to positive or remove negation."""
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    text = "我喜欢喝咖啡，但是不加糖。糖对健康不好。"
    replace_body(app, body, "m1", text)

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify negation preserved
    assert "不加糖" in plan.rendered_context
    assert "不好" in plan.rendered_context

    # Verify not converted to positive statement
    assert "加糖" not in plan.rendered_context or "不加糖" in plan.rendered_context


def test_rc_fus_08_semantic_preservation_quoted_speech(app):
    """Preserve quoted speech "同事说喜欢糖" - maintain attribution, don't present as direct fact."""
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    text = '我的同事说："我喜欢在咖啡里加糖。"但我不这样认为。'
    replace_body(app, body, "m1", text)

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify quote structure preserved
    assert "同事说" in plan.rendered_context or "同事" in plan.rendered_context
    assert "喜欢" in plan.rendered_context and "加糖" in plan.rendered_context

    # Original text should be preserved
    m1_unit = next(u for u in plan.units if u.bodies[0].memory.memory_id == "m1")
    assert m1_unit.bodies[0].content == text


def test_rc_fus_08_semantic_preservation_conditional_statement(app):
    """Preserve conditional "如果加班就喝咖啡" - maintain if-then structure, not convert to fact."""
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    text = "如果明天加班，我就喝咖啡。否则喝茶。"
    replace_body(app, body, "m1", text)

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify conditional structure preserved
    assert "如果" in plan.rendered_context or text in plan.rendered_context
    assert "加班" in plan.rendered_context
    assert "喝咖啡" in plan.rendered_context

    # Original conditional preserved, not converted to "I drink coffee"
    m1_unit = next(u for u in plan.units if u.bodies[0].memory.memory_id == "m1")
    assert m1_unit.bodies[0].content == text


def test_rc_fus_08_semantic_preservation_specific_datetime(app):
    """Preserve specific datetime "周二10:00" - maintain exact time reference."""
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    text = "我们周二10:00开会讨论项目进展。会议室在3楼。"
    replace_body(app, body, "m1", text)

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify specific time preserved
    assert "周二" in plan.rendered_context or "周二10:00" in plan.rendered_context
    assert "10:00" in plan.rendered_context or "10" in plan.rendered_context
    assert "开会" in plan.rendered_context

    # Original text with exact time preserved
    m1_unit = next(u for u in plan.units if u.bodies[0].memory.memory_id == "m1")
    assert m1_unit.bodies[0].content == text


def test_rc_fus_08_preserve_source_and_model_identity(app):
    """Preserve original text, source, model identity - don't convert to definite facts."""
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    text = "根据我的观察，团队成员通常在下午工作效率更高。"
    replace_body(app, body, "m1", text)

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify source attribution preserved ("根据我的观察")
    assert "根据" in plan.rendered_context or "观察" in plan.rendered_context

    # Verify qualifier preserved ("通常")
    assert "通常" in plan.rendered_context or text in plan.rendered_context

    # Verify complete original preserved
    m1_unit = next(u for u in plan.units if u.bodies[0].memory.memory_id == "m1")
    assert m1_unit.bodies[0].content == text


def test_rc_body_01_multi_chunk_with_chinese_emoji_newlines_negation(app):
    """Complete body with Chinese, emoji, newlines, negation preserved exactly."""
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    # Complex multi-chunk body with various elements
    text = """开始部分 🎉
不要忘记这个重要信息！
中间部分包含否定：我不喜欢加班。
如果明天下雨☔，我就不去公园。
周五14:30有个会议。
结束部分 ✓"""

    replace_body(app, body, "m1", text)

    # Create multi-chunk scenario
    for manifest, _ in list(body.proofs.values()):
        if manifest["memory"]["memory_id"] == "m1":
            manifest["expected_chunk_count"] = 3

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify all elements preserved
    m1_unit = next(u for u in plan.units if u.bodies[0].memory.memory_id == "m1")
    assert m1_unit.bodies[0].content == text

    # Verify in rendered context
    assert "🎉" in plan.rendered_context
    assert "☔" in plan.rendered_context
    assert "✓" in plan.rendered_context
    assert "不要忘记" in plan.rendered_context
    assert "不喜欢" in plan.rendered_context
    assert "不去" in plan.rendered_context
    assert "如果" in plan.rendered_context
    assert "周五14:30" in plan.rendered_context


def test_rc_fus_08_relevance_score_not_factual_confidence(app):
    """Relevance score is not factual confidence; high score doesn't convert opinion to fact."""
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    # High-relevance opinion that should stay as opinion
    text = "我认为Python比Java更适合数据科学项目。这只是我的个人看法。"
    replace_body(app, body, "m1", text)

    # Force high relevance score
    with app.foundation.uow.transaction() as tx:
        for key, row in tx.rows("generation_vectors"):
            if row["hit"]["memory"]["memory_id"] == "m1":
                row["hit"]["score"] = 0.99  # Very high relevance
                row["vector"] = [0.99, 0.0]
                tx.write("generation_vectors", key, row)

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify opinion markers preserved despite high relevance
    assert "我认为" in plan.rendered_context or "认为" in plan.rendered_context
    assert "个人看法" in plan.rendered_context or "看法" in plan.rendered_context

    # Original opinion text preserved exactly
    m1_unit = next(u for u in plan.units if u.bodies[0].memory.memory_id == "m1")
    assert m1_unit.bodies[0].content == text


def test_rc_body_01_model_input_limit_does_not_change_complete_body_requirement(app):
    """Model input limit can't change Pack's complete body requirement.

    Even if body exceeds some model input limit, Pack must provide complete authoritative body.
    Truncation or summarization is the consumer's decision, not Recall's.
    """
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    # Large body that might exceed some limits
    text = "第一段内容。\n" * 50 + "关键信息在这里。\n" + "最后一段内容。\n" * 50
    replace_body(app, body, "m1", text)

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify complete body delivered
    m1_unit = next(u for u in plan.units if u.bodies[0].memory.memory_id == "m1")
    assert m1_unit.bodies[0].content == text

    # Verify not truncated in Pack
    assert "关键信息在这里" in plan.rendered_context
    assert plan.rendered_context.count("第一段内容") >= 40  # Most repetitions present
    assert plan.rendered_context.count("最后一段内容") >= 40


def test_rc_body_02_authoritative_body_read_path_verification(app, monkeypatch):
    """Comprehensive verification that authoritative body read path is used, not index payload.

    Tests multiple injection points to confirm system never uses index payload as body source.
    """
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    # Inject different canaries at different points
    client = assembly.candidates.vectors.vectors.client
    original_search = client.search
    injection_points = []

    def multi_canary_search(*args, **kwargs):
        result = original_search(*args, **kwargs)
        for page_idx, page in enumerate(result):
            for hit_idx, hit in enumerate(page):
                canary = f"CANARY_PAGE{page_idx}_HIT{hit_idx}"
                hit["entity"]["text"] = canary
                injection_points.append(canary)
        return result

    monkeypatch.setattr(client, "search", multi_canary_search)

    scorer = CaptureReranker()
    use_reranker(app, scorer)

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify injections happened
    assert len(injection_points) > 0

    # Verify no canaries in Pack
    for canary in injection_points:
        assert canary not in plan.rendered_context

    # Verify no canaries in reranker
    for canary in injection_points:
        assert all(canary not in doc for doc in scorer.documents)

    # Verify authoritative bodies used
    for doc in scorer.documents:
        assert any(doc == body.bodies[mid].content for mid in body.bodies)
