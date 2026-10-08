"""Default service assembly, real storage/Temporal, controlled HTTP tool transport."""

import json
import time

import httpx
from fastapi.testclient import TestClient
from test_temporal_http import configuration, headers, request, wait_ready, wait_result

from aether_agent_memory.remember.basic.official_langmem import OfficialLangMemConsolidation
from aether_agent_memory.runtime.flows.config import LanguageModel
from azure_component_service import Service


def test_service_default_uses_official_tools_and_commits_http_remember(
    tmp_path, temporal_server, monkeypatch
):
    calls = []

    def handler(message):
        body = json.loads(message.content)
        calls.append(body)
        tool_names = {tool["function"]["name"] for tool in body["tools"]}
        if "LangMemToolReadiness" in tool_names:
            name, args = "LangMemToolReadiness", {"ready": True}
        else:
            source = next(
                source
                for part in body["messages"]
                if part["role"] == "user" and '{"sources"' in part.get("content", "")
                for source in json.JSONDecoder().raw_decode(
                    part["content"][part["content"].index('{"sources"') :]
                )[0]["sources"]
                if source["role"] == "new"
            )
            name = "ConsolidatedMemory"
            args = {
                "text": source["text"],
                "kind": "semantic",
                "evidence": [{"source_id": source["source_id"], "quote": source["text"]}],
                "relationship": "create",
            }
        return httpx.Response(
            200,
            json={
                "id": "controlled-service-response",
                "object": "chat.completion",
                "model": "controlled-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "controlled-call",
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(args)},
                                }
                            ],
                        },
                    }
                ],
            },
        )

    monkeypatch.setattr(
        "aether_agent_memory.remember.langmem_model._LimitedTransport",
        lambda limit: httpx.MockTransport(handler),
    )
    config = configuration(tmp_path, temporal_server.endpoint).model_copy(
        update={"language_model": LanguageModel(model="controlled-model")}
    )
    service = Service(config)
    adapter = service.runtime.remember.extraction
    assert isinstance(adapter, OfficialLangMemConsolidation)
    with TestClient(service.app()) as client:
        wait_ready(client, service)
        value = request()
        # Explicit legacy trigger remains supported without intent classification.
        value["trigger"] = "remember"
        response = client.post("/p3/remember", json=value, headers=headers())
        if response.status_code == 400:
            response = wait_result(client, response.headers["location"])
        assert response.status_code == 200, response.text
        memory_id = response.json()["memories"][0]["memory_id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with service.runtime.foundation.uow.transaction() as tx:
                pending = tx.read("remember_pending", memory_id)
                count = len(list(tx.rows("remember_current")))
                tasks = [row for _, row in tx.rows("tasks")]
            if pending["state"] == "processed":
                break
            if any(row["record"]["state"] in {"failed", "attention_required"} for row in tasks):
                break
            time.sleep(0.05)
        assert pending["state"] == "processed", [
            (row["record"]["kind"], row["record"]["state"], row.get("terminal_reason"))
            for row in tasks
        ]
        assert count == 2
        assert any("ConsolidatedMemory" in json.dumps(call["tools"]) for call in calls)
    assert adapter.manager.model._closed
