"""Real PG/Ceph/Redis/Milvus/Temporal with controlled consolidation output."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from remember_helpers import app as app
from remember_helpers import context, drain, source

from aether_agent_memory.remember.basic.official_langmem import (
    ConsolidatedMemory,
    OfficialLangMemConsolidation,
)
from aether_agent_memory.remember.contracts.models import RememberRequest, TextInput
from aether_agent_memory.runtime.contracts.models import ScopeSelector


class Manager:
    def __init__(self, emit=True):
        self.inputs = []
        self.emit = emit

    async def ainvoke(self, payload, **kwargs):
        self.inputs.append(payload)
        if not self.emit:
            return [SimpleNamespace(id=key, content=value) for key, value in payload["existing"]]
        item = next(
            s
            for s in json.loads(payload["messages"][0]["content"])["sources"]
            if s["role"] == "new"
        )
        old = payload["existing"]
        key = old[0][0] if old else "official-generated-id"
        fact = ConsolidatedMemory(
            text=item["text"],
            kind="semantic",
            evidence=[{"source_id": item["source_id"], "quote": item["text"]}],
            relationship="no_change" if old else "create",
        )
        return [SimpleNamespace(id=key, content=fact)]


def remember(app, name, text):
    return asyncio.run(
        app.remember.save(
            context(app),
            RememberRequest(
                source=source(name),
                content=TextInput(kind="text", text=text),
                selection=ScopeSelector(session_id="official-session"),
                trigger="remember",
            ),
        )
    )


def test_official_consolidation_owns_new_old_decisions(app):
    manager = Manager()
    app.remember.extraction = OfficialLangMemConsolidation(manager, "controlled")
    comparison = AsyncMock(side_effect=AssertionError("legacy comparison was called"))
    app.remember.comparison = SimpleNamespace(compare=comparison)
    first = remember(app, "first", "Alice prefers unsweetened coffee.")
    drain(app)
    with app.foundation.uow.transaction() as tx:
        old = [
            r
            for _, r in tx.rows("remember_current")
            if r["object_id"] != first.memories[0].memory_id
        ]
    assert len(old) == 1
    second = remember(app, "second", "Alice prefers unsweetened coffee.")
    drain(app)
    with app.foundation.uow.transaction() as tx:
        refs = [
            r
            for _, r in tx.rows("remember_current")
            if r["object_id"] not in {first.memories[0].memory_id, second.memories[0].memory_id}
        ]
    assert len(refs) == 1
    assert manager.inputs[1]["existing"]
    comparison.assert_not_awaited()


def test_zero_output_marks_input_processed_without_inventing_memory(app):
    manager = Manager(emit=False)
    app.remember.extraction = OfficialLangMemConsolidation(manager, "controlled")
    receipt = remember(app, "greeting", "Hello.")
    drain(app)
    with app.foundation.uow.transaction() as tx:
        pending = tx.read("remember_pending", receipt.memories[0].memory_id)
        assert pending["state"] == "processed"
        assert len(list(tx.rows("remember_current"))) == 1


def test_real_official_manager_runs_through_temporal_and_real_storage(app):
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class ToolModel(BaseChatModel):
        evidence_id: str = ""

        @property
        def _llm_type(self):
            return "controlled-official-integration"

        def bind_tools(self, tools, **kwargs):
            return self.bind(tools=tools, **kwargs)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            assert "tools" in kwargs
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "tool_1",
                                    "name": "ConsolidatedMemory",
                                    "args": ConsolidatedMemory(
                                        text="Alice uses Linux.",
                                        kind="semantic",
                                        evidence=[
                                            {
                                                "source_id": self.evidence_id,
                                                "quote": "Alice uses Linux.",
                                            }
                                        ],
                                    ).model_dump(),
                                }
                            ],
                        )
                    )
                ]
            )

    model = ToolModel()
    app.remember.extraction = OfficialLangMemConsolidation.from_model(model, "controlled")
    receipt = remember(app, "real-official", "Alice uses Linux.")
    model.evidence_id = receipt.source.source_id
    drain(app)
    with app.foundation.uow.transaction() as tx:
        pending = tx.read("remember_pending", receipt.memories[0].memory_id)
        assert pending["state"] == "processed"
        assert tx.read("remember_model_calls", pending["task_id"]) == 1
        assert len(list(tx.rows("remember_current"))) == 2


def test_changed_space_recomputes_official_plan_before_commit(app):
    class RacingManager(Manager):
        async def ainvoke(self, payload, **kwargs):
            result = await super().ainvoke(payload, **kwargs)
            if len(self.inputs) == 1:
                with app.foundation.uow.transaction() as tx:
                    key, row = next(iter(tx.rows("remember_pending")))
                    memory = app.remember.current(tx, key)
                    space = app.remember.space_key(memory.ref.scope)
                    tx.write(
                        "remember_space_seq", space, (tx.read("remember_space_seq", space) or 0) + 1
                    )
            return result

    manager = RacingManager()
    app.remember.extraction = OfficialLangMemConsolidation(manager, "controlled")
    receipt = remember(app, "racing", "Alice uses Linux.")
    drain(app)
    assert len(manager.inputs) >= 2
    with app.foundation.uow.transaction() as tx:
        assert tx.read("remember_pending", receipt.memories[0].memory_id)["state"] == "processed"
        assert len(list(tx.rows("remember_current"))) == 2


@pytest.mark.parametrize("overlap", [0, 2])
def test_processed_large_source_does_not_poison_next_tiny_batch(app, overlap):
    from aether_agent_memory.remember.basic.compression import CompressionOutput, QualityEvidence

    claim = "Alice uses Linux."

    class Compressor:
        async def compress(self, ctx, text):
            assert text.startswith(claim)
            return CompressionOutput(text=claim, strategy="controlled")

    class Quality:
        async def verify(self, ctx, original, compressed):
            assert original.startswith(compressed)
            return QualityEvidence(
                passed=True,
                policy="controlled",
                reason="controlled test verdict",
                retained_fact_fraction=1,
                declared_use="supported_summary",
            )

    manager = Manager()
    app.remember.extraction = OfficialLangMemConsolidation(manager, "controlled")
    app.remember.compressor, app.remember.quality = Compressor(), Quality()
    app.remember.policy = app.remember.policy.model_copy(
        update={
            "comparison_context_tokens": 512,
            "extraction_chunk_tokens": 20000,
            "consolidation_overlap_messages": overlap,
        }
    )
    text = claim + "\n" + "background prose " * 1000
    assert app.remember.tokenizer.count(text) > app.remember.policy.comparison_context_tokens
    first = remember(app, "large-history", text)
    drain(app)
    with app.foundation.uow.transaction() as tx:
        assert tx.read("remember_pending", first.memories[0].memory_id)["state"] == "processed"
        assert tx.read("remember_artifacts", app.remember.refkey(first.memories[0]))["published"]

    second = remember(app, "tiny-after-large", claim)
    drain(app)
    with app.foundation.uow.transaction() as tx:
        assert tx.read("remember_pending", second.memories[0].memory_id)["state"] == "processed"
        assert len(list(tx.rows("remember_current"))) == 3
    assert manager.inputs[-1]["existing"]
    historical = [
        s
        for s in json.loads(manager.inputs[-1]["messages"][0]["content"])["sources"]
        if s["source_id"] == first.source.source_id
    ]
    assert historical == [
        {
            "source_id": first.source.source_id,
            "role": "context" if overlap else "old_evidence",
            "text": claim,
        }
    ]
