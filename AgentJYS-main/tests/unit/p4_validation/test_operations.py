"""Observe real adapter requests; a replayed POST is always a regression."""

import httpx
import pytest

from aether_p4_simulator.validation.errors import ValidationError
from aether_p4_simulator.validation.models import RememberReceipt
from aether_p4_simulator.validation.operations import resolve_operation

from .test_client import PROCESSING, RECEIPT, RECORD_REF, TASK, client_for, invoke

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def operation(state, effect="not_started", job="job_1"):
    return {
        **TASK,
        "task_id": job,
        "state": state,
        "effect_status": effect,
        "lease": (
            {"owner_id": "w1", "token": "lease_1", "until": "2026-10-01T00:00:00.000Z"}
            if state == "running"
            else None
        ),
        "result_ref": RECORD_REF if state == "succeeded" else None,
        "temporal": {"workflow_id": None, "binding": None, "diagnostic": None},
    }


def pending(job="job_1"):
    return httpx.Response(400, json={"code": "REQUEST_IN_PROGRESS"}, headers={"X-P3-Job-ID": job})


@pytest.mark.parametrize(
    "responses,want",
    [
        ([httpx.Response(200, json=RECEIPT)], None),
        (
            [
                pending(),
                httpx.Response(200, json=operation("running", "unknown")),
                httpx.Response(200, json=operation("succeeded", "confirmed")),
                httpx.Response(200, json=RECEIPT),
            ],
            None,
        ),
        (
            [
                pending(),
                httpx.Response(200, json=operation("succeeded", "confirmed")),
                pending(),
                httpx.Response(200, json=operation("succeeded", "confirmed")),
                httpx.Response(200, json=RECEIPT),
            ],
            None,
        ),
        ([pending(), httpx.Response(200, json=operation("pending"))], "observation_timeout"),
        (
            [pending(), httpx.Response(200, json=operation("recovery_wait", "unknown"))],
            "observation_timeout",
        ),
        (
            [pending(), httpx.Response(200, json=operation("attention_required", "unknown"))],
            "operation_unconfirmed",
        ),
        (
            [pending(), httpx.Response(200, json=operation("failed", "no_effect"))],
            "operation_failed",
        ),
        (
            [pending(), httpx.Response(200, json=operation("cancelled", "no_effect"))],
            "operation_failed",
        ),
        (
            [
                pending(),
                httpx.Response(200, json=operation("succeeded", "confirmed")),
                httpx.Response(200, json={}),
            ],
            "upstream_protocol_error",
        ),
        (
            [pending(), httpx.Response(200, json=operation("pending", job="wrong"))],
            "upstream_protocol_error",
        ),
        (
            [
                pending(),
                httpx.Response(200, json=operation("succeeded", "confirmed")),
                pending("wrong"),
            ],
            "upstream_protocol_error",
        ),
        ([pending(), httpx.Response(403, json={"message": "secret"})], "forbidden"),
    ],
)
def test_resolve_observes_original_job_without_replaying(responses, want):
    seen, published = [], []
    clock = Clock()

    def handler(request):
        seen.append(request)
        return responses[min(len(seen) - 1, len(responses) - 1)]

    with client_for(handler) as client:

        def run():
            return resolve_operation(
                client,
                lambda: invoke(client, "remember"),
                RememberReceipt,
                operation_id="op-1",
                write=True,
                wait_seconds=2,
                clock=clock,
                sleep=clock.sleep,
                on_pending=published.append,
            )

        if want:
            with pytest.raises(ValidationError) as captured:
                run()
            assert captured.value.code == want
            assert captured.value.operation_id == "op-1"
            if want != "operation_failed":
                assert captured.value.write_outcome == "unconfirmed"
        else:
            assert run().saved
    assert sum(r.method == "POST" for r in seen) == 1
    assert all(
        r.method == "GET" and r.url.path in {"/p3/operations/job_1", "/p3/operations/job_1/result"}
        for r in seen[1:]
    )
    assert clock.now <= 2
    if len(responses) > 1:
        assert published == ["job_1"]
        assert all(0 < r.extensions["timeout"]["read"] <= 2 for r in seen[1:])


@pytest.mark.parametrize("at", [1, 2])
def test_network_failure_never_reposts(at):
    requests = []
    clock = Clock()

    def handler(request):
        requests.append(request)
        if len(requests) == at:
            raise httpx.ReadTimeout("secret", request=request)
        return pending()

    with client_for(handler) as client, pytest.raises(ValidationError) as error:
        resolve_operation(
            client,
            lambda: invoke(client, "remember"),
            RememberReceipt,
            operation_id="op-1",
            write=True,
            clock=clock,
            sleep=clock.sleep,
        )
    assert error.value.code == "upstream_timeout"
    assert error.value.write_outcome == "unconfirmed"
    assert len(requests) == at
    assert sum(r.method == "POST" for r in requests) == 1


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
@pytest.mark.parametrize("method", ["processing", "operation", "operation_result", "resolve"])
def test_bad_budget_rejected_before_transport(timeout, method):
    with client_for(lambda _: pytest.fail("invalid timeout reached transport")) as client:
        with pytest.raises(ValidationError) as error:
            if method == "resolve":
                resolve_operation(
                    client,
                    lambda: invoke(client, "remember"),
                    RememberReceipt,
                    operation_id="op-1",
                    write=True,
                    wait_seconds=timeout,
                )
            elif method == "operation_result":
                client.operation_result("job_1", RememberReceipt, timeout_seconds=timeout)
            else:
                getattr(client, method)("job_1", timeout_seconds=timeout)
        assert error.value.code == "invalid_argument"


def test_processing_timeout_is_per_request_not_shared():
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json=PROCESSING)

    with client_for(handler) as client:
        client.processing("m-1", timeout_seconds=0.2)
        client.processing("m-1")
    assert seen == [0.2, 60]
