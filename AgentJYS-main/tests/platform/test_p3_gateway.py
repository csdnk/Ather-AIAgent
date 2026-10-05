import httpx
import pytest

from aether_platform.directory import Actor
from aether_platform.p3 import P3Client, P3Error

ACTOR = Actor("u1", "issuer", "subject", "t1", "user", 1)


def test_cluster_http_requires_explicit_matching_service_hostname():
    host = "aether-agent-p3.aether-p3-demo.svc.cluster.local"
    with pytest.raises(ValueError):
        P3Client("http://" + host + ":8080", "token", ACTOR)
    with P3Client("http://" + host + ":8080", "token", ACTOR, trusted_http_host=host):
        pass
    for url in ["http://other.svc.cluster.local:8080", "http://example.com"]:
        with pytest.raises(ValueError):
            P3Client(url, "token", ACTOR, trusted_http_host=host)
    with pytest.raises(ValueError):
        P3Client("http://example.com", "token", ACTOR, trusted_http_host="example.com")


def test_original_sources_are_deduplicated_bounded_and_checked():
    import json

    seen = []

    def handle(request):
        source = json.loads(request.content)
        seen.append(source)
        return httpx.Response(
            200, json={"source": source, "content": "x" * 4000, "is_complete": True}
        )

    sources = [{"source_id": str(i)} for i in range(5)]
    pack = {"groups": [{"items": [{"sources": [sources[0], *sources]}]}]}
    with P3Client(
        "http://127.0.0.1:19020", "token", ACTOR, transport=httpx.MockTransport(handle)
    ) as p3:
        result = p3.source_excerpts(pack)
    assert len(result) == len(seen) == 3
    assert all(len(item["excerpt"]) == 3000 and item["truncated"] for item in result)


def test_scoped_identity_and_pending_command_uses_original_job():
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer user-token"
        assert request.headers["X-P3-Tenant"] == "t1"
        if request.method == "POST":
            return httpx.Response(
                400, json={"code": "REQUEST_IN_PROGRESS"}, headers={"X-P3-Job-ID": "job1"}
            )
        return httpx.Response(200, json={"saved": True})

    with P3Client(
        "http://127.0.0.1:19020", "user-token", ACTOR, transport=httpx.MockTransport(handle)
    ) as p3:
        assert p3.call("POST", "/p3/remember", body={}, operation_id="op1", kind="remember.save")[
            "saved"
        ]
    assert [r.url.path for r in requests] == ["/p3/remember", "/p3/operations/job1/result"]
    assert sum(r.method == "POST" for r in requests) == 1


def test_foreign_scope_is_rejected_even_if_upstream_returns_it():
    with (
        P3Client(
            "http://127.0.0.1:19020",
            "token",
            ACTOR,
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    json={
                        "principal_id": "u1",
                        "scope": {
                            "tenant_id": "t2",
                            "user_id": "u1",
                            "application_id": "agent-platform",
                            "agent_id": "aether",
                        },
                    },
                )
            ),
        ) as p3,
        pytest.raises(P3Error),
    ):
        p3.identity()


def test_redirect_and_pending_without_job_never_become_success():
    for response in [
        httpx.Response(302, headers={"Location": "https://untrusted.invalid"}),
        httpx.Response(400, json={"code": "REQUEST_IN_PROGRESS"}),
    ]:
        with (
            P3Client(
                "http://127.0.0.1:19020",
                "token",
                ACTOR,
                transport=httpx.MockTransport(lambda r, response=response: response),
                wait_seconds=0,
            ) as p3,
            pytest.raises(P3Error),
        ):
            p3.call("POST", "/p3/remember", body={}, operation_id="op1")
