from __future__ import annotations

import json
from typing import Any

import pytest

from aether_agent_memory.b2.b1_client import (
    B1EmbeddingServiceClient,
    B1EmbeddingServiceError,
)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


@pytest.mark.unit
def test_b2_client_translates_to_sidecar_and_normalizes_records() -> None:
    sent: dict[str, Any] = {}

    def opener(request: Any, *, timeout: int) -> _Response:
        sent.update(json.loads(request.data))
        assert timeout == 60
        return _Response(
            {
                "overall_status": "success",
                "results": [
                    {
                        "status": "success",
                        "request_id": "request-1",
                        "trace_id": "trace-1",
                        "source_id": "source-1",
                        "object_id": "object-1",
                        "embedding_model": "test-model",
                        "chunks": [
                            {
                                "chunk_id": "source-1:0000",
                                "chunk_index": 0,
                                "start_char": 0,
                                "end_char": 5,
                                "chunk_text": "hello",
                                "vector": [0.6, 0.8],
                            }
                        ],
                    }
                ],
            }
        )

    result = B1EmbeddingServiceClient("http://b1.test/v1/intercept", opener=opener).process(
        {
            "text": "hello world",
            "source_type": "document",
            "request_id": "request-1",
            "trace_id": "trace-1",
            "tenant_id": "tenant-1",
            "source_id": "source-1",
            "object_id": "object-1",
            "memory_id": "memory-1",
            "metadata": {"session_id": "session-1", "agent_id": "agent-1"},
        }
    )

    assert sent["metadata"] == {
        "session_id": "session-1",
        "agent_id": "agent-1",
        "memory_id": "memory-1",
    }
    assert "memory_id" not in sent
    assert result["records"][0]["chunk_text"] == "hello"
    assert result["records"][0]["metadata"]["agent_id"] == "agent-1"


@pytest.mark.unit
def test_b2_client_rejects_failed_b1_result() -> None:
    def opener(_request: Any, *, timeout: int) -> _Response:
        assert timeout == 60
        return _Response(
            {
                "results": [
                    {
                        "status": "failed",
                        "error_code": "B1_INVALID_REQUEST",
                        "error_message": "bad input",
                    }
                ]
            }
        )

    client = B1EmbeddingServiceClient("http://b1.test/v1/intercept", opener=opener)
    with pytest.raises(B1EmbeddingServiceError, match="B1_INVALID_REQUEST"):
        client.process(
            {
                "text": "hello",
                "source_type": "document",
                "request_id": "request-1",
                "tenant_id": "tenant-1",
                "source_id": "source-1",
            }
        )
