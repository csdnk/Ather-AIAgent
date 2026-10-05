import json

import httpx
import pytest

from aether_p4_simulator.demo.runner import run_library
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError

from ..p4_validation.test_operations import Clock
from .support import Upstream, fixed_definition

pytestmark = pytest.mark.unit


def test_fixed_story_uses_real_scoped_requests_and_actual_context():
    transport = Upstream()
    client = P3ValidationClient(
        "http://p3.test", "secret", transport=httpx.MockTransport(transport)
    )
    progress = []
    try:
        run_library(client, "demo_unit", progress.append, definition=fixed_definition())
    finally:
        client.close()
    saves = [json.loads(r.content) for r in transport.requests if r.url.path == "/p3/remember"]
    recalls = [json.loads(r.content) for r in transport.requests if r.url.path == "/p3/recall"]
    assert len(saves) == 3
    assert len(recalls) == 3
    assert recalls[0]["query"] == "图书馆一次最多能借几本，借多久？"
    assert recalls[0]["selection"] == {"task_id": "demo_unit", "session_id": "demo_unit_session"}
    assert recalls[0]["sources"] == "working"
    assert "5 本" not in recalls[0]["query"]
    assert "30 天" not in recalls[0]["query"]
    assert len({s["source"]["external_id"] for s in saves}) == 3
    assert transport.processing_reads == 3
    passed = [s for s in progress if s.state == "passed"]
    assert [s.id for s in passed] == [1, 2, 3, 4, 5, 6]
    assert passed[-1].response_text.startswith("真实P3上下文：")
    assert "机器学习入门" in passed[-1].response_text
    assert passed[-1].evidence.recall_id == "recall_3"


@pytest.mark.parametrize(
    "variant,code,posts",
    [
        ("not_saved", "upstream_protocol_error", 1),
        ("bad_receipt_scope", "scope_mismatch", 1),
        ("bad_processing_ref", "scope_mismatch", 1),
        ("projection_pending", "observation_timeout", 1),
        ("projection_failed", "processing_failed", 1),
        ("empty", "recall_check_failed", 3),
        ("wrong_content", "recall_check_failed", 3),
        ("foreign_scope", "scope_mismatch", 3),
        ("foreign_pack", "scope_mismatch", 3),
        ("foreign_memory", "scope_mismatch", 3),
        ("extra_foreign", "scope_mismatch", 3),
        ("degraded", "recall_check_failed", 3),
    ],
)
def test_failure_stops_dependent_calls(variant, code, posts):
    upstream = Upstream(variant)
    clock = Clock()
    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    progress = []
    try:
        with pytest.raises(ValidationError) as error:
            run_library(
                client,
                "demo_unit",
                progress.append,
                definition=fixed_definition(),
                wait_seconds=1,
                clock=clock,
                sleep=clock.sleep,
            )
    finally:
        client.close()
    assert error.value.code == code
    assert sum(r.method == "POST" for r in upstream.requests) == posts
    assert all(s.id <= (1 if posts == 1 else 3) for s in progress)
    if variant in {"foreign_scope", "foreign_pack", "foreign_memory", "extra_foreign"}:
        assert not any(s.response_text for s in progress if s.id == 3)
