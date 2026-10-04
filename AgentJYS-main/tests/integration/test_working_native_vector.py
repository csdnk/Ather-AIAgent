"""Working semantic recall through real BGE and the durable local vector backend."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_continuous_service import (
    configuration as configuration,
)
from tests.integration.test_continuous_service import eventually, headers, save

from azure_component_service import Service


@pytest.mark.skipif(
    not os.environ.get("P3_TEST_NATIVE_CONFIG"), reason="explicit native model config required"
)
def test_working_native_semantic_recall_and_restart(configuration):
    config = configuration.model_copy(
        update={
            "embedding_profile": "native",
            "embedding_config": Path(os.environ["P3_TEST_NATIVE_CONFIG"]),
        }
    )
    content = "用户偏爱无糖咖啡。"
    service = Service(config)
    with TestClient(service.app()) as client:
        response = save(client, content, "working_native_save")
        assert response.status_code == 200, response.text

        def working_ready():
            result = client.get("/p3/memories", headers=headers())
            assert result.status_code == 200, result.text
            return next(
                (
                    item
                    for item in result.json()["items"]
                    if item["kind"] == "working" and item["projection_state"] == "ready"
                ),
                None,
            )

        # The listing intentionally contains metadata, not hydrated plaintext.
        # Verify the exact body below through the public Recall response.
        eventually(working_ready, seconds=30)
        assert service.runtime.native_embedding.space.dimensions == 512
        request = {
            "query": "早上的饮品要不要放蔗糖？",
            "selection": {"session_id": "s1"},
            "sources": "working",
            "token_budget": 1000,
        }
        result = client.post("/p3/recall", headers=headers(), json=request)
        assert result.status_code == 200, result.text
        assert content in result.json()["rendered_context"]
        recall_id = result.json()["recall_id"]
        unrelated = client.post(
            "/p3/recall",
            headers=headers(),
            json={**request, "selection": {"session_id": "s2"}},
        )
        assert unrelated.status_code == 200, unrelated.text
        assert unrelated.json()["rendered_context"] == ""

    with TestClient(Service(config).app()) as client:
        replay = client.get(f"/p3/recalls/{recall_id}/result", headers=headers())
        assert replay.status_code == 200, replay.text
        assert content in replay.json()["rendered_context"]
        result = client.post("/p3/recall", headers=headers(), json=request)
        assert result.status_code == 200, result.text
        assert content in result.json()["rendered_context"]
