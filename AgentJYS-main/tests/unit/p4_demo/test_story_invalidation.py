"""A stale-result check needs the exact HTTP and business-code pair."""

import httpx
import pytest

from aether_p4_simulator.demo.execution import StoryRun
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from aether_p4_simulator.validation.models import ContextPack

from .support import NOW, fixed_definition

pytestmark = pytest.mark.unit


def old_pack():
    return ContextPack.model_validate(
        {
            "recall_id": "owned_recall",
            "scope": {
                "tenant_id": "t1",
                "application_id": "a1",
                "user_id": "u1",
                "agent_id": "agent1",
                "task_id": "owned_run",
                "session_id": "owned_run_session",
            },
            "outcome": "empty",
            "selected_sources": ["working"],
            "coverage": {"working": "complete", "long_term": "not_requested"},
            "groups": [],
            "rendered_context": "",
            "token_budget": 3000,
            "tokens_used": 0,
            "tokenizer_id": "lexical",
            "policy_version": "v1",
            "degradation_reasons": [],
            "committed_at": NOW,
        }
    )


@pytest.mark.parametrize(
    "status,content,want",
    [
        (410, b"null", "upstream_protocol_error"),
        (410, b'{"code":"FORBIDDEN"}', "upstream_protocol_error"),
        (410, b"invalid JSON", "upstream_protocol_error"),
        (410, b'{"code":"MEMORY_GONE"}', "memory_gone"),
        (403, b'{"code":"RESULT_INVALIDATED"}', "forbidden"),
    ],
)
def test_other_errors_cannot_pass_the_story_old_result_check(status, content, want):
    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(status, content=content)),
    )
    run = StoryRun(
        client,
        "owned_run",
        "preference-update",
        None,
        lambda _: None,
        definition=fixed_definition("preference-update"),
    )
    try:
        with (
            pytest.raises(ValidationError) as error,
            run.step(5, "/p3/recalls/{recall_id}/result", method="GET"),
        ):
            run.old_result_invalid(old_pack())
        assert error.value.code == want
        assert run.current_step.state != "passed"
        assert not any(check.passed for check in run.current_step.checks)
        assert not run.current_step.response_text
    finally:
        client.close()


def test_exact_result_invalidation_passes_and_keeps_the_owned_recall_id():
    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(410, json={"code": "RESULT_INVALIDATED"})
        ),
    )
    run = StoryRun(
        client,
        "owned_run",
        "preference-update",
        None,
        lambda _: None,
        definition=fixed_definition("preference-update"),
    )
    try:
        with run.step(5, "/p3/recalls/{recall_id}/result", method="GET"):
            run.old_result_invalid(old_pack())
        assert run.current_step.state == "passed"
        assert run.evidence.recall_id == "owned_recall"
        assert run.current_step.checks[0].passed
    finally:
        client.close()
