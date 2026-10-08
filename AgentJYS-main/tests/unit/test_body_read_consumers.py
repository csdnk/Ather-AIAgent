"""Existing body HTTP consumers with real readers and isolated external I/O."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.http import attach_routes
from aether_agent_memory.runtime.foundation.requests import text_hash
from unit.test_body_address_reads import body_case as body_case


@pytest.mark.parametrize("hot", [True, False])
def test_existing_body_and_range_http_contracts_use_verified_address_reads(body_case, hot):
    p = body_case
    if hot:
        p.state.cold = OSError("cold body offline")
    else:
        p.change(cache_location=None)
    with p.transaction() as tx:
        before = tx.get(memory_ref(p.ref, versioned=True))
    app = FastAPI()
    # Authentication is a controlled fixture; route validation, serialization,
    # Remember, scope revalidation and storage adapter logic execute normally.
    attach_routes(app, p.reader, Depends(lambda: p.ctx))
    with TestClient(app) as client:
        body = client.post("/p3/remember/body", json=p.ref.model_dump(mode="json"))
        assert body.status_code == 200
        value = body.json()
        assert (value["outcome"], value["content"]) == ("read", "body")
        assert value["path"] == ("cache" if hot else "authority")
        location = p.record.cache_location if hot else p.record.body_location
        assert value["location"] == location.model_dump(mode="json")

        response = client.post(
            "/p3/remember/body/range?start=1&end=3", json=p.ref.model_dump(mode="json")
        )
        assert response.status_code == 200
        value = response.json()
        assert value["content"] == "od"
        assert value["representation"] == "text_range" and value["is_complete"] is False
        assert (value["start_char"], value["end_char"], value["total_chars"]) == (1, 3, 4)
        assert value["range_hash"] == text_hash("od")
        assert value["body_hash"] == text_hash("body")
    with p.transaction() as tx:
        assert tx.get(memory_ref(p.ref, versioned=True)) == before
        assert tx.rows("remember_cache_admission") == []
    if hot:
        assert p.state.cold_calls == 0
    else:
        assert p.state.redis_calls == 0
