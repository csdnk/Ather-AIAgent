"""Real BGE + Azure storage + Temporal; controlled text processing remains.

This verifies the native vector consumer and HTTP recovery, not formal P2
databases, a real language model, model quality over a corpus, or production HA.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import (
    command,
    eventually,
    headers,
)
from tests.integration.test_current_p2_http import (
    configuration as configuration,
)

from aether_agent_memory.runtime.storage.vectors import AzureVectors
from azure_component_service import Service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("P3_TEST_NATIVE_CONFIG"),
        reason="explicit native model config required",
    ),
]


@pytest.mark.parametrize("sources", ["working", "long_term"])
def test_native_p2_semantic_recall_and_service_reconstruction(configuration, sources):
    config = configuration.model_copy(
        update={
            "embedding_profile": "native",
            "embedding_config": Path(os.environ["P3_TEST_NATIVE_CONFIG"]),
        }
    )
    content = "用户偏爱无糖咖啡。"
    body = {
        "source": {
            "kind": "conversation",
            "external_id": "native-source",
            "external_version": "1",
            "occurred_at": "2026-10-03T00:00:00.000Z",
        },
        "selection": {"session_id": "s1"},
        "content": {"kind": "text", "text": content},
    }
    request = {
        "query": "早上的饮品需要添加蔗糖吗？",
        "selection": {"session_id": "s1"} if sources == "working" else {},
        "sources": sources,
        "token_budget": 1000,
    }
    service = Service(config)
    assert isinstance(service.runtime.vectors, AzureVectors)
    assert service.runtime.native_embedding is not None
    assert service.runtime.native_embedding.space.dimensions == 512
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        saved, location = command(client, "/p3/remember", body, "native-save")
        repeated, repeated_location = command(client, "/p3/remember", body, "native-save")
        assert repeated == saved and repeated_location == location

        def ready():
            response = client.get("/p3/memories", headers=headers())
            assert response.status_code == 200, response.text
            return next(
                (
                    item for item in response.json()["items"]
                    if (item["kind"] == "working") == (sources == "working")
                    and item["projection_state"] == "ready"
                ), None
            )

        memory = eventually(ready)
        result, _ = command(client, "/p3/recall", request, "native-recall")
        assert content in result["rendered_context"]
        assert client.get(
            f"/p3/remember/{memory['ref']['memory_id']}", headers=headers(user="eve")
        ).status_code == 403
        if sources == "working":
            isolated, _ = command(
                client, "/p3/recall", {**request, "selection": {"session_id": "s2"}},
                "native-other-session",
            )
            assert isolated["rendered_context"] == ""
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.rows(service.runtime.vectors.projection_namespace)
        assert not list((config.data_dir / "bodies").glob("*"))
        assert not list((config.data_dir / "inputs").glob("*"))

    restarted = Service(config)
    assert isinstance(restarted.runtime.vectors, AzureVectors)
    with TestClient(restarted.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        replay = client.get(location + "/result", headers=headers())
        assert replay.status_code == 200 and replay.json() == saved
        replay = client.get(f"/p3/recalls/{result['recall_id']}/result", headers=headers())
        assert replay.status_code == 200 and replay.json() == result
        recalled, _ = command(client, "/p3/recall", request, "native-recall-after-restart")
        assert content in recalled["rendered_context"]
