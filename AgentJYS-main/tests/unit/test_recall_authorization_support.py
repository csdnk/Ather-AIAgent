"""Strict HTTP peers test harness behavior, never product authorization."""

import httpx
import pytest

from recall_authorization_support import RecallHTTP, SafeEvidence, assert_denied


def test_original_job_failure_is_observed_without_resubmission():
    def peer(request):
        assert request.method == "GET"
        assert request.url.path == "/p3/operations/original"
        return httpx.Response(200, json={"state": "failed", "error_code": "FORBIDDEN"})

    with httpx.Client(transport=httpx.MockTransport(peer), base_url="http://target") as client:
        harness = RecallHTTP(client, "maintainer")
        assert harness.poll_job("original")["error_code"] == "FORBIDDEN"


def test_current_p3_pending_response_recovers_original_job_without_reposting():
    methods = []

    def peer(request):
        methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(
                400,
                json={"code": "REQUEST_IN_PROGRESS"},
                headers={
                    "Location": "/p3/operations/original",
                    "X-P3-Job-ID": "original",
                },
            )
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json={"recall_id": "original"})
        return httpx.Response(200, json={"state": "succeeded"})

    with httpx.Client(transport=httpx.MockTransport(peer), base_url="http://target") as client:
        assert RecallHTTP(client, "maintainer").command("/p3/recall", {}, "op", "reader") == (
            {"recall_id": "original"},
            "original",
        )
    assert methods == ["POST", "GET", "GET"]


def test_nonterminal_original_job_times_out_instead_of_passing():
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"state": "running"})),
            base_url="http://target",
        ) as client,
        pytest.raises(AssertionError, match="terminal"),
    ):
        RecallHTTP(client, "maintainer", timeout=0.01).poll_job("original")


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "FORBIDDEN", "detail": "用户喝咖啡不加糖。"},
        {"code": "FORBIDDEN", "debug": {"candidates": [{"memory_id": "private"}]}},
        {"code": "FORBIDDEN", "pack": {"outcome": "empty", "groups": []}},
        {"code": "FORBIDDEN", "content": "partial secret"},
    ],
)
def test_error_envelopes_cannot_hide_content_candidates_or_even_empty_packs(payload):
    with pytest.raises(AssertionError, match="leak"):
        assert_denied(httpx.Response(403, json=payload), 403, "FORBIDDEN")


def test_denial_requires_the_exact_target_contract():
    assert_denied(httpx.Response(403, json={"code": "FORBIDDEN"}), 403, "FORBIDDEN")
    with pytest.raises(AssertionError):
        assert_denied(httpx.Response(401, json={"code": "UNAUTHENTICATED"}), 403, "FORBIDDEN")


def test_async_admission_is_not_a_successful_control():
    seen = []

    def peer(request):
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(202, json={}, headers={"Location": "/p3/operations/original"})
        return httpx.Response(200, json={"state": "failed", "error_code": "FORBIDDEN"})

    with (
        httpx.Client(transport=httpx.MockTransport(peer), base_url="http://target") as client,
        pytest.raises(AssertionError, match="succeed"),
    ):
        RecallHTTP(client, "maintainer").command("/p3/recall", {}, "op", "reader")
    assert seen == [("POST", "/p3/recall"), ("GET", "/p3/operations/original")]


def test_f1_body_or_ref_mismatch_is_not_ready_evidence():
    ref = {"memory_id": "coffee", "version": 1}

    def peer(request):
        assert request.url.path == "/p3/remember/body"
        return httpx.Response(
            200,
            json={
                "memory": ref,
                "outcome": "read",
                "content": "用户喜欢乌龙茶。",
                "location": {"generation": "g1", "content_hash": "f" * 64},
                "guard": {"memory": ref, "body_hash": "f" * 64},
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(peer), base_url="http://target") as client,
        pytest.raises(AssertionError, match="body"),
    ):
        RecallHTTP(client, "maintainer").verify_body(ref, "用户喝咖啡不加糖。")


def test_evidence_omits_credentials_and_body_even_with_mixed_case_headers():
    evidence = SafeEvidence("RC-AUTH-02", "write-only")
    response = httpx.Response(
        403,
        json={
            "code": "FORBIDDEN",
            "detail": "secret-token 用户喝咖啡不加糖。",
        },
        headers={"Authorization": "Bearer secret-token", "X-Trace-ID": "a" * 32},
    )
    evidence.response("POST", "/p3/recall", response)
    serialized = str(evidence.data)
    assert "secret-token" not in serialized and "咖啡" not in serialized
    assert evidence.data["responses"][0]["trace_id"] == "a" * 32


@pytest.mark.parametrize("admission", [200, 202])
def test_successful_commands_always_retrieve_the_original_persisted_result(admission):
    seen = []
    receipt = {"saved": True, "operation_id": "save"}

    def peer(request):
        seen.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer writer"
        if request.method == "POST":
            assert request.headers["X-Operation-ID"] == "save"
            return httpx.Response(
                admission,
                json=receipt if admission == 200 else {},
                headers={
                    "Location": "/p3/operations/original",
                    "X-P3-Job-ID": "original",
                },
            )
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json=receipt)
        return httpx.Response(200, json={"state": "succeeded"})

    with httpx.Client(transport=httpx.MockTransport(peer), base_url="http://target") as client:
        assert RecallHTTP(client, "maintainer").command("/p3/remember", {}, "save", "writer") == (
            receipt,
            "original",
        )
    assert seen == [
        ("POST", "/p3/remember"),
        ("GET", "/p3/operations/original"),
        ("GET", "/p3/operations/original/result"),
    ]


def test_evidence_cannot_be_written_inside_the_project(tmp_path):
    from pathlib import Path

    project = Path(__file__).resolve().parents[3]
    with pytest.raises(ValueError, match="outside"):
        SafeEvidence("RC-AUTH-01", "missing").write(project / "outputs")
