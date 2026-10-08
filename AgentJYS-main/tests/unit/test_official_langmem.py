"""Official LangMem performs consolidation; P3 validates evidence and owns writes."""

import json
from types import SimpleNamespace

import pytest

from aether_agent_memory.remember.basic.official_langmem import (
    ConsolidatedMemory,
    OfficialLangMemConsolidation,
)
from aether_agent_memory.remember.contracts.models import (
    MemoryKind,
    MemoryRef,
    MemorySnapshot,
    MemoryStatus,
    ProjectionState,
    SourceRef,
)
from aether_agent_memory.runtime.contracts.models import Scope
from aether_agent_memory.runtime.foundation.requests import text_hash


def snapshot(key, text, kind=MemoryKind.WORKING):
    return MemorySnapshot(
        ref=MemoryRef(
            scope=Scope(tenant_id="t", application_id="a", user_id="u", agent_id="g"),
            memory_id=key,
            version=1,
        ),
        revision=1,
        object_revision=1,
        kind=kind,
        status=MemoryStatus.ACTIVE,
        content=text,
        content_hash=text_hash(text),
        sources=(
            SourceRef(
                source_id="src_" + key,
                source_version=1,
                content_hash=text_hash(text),
                locator="objects/" + key,
            ),
        ),
        projection_state=ProjectionState.PENDING,
        created_at="2026-10-07T00:00:00.000Z",
    )


def fact(text, source_id, quote=None, **kwargs):
    return ConsolidatedMemory(
        text=text,
        kind="semantic",
        evidence=[{"source_id": source_id, "quote": quote or text}],
        **kwargs,
    )


class Manager:
    def __init__(self, callback):
        self.callback, self.calls = callback, []

    async def ainvoke(self, payload, **kwargs):
        self.calls.append(payload)
        return self.callback(payload)


def row(key, value):
    return SimpleNamespace(id=key, content=value)


async def test_zero_input_never_calls_manager_even_with_old_context():
    manager = Manager(lambda _: pytest.fail("empty input called manager"))
    result = await OfficialLangMemConsolidation(manager, "test").consolidate(
        None,
        (),
        (),
        "p1",
        context_items=(snapshot("ctx", "old message"),),
    )
    assert result.proposals == ()


async def test_many_inputs_produce_many_memories_and_keep_original_offsets():
    one, two = (
        snapshot("one", "Alice uses Linux.\r\nOnly for work."),
        snapshot("two", "Bob uses Mac."),
    )
    manager = Manager(
        lambda _: [
            row(
                "generated-uuid-one",
                fact(one.content, "src_one", one.content.replace("\r\n", "\n")),
            ),
            row("generated-uuid-two", fact(two.content, "src_two")),
        ]
    )
    result = await OfficialLangMemConsolidation(manager, "test").consolidate(
        None,
        (one, two),
        (),
        "p1",
    )
    assert [p.decision.outcome for p in result.proposals] == ["create", "create"]
    evidence = result.proposals[0].candidate.evidence[0]
    assert one.content[evidence.start_char : evidence.end_char] == evidence.quote == one.content


async def test_official_unchanged_old_records_are_ignored():
    new, old = snapshot("new", "Hello"), snapshot("old", "Alice uses Linux.", MemoryKind.SEMANTIC)
    manager = Manager(lambda payload: [row(key, value) for key, value in payload["existing"]])
    result = await OfficialLangMemConsolidation(manager, "test").consolidate(
        None,
        (new,),
        (old,),
        "p1",
    )
    assert result.proposals == ()
    assert manager.calls[0]["existing"][0][0] == "old"


async def test_repeated_old_content_with_new_evidence_adds_support_without_version_change():
    new = snapshot("new", "Alice uses Linux.")
    old = snapshot("old", new.content, MemoryKind.SEMANTIC)
    manager = Manager(
        lambda _: [row("old", fact(new.content, "src_new", relationship="no_change"))]
    )
    result = await OfficialLangMemConsolidation(manager, "test").consolidate(
        None,
        (new,),
        (old,),
        "p1",
    )
    assert result.proposals[0].decision.outcome == "no_change"
    assert result.proposals[0].decision.target_id == "old"
    assert result.proposals[0].candidate.sources == new.sources


@pytest.mark.parametrize(
    "relationship,kind,expected",
    [
        ("amend", MemoryKind.EPISODIC, "amend"),
        ("amend", MemoryKind.SEMANTIC, "conflict"),
        ("conflict", MemoryKind.EPISODIC, "conflict"),
    ],
)
async def test_known_id_changes_are_business_proposals(relationship, kind, expected):
    old = snapshot("old", "Alice attended.", kind)
    new = snapshot("new", "Alice attended. Bob attended too.")
    changed = fact(new.content, "src_new", relationship=relationship).model_copy(
        update={"kind": kind.value}
    )
    manager = Manager(lambda _: [row("old", changed)])
    result = await OfficialLangMemConsolidation(manager, "test").consolidate(
        None,
        (new,),
        (old,),
        "p1",
    )
    assert result.proposals[0].decision.outcome == expected
    assert result.proposals[0].decision.target_id == "old"


@pytest.mark.parametrize(
    "invalid",
    ["unknown_source", "forged_quote", "context_only", "duplicate_id", "delete", "extra_field"],
)
async def test_invalid_output_fails_closed(invalid):
    new, context = snapshot("new", "Alice uses Linux."), snapshot("ctx", "Bob uses Mac.")
    value = fact(new.content, "src_new")
    if invalid == "unknown_source":
        value = fact(new.content, "unknown")
    elif invalid == "forged_quote":
        value = fact("Alice uses Windows.", "src_new")
    elif invalid == "context_only":
        value = fact(context.content, "src_ctx")
    elif invalid == "delete":
        value = {"json_doc_id": "new"}
    elif invalid == "extra_field":
        value = {**value.model_dump(), "target_id": "not_authorized"}
    rows = [row("generated", value)]
    if invalid == "duplicate_id":
        rows.append(row("generated", value))
    manager = Manager(lambda _: rows)
    with pytest.raises(ValueError):
        await OfficialLangMemConsolidation(manager, "test").consolidate(
            None,
            (new,),
            (),
            "p1",
            context_items=(context,),
        )


async def test_legacy_compressed_view_never_replaces_original_model_input():
    new = snapshot("new", "The log says: Alice uses Linux. Then nothing else happened.")
    manager = Manager(lambda _: [row("generated", fact("Alice uses Linux.", "src_new"))])
    result = await OfficialLangMemConsolidation(manager, "test").consolidate(
        None,
        (new,),
        (),
        "p1",
        representations=[{"source_id": "src_new", "text": "Alice uses Linux."}],
    )
    body = json.loads(manager.calls[0]["messages"][0]["content"])
    assert body["sources"][0]["text"] == new.content
    assert result.proposals[0].candidate.evidence[0].start_char == len("The log says: ")


async def test_historical_evidence_preserves_original_content_and_source_role():
    new = snapshot("new", "Alice still uses Linux.")
    original = snapshot("history", "Historical log: Alice uses Linux. " + "padding " * 6000)
    value = fact(new.content, "src_new").model_dump()
    value["evidence"].append({"source_id": "src_history", "quote": "Alice uses Linux."})
    value = ConsolidatedMemory.model_validate(value)
    manager = Manager(lambda _: [row("generated", value)])
    result = await OfficialLangMemConsolidation(manager, "test").consolidate(
        None,
        (new,),
        (),
        "p1",
        representations=[{"source_id": "src_history", "text": "Alice uses Linux."}],
        existing_evidence=(original,),
    )
    sources = json.loads(manager.calls[0]["messages"][0]["content"])["sources"]
    historical = next(s for s in sources if s["source_id"] == "src_history")
    assert historical == {
        "source_id": "src_history",
        "role": "old_evidence",
        "text": original.content,
    }
    evidence = next(
        e for e in result.proposals[0].candidate.evidence if e.source.source_id == "src_history"
    )
    assert evidence.start_char == len("Historical log: ")
    assert evidence.source.content_hash == original.sources[0].content_hash


async def test_processed_overlap_does_not_authorize_historical_evidence():
    from aether_agent_memory.remember.basic.extraction import EvidenceValidationError

    new = snapshot("new", "Alice still uses Linux.")
    processed = snapshot("history", "Alice uses Linux.")
    value = fact(new.content, "src_new").model_dump()
    value["evidence"].append({"source_id": "src_history", "quote": processed.content})
    manager = Manager(lambda _: [row("generated", value)])
    with pytest.raises(EvidenceValidationError, match="unknown_source_id"):
        await OfficialLangMemConsolidation(manager, "test").consolidate(
            None, (new,), (), "p1", context_items=(processed,)
        )
    sources = json.loads(manager.calls[0]["messages"][0]["content"])["sources"]
    assert [item["source_id"] for item in sources] == ["src_new"]


async def test_historical_view_cannot_authorize_quote_absent_from_original():
    new, original = snapshot("new", "Alice uses Linux."), snapshot("history", "Bob uses Mac.")
    value = fact(new.content, "src_new").model_dump()
    value["evidence"].append({"source_id": "src_history", "quote": "Bob uses Linux."})
    manager = Manager(lambda _: [row("generated", value)])
    from aether_agent_memory.remember.basic.extraction import EvidenceValidationError

    with pytest.raises(EvidenceValidationError, match="quote_not_in_source"):
        await OfficialLangMemConsolidation(manager, "test").consolidate(
            None,
            (new,),
            (),
            "p1",
            existing_evidence=(original,),
            representations=[{"source_id": "src_history", "text": "Bob uses Linux."}],
        )


async def test_real_official_manager_calls_tools_for_multi_insert_and_keeps_old_id():
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class ScriptedModel(BaseChatModel):
        calls: list = []

        @property
        def _llm_type(self):
            return "scripted-tools"

        def bind_tools(self, tools, **kwargs):
            return self.bind(tools=tools, **kwargs)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls.append((messages, kwargs))
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "call_a",
                                    "name": "ConsolidatedMemory",
                                    "args": fact("Alice uses Linux.", "src_new").model_dump(),
                                },
                                {
                                    "id": "call_b",
                                    "name": "ConsolidatedMemory",
                                    "args": fact("Bob uses Mac.", "src_two").model_dump(),
                                },
                            ],
                        )
                    )
                ]
            )

    model = ScriptedModel()
    adapter = OfficialLangMemConsolidation.from_model(model, "scripted")
    result = await adapter.consolidate(
        None,
        (snapshot("new", "Alice uses Linux."), snapshot("two", "Bob uses Mac.")),
        (snapshot("old", "Carol uses BSD.", MemoryKind.SEMANTIC),),
        "p1",
    )
    assert len(result.proposals) == 2
    assert {p.candidate.text for p in result.proposals} == {"Alice uses Linux.", "Bob uses Mac."}
    assert model.calls and "tools" in model.calls[0][1]
    assert "old" in str(model.calls[0][0])


async def test_chat_factory_uses_real_tool_calls_and_deployment_limits(monkeypatch):
    import httpx

    from aether_agent_memory.remember.langmem_model import create_langmem_chat_model
    from aether_agent_memory.runtime.flows.config import LanguageModel

    received = []

    async def handler(request):
        received.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chat-test",
                "object": "chat.completion",
                "model": "deployment",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_one",
                                    "type": "function",
                                    "function": {
                                        "name": "ConsolidatedMemory",
                                        "arguments": json.dumps(
                                            fact("Alice uses Linux.", "src_new").model_dump()
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
        )

    monkeypatch.setenv("TEST_LANGMEM_KEY", "test-not-a-real-key")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = LanguageModel(
        endpoint="http://localhost:8000/v1",
        model="deployment",
        api_key_env="TEST_LANGMEM_KEY",
        max_output_tokens=512,
        token_limit_parameter="max_tokens",
        temperature=0,
    )
    model = create_langmem_chat_model(config, client=client)
    result = await model.bind_tools([ConsolidatedMemory]).ainvoke("input")
    payload = json.loads(received[0].content)
    assert result.tool_calls[0]["name"] == "ConsolidatedMemory"
    assert "tools" in payload and "response_format" not in payload
    assert payload["max_tokens"] == 512 and "max_completion_tokens" not in payload
    assert payload["temperature"] == 0
    assert received[0].url.path == "/v1/chat/completions"
    assert received[0].headers["authorization"] == "Bearer test-not-a-real-key"
    assert "test-not-a-real-key" not in str(model.checkpoint_identity())
    await model.aclose()
    assert not client.is_closed
    await client.aclose()


async def test_chat_factory_rejects_oversized_response():
    import httpx

    from aether_agent_memory.remember.langmem_model import create_langmem_chat_model
    from aether_agent_memory.runtime.flows.config import LanguageModel

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "id": "chat-test",
                    "object": "chat.completion",
                    "model": "deployment",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": "x" * 2048,
                            },
                        }
                    ],
                },
            )
        )
    )
    model = create_langmem_chat_model(
        LanguageModel(model="deployment", max_response_bytes=1024), client=client
    )
    with pytest.raises(ValueError, match="response exceeds"):
        await model.ainvoke("input")
    await model.aclose()
    await client.aclose()


def scripted_tool_model(tool_calls):
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class ScriptedModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "scripted-tools"

        def bind_tools(self, tools, **kwargs):
            return self.bind(tools=tools, **kwargs)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=tool_calls,
                        )
                    )
                ]
            )

    return ScriptedModel()


@pytest.mark.parametrize(
    "mode,expected", [("amend", "amend"), ("no_change", "no_change"), ("unchanged", None)]
)
async def test_real_official_patch_retains_target_identity(mode, expected):
    old = snapshot("old", "Alice attended.", MemoryKind.EPISODIC)
    new = snapshot("new", "Alice attended. Bob attended too.")
    patches = (
        []
        if mode == "unchanged"
        else [
            {
                "op": "replace",
                "path": "/evidence",
                "value": [{"source_id": "src_new", "quote": new.content}],
            },
            {"op": "replace", "path": "/relationship", "value": mode},
        ]
    )
    if mode == "amend":
        patches.append({"op": "replace", "path": "/text", "value": new.content})
    model = scripted_tool_model(
        [
            {
                "id": "patch_call",
                "name": "PatchDoc",
                "args": {
                    "json_doc_id": "old",
                    "planned_edits": "Apply supported new evidence.",
                    "patches": patches,
                },
            }
        ]
    )
    result = await OfficialLangMemConsolidation.from_model(model, "test").consolidate(
        None,
        (new,),
        (old,),
        "p1",
    )
    if expected is None:
        assert result.proposals == ()
    else:
        assert len(result.proposals) == 1
        assert result.proposals[0].decision.outcome == expected
        assert result.proposals[0].decision.target_id == "old"


async def test_real_official_unknown_patch_target_is_not_silently_lost():
    model = scripted_tool_model(
        [
            {
                "id": "patch_call",
                "name": "PatchDoc",
                "args": {
                    "json_doc_id": "not_authorized",
                    "planned_edits": "change",
                    "patches": [
                        {"op": "replace", "path": "/text", "value": "Alice uses Linux."},
                    ],
                },
            }
        ]
    )
    with pytest.raises(ValueError, match="unknown.*target"):
        await OfficialLangMemConsolidation.from_model(model, "test").consolidate(
            None,
            (snapshot("new", "Alice uses Linux."),),
            (snapshot("old", "Alice uses Mac.", MemoryKind.SEMANTIC),),
            "p1",
        )


async def test_chat_factory_enforces_concurrency_and_timeout():
    import asyncio

    import httpx

    from aether_agent_memory.remember.langmem_model import create_langmem_chat_model
    from aether_agent_memory.runtime.flows.config import LanguageModel

    active, peak = 0, 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.05)
            return httpx.Response(
                200,
                json={
                    "id": "chat-test",
                    "object": "chat.completion",
                    "model": "deployment",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": "No durable information.",
                            },
                        }
                    ],
                },
            )
        finally:
            active -= 1

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = create_langmem_chat_model(LanguageModel(model="test", concurrency=1), client=client)
    await asyncio.gather(model.ainvoke("one"), model.ainvoke("two"))
    assert peak == 1
    timed = create_langmem_chat_model(
        LanguageModel(model="test", timeout_seconds=0.01),
        client=client,
    )
    with pytest.raises(TimeoutError):
        await timed.ainvoke("timeout")
    await model.aclose()
    await timed.aclose()
    await client.aclose()


async def test_network_stream_enforces_limit_before_reading_whole_body():
    import httpx

    from aether_agent_memory.remember.langmem_model import _LimitedStream

    consumed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(10):
                consumed.append(index)
                yield b"x" * 512

    with pytest.raises(ValueError, match="response exceeds"):
        async for _ in _LimitedStream(Stream(), 1024):
            pass
    assert consumed == [0, 1, 2]


async def test_real_model_call_budget_runs_before_each_official_call():
    model = scripted_tool_model([])
    adapter = OfficialLangMemConsolidation.from_model(model, "test")
    calls = []
    result = await adapter.consolidate(
        None,
        (snapshot("new", "hello"),),
        (),
        "p1",
        on_model_call=lambda: calls.append(1),
    )
    assert result.proposals == () and calls == [1]

    def exceeded():
        calls.append(2)
        raise ValueError("task model budget exhausted")

    with pytest.raises(ValueError, match="budget exhausted"):
        await adapter.consolidate(
            None,
            (snapshot("new", "hello"),),
            (),
            "p1",
            on_model_call=exceeded,
        )
    assert calls == [1, 2]


async def test_official_provider_retry_also_consumes_task_budget():
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    actual_calls, charged_calls = [], []

    class RetryModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "retry-test"

        def bind_tools(self, tools, **kwargs):
            return self.bind(tools=tools, **kwargs)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            actual_calls.append(1)
            if len(actual_calls) == 1:
                raise ValueError("transient provider failure")
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])

    await OfficialLangMemConsolidation.from_model(RetryModel(), "test").consolidate(
        None,
        (snapshot("new", "hello"),),
        (snapshot("old", "Alice uses Linux.", MemoryKind.SEMANTIC),),
        "p1",
        on_model_call=lambda: charged_calls.append(1),
    )
    assert len(actual_calls) == len(charged_calls) == 2


@pytest.mark.parametrize("tools_work", [True, False])
async def test_health_requires_real_tool_support_and_caches_result(tools_work):
    import asyncio

    import httpx

    from aether_agent_memory.remember.langmem_model import create_langmem_chat_model
    from aether_agent_memory.runtime.flows.config import LanguageModel

    calls = []

    async def handler(request):
        calls.append(json.loads(request.content))
        await asyncio.sleep(0.01)
        message = {"role": "assistant", "content": '{"ready": true}'}
        if tools_work:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "probe_call",
                        "type": "function",
                        "function": {
                            "name": "LangMemToolReadiness",
                            "arguments": '{"ready": true}',
                        },
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "id": "chat-test",
                "object": "chat.completion",
                "model": "deployment",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if tools_work else "stop",
                        "message": message,
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = create_langmem_chat_model(
        LanguageModel(model="test", health_max_output_tokens=512), client=client
    )
    adapter = OfficialLangMemConsolidation.from_model(model, "test")
    one, two = await asyncio.gather(adapter.health(), adapter.health())
    assert one == two and one["state"] == ("available" if tools_work else "unavailable")
    assert await adapter.health() == one
    assert len(calls) == 1 and calls[0]["max_completion_tokens"] == 512
    await model.aclose()
    assert (await adapter.health())["state"] == "unavailable"
    await client.aclose()
