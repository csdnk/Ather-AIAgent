from __future__ import annotations

import pytest

from aether_agent_memory.b2.p2_bridge import embedding_records, p2_collection_for_scope


@pytest.mark.unit
def test_b2_records_carry_p2_traceability_and_unique_chunk_ids() -> None:
    records = embedding_records(
        {
            "task_id": "task-1",
            "memory_id": "memory-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "agent_id": "agent-1",
            "session_id": "session-1",
            "content_ref": "p2://p3-memory/b2/long-text/doc.txt",
            "object_id": "b2/long-text/doc.txt",
        },
        {
            "request_id": "request-1",
            "trace_id": "trace-1",
            "source_id": "source-1",
            "records": [
                {
                    "chunk_id": "source-1:0000",
                    "chunk_text": "hello",
                    "vector": [0.1, 0.2],
                    "embedding_model": "b1-test",
                    "metadata": {"start_char": 0, "end_char": 5},
                }
            ],
        },
        category="fact",
        keywords=["hello"],
    )

    assert records[0].chunk_id == "memory-1:source-1:0000"
    assert records[0].chunk_text == "hello"
    assert records[0].metadata == {
        "start_char": 0,
        "end_char": 5,
        "task_id": "task-1",
        "memory_id": "memory-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "agent_id": "agent-1",
        "session_id": "session-1",
        "content_ref": "p2://p3-memory/b2/long-text/doc.txt",
        "category": "fact",
        "keywords": ["hello"],
        "b1_chunk_id": "source-1:0000",
    }


@pytest.mark.unit
def test_p2_collection_is_stable_and_scope_isolated() -> None:
    first = p2_collection_for_scope("p3", "tenant-1", "user-1", "agent-1", 512)
    assert first == p2_collection_for_scope("p3", "tenant-1", "user-1", "agent-1", 512)
    assert first != p2_collection_for_scope("p3", "tenant-1", "user-2", "agent-1", 512)
    assert first != p2_collection_for_scope("p3", "tenant-1", "user-1", "agent-1", 1024)
