"""Official Trustcall repairs schema errors; P3 still rejects unresolved tools."""

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from aether_agent_memory.remember.basic.official_langmem import OfficialLangMemConsolidation

from .test_official_langmem import snapshot


class RepairModel(BaseChatModel):
    calls: int = 0
    target: str = "invalid-kind"
    repairs: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "official-schema-repair-regression"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=tools, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            name = "CandidateMemory"
            args = {
                "text": "Alice approved the launch.",
                "kind": "decision",
                "evidence_ids": ["e1"],
            }
        else:
            self.repairs.append(messages)
            name = "PatchFunctionErrors"
            args = {
                "json_doc_id": self.target,
                "planned_edits": "kind must use the supplied memory type enum",
                "patches": [{"op": "replace", "path": "/kind", "value": "episodic"}],
            }
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "invalid-kind" if self.calls == 1 else "repair-kind",
                                "name": name,
                                "args": args,
                            }
                        ],
                    )
                )
            ]
        )


async def test_official_manager_repairs_invalid_kind_before_returning_candidates():
    model = RepairModel()
    adapter = OfficialLangMemConsolidation.from_model(model, "repair-test")
    charged = []
    result = await adapter.extract_candidates(
        None,
        (snapshot("source", "Alice approved the launch."),),
        "v1",
        on_model_call=lambda: charged.append(True),
    )
    assert model.calls == len(charged) == 2
    assert len(result.candidates) == 1
    assert result.candidates[0].candidate.kind == "episodic"
    assert result.candidates[0].candidate.evidence[0].quote == "Alice approved the launch."
    assert model.repairs


async def test_schema_repair_cannot_target_an_unseen_call():
    model = RepairModel(target="another-call")
    adapter = OfficialLangMemConsolidation.from_model(model, "repair-test")
    with pytest.raises(ValueError, match="repair target"):
        await adapter.extract_candidates(None, (snapshot("s", "Alice approved the launch."),), "v1")


@pytest.mark.parametrize("legacy", [False, True])
async def test_unrepaired_schema_error_cannot_become_zero_candidate_success(legacy):
    from langchain_core.outputs import LLMResult

    class DiscardingManager:
        async def ainvoke(self, payload, config):
            response = RepairModel()._generate([])
            response.generations[0].message.tool_calls[0]["name"] = config["callbacks"][
                0
            ].schema.__name__
            config["callbacks"][0].on_llm_end(LLMResult(generations=[response.generations]))
            return []

    manager = DiscardingManager()
    adapter = OfficialLangMemConsolidation(manager, "discard-test", extraction_manager=manager)
    with pytest.raises(ValueError, match="unresolved schema"):
        items = (snapshot("s", "Alice approved the launch."),)
        if legacy:
            await adapter.consolidate(None, items, (), "v1")
        else:
            await adapter.extract_candidates(None, items, "v1")
