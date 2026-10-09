"""Exercise official two-stage evidence contracts, not model accuracy claims."""

import json

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from aether_agent_memory.remember.basic.llmlingua import CompressionView
from aether_agent_memory.remember.basic.official_langmem import OfficialLangMemConsolidation
from aether_agent_memory.runtime.foundation.requests import text_hash
from unit.test_official_langmem import snapshot


@pytest.mark.parametrize(
    "original,extracted,restored,compressed",
    [
        (
            "终端必须离线保存至少500条扫描记录，恢复网络后按原event_id补传。",
            "终端必须离线保存500条扫描记录，恢复网络后按原event_id补传。",
            "终端必须离线保存至少500条扫描记录，恢复网络后按原event_id补传。",
            True,
        ),
        (
            "延期原因是北湾仓货架调整，不是软件性能不达标。",
            "延期原因是北湾仓货架调整。",
            "延期原因是北湾仓货架调整，不是软件性能不达标。",
            False,
        ),
        (
            "测试订单完成后先导出证据再按演练run_id清理，不能用删除全部订单的方式收尾。",
            "测试订单完成后先导出证据再按演练run_id清理。",
            "测试订单完成后先导出证据再按演练run_id清理，不能用删除全部订单的方式收尾。",
            False,
        ),
    ],
    ids=["compressed-minimum", "direct-negated-cause", "short-prohibited-alternative"],
)
async def test_existing_two_calls_can_restore_same_claim_qualifiers_from_bound_evidence(
    original, extracted, restored, compressed
):
    # These are the loss shapes observed in Azure. The scripted first call
    # deliberately reproduces a weakened candidate; it is NOT a quality judge.
    calls = []
    working = snapshot("qualified", original)

    class ControlledModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "semantic-fidelity-contract"

        def bind_tools(self, tools, **kwargs):
            return self.bind(tools=tools, **kwargs)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            rendered = "\n".join(str(message.content) for message in messages)
            calls.append(rendered)
            assert "Retain the numeric operator together with its value and unit" in rendered
            if len(calls) == 1:
                assert "Negated causes and prohibited alternatives are durable content" in rendered
                name = "CandidateMemory"
                args = {"text": extracted, "kind": "semantic", "evidence_ids": ["e1"]}
            else:
                assert len(calls) == 2  # No extra compression review/model pass.
                assert "Restore a missing qualification of the SAME candidate assertion" in rendered
                assert "Do not extract unrelated claims from an evidence excerpt" in rendered
                # Stage two must receive the full verified supporting excerpt,
                # even when stage one only saw the token-deleted source view.
                assert original in rendered
                assert extracted in rendered
                name = "ConsolidatedMemory"
                args = {
                    "text": restored,
                    "kind": "semantic",
                    "candidate_ids": ["c1"],
                    "evidence_ids": ["e1"],
                    "relationship": "create",
                    "reason": "Retain the explicit qualification of the same sourced assertion.",
                }
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[{"id": f"call-{len(calls)}", "name": name, "args": args}],
                        )
                    )
                ]
            )

    views = None
    if compressed:
        start = original.index("至少")
        offsets = tuple(i for i in range(len(original)) if i not in (start, start + 1))
        text = "".join(original[i] for i in offsets)
        views = {
            "src_qualified": CompressionView(
                text=text,
                offsets=offsets,
                source_hash=text_hash(original),
                source_chars=len(original),
                original_bytes=len(original.encode()),
                retained_bytes=len(text.encode()),
            )
        }
    adapter = OfficialLangMemConsolidation.from_model(ControlledModel(), "controlled")
    extracted_result = await adapter.extract_candidates(None, (working,), "p1", source_views=views)
    candidates = extracted_result.candidates
    assert len(candidates) == 1
    assert candidates[0].candidate.text == extracted
    evidence = candidates[0].candidate.evidence
    assert evidence[0].quote == original
    assert original[evidence[0].start_char : evidence[0].end_char] == original
    result = await adapter.decide_candidates(
        None,
        candidates,
        (),
        "p1",
        originals=(working,),
        related_ids={candidates[0].candidate_id: ()},
    )
    assert len(calls) == 2
    proposal = result.proposals[0]
    assert proposal.candidate.text == restored
    assert proposal.candidate.evidence == evidence
    assert proposal.candidate.sources == working.sources
    assert proposal.candidate_ids == (candidates[0].candidate_id,)
    assert proposal.decision.outcome == "create"
    # The normal persistence contract serializes the complete restored body;
    # there is no second authority body or hidden verifier result.
    assert json.loads(result.model_dump_json())["proposals"][0]["candidate"]["text"] == restored
