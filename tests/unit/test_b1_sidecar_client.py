from __future__ import annotations

import json

import httpx
import pytest

from aether_agent_memory.b1.sidecar_client import SidecarEmbeddingClient, SidecarEmbeddingError


@pytest.mark.unit
async def test_sidecar_client_preserves_embedding_client_contract_and_batches() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/ready":
            return httpx.Response(
                200,
                json={
                    "status": "ready",
                    "dimension": 3,
                    "model": "test-sidecar-model",
                },
            )
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))
        items = payload["items"]
        return httpx.Response(
            200,
            json={
                "overall_status": "success",
                "results": [
                    {
                        "status": "success",
                        "embedding_dim": 3,
                        "chunks": [{"chunk_id": item["chunk_id"], "vector": [1, 2, 3]}],
                    }
                    for item in items
                ],
            },
        )

    client = SidecarEmbeddingClient(
        "http://sidecar.test",
        max_batch_items=2,
        transport=httpx.MockTransport(handler),
    )
    try:
        vectors = await client.embed(["one", "two", "three"])
        query = await client.embed_one("query")
    finally:
        await client.close()

    assert vectors == [[1.0, 2.0, 3.0]] * 3
    assert query == [1.0, 2.0, 3.0]
    assert len(calls) == 3
    assert all(
        payload["items"][0]["metadata"] == {"integration": "p3-runtime"}
        for _, payload in calls
    )
    assert calls[-1][1]["items"][0]["input_type"] == "query"
    assert client.dimension == 3
    assert client.model_name == "test-sidecar-model"


@pytest.mark.unit
async def test_sidecar_client_rejects_multiple_chunks_for_one_pipeline_item() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ready", "dimension": 3})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "status": "success",
                        "embedding_dim": 3,
                        "chunks": [
                            {"vector": [1, 2, 3]},
                            {"vector": [1, 2, 3]},
                        ],
                    }
                ]
            },
        )

    client = SidecarEmbeddingClient(
        "http://sidecar.test", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(SidecarEmbeddingError, match="multiple chunks"):
            await client.embed(["one"])
    finally:
        await client.close()
