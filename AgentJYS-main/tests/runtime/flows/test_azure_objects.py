"""Object adapter contracts with controlled S3 responses, not real Ceph acceptance."""

import io
from hashlib import sha256

import pytest
from botocore.exceptions import ReadTimeoutError
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from aether_agent_memory.remember.basic.ceph_p2 import CephP2Config
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError


@pytest.fixture
def objects(monkeypatch):
    from aether_agent_memory.runtime.storage.objects import CephObjects

    monkeypatch.setenv("CEPH_ACCESS_KEY_ID", "contract-access")
    monkeypatch.setenv("CEPH_SECRET_ACCESS_KEY", "contract-secret")
    provider = CephObjects(
        CephP2Config(endpoint="https://ceph.example", bucket="p3-contract"),
        namespace="test-a",
    )
    yield provider
    provider.close()


def response(body, **overrides):
    return {
        "Body": StreamingBody(io.BytesIO(body), len(body)),
        "ContentLength": len(body),
        "Metadata": {"sha256": sha256(body).hexdigest()},
        **overrides,
    }


@pytest.mark.parametrize("operation", ["verified_text", "remember_verified"])
def test_in_process_body_cache_cannot_bypass_ceph_resource_binding(objects, tmp_path, operation):
    from aether_agent_memory.remember.basic.content import Bodies
    from aether_agent_memory.remember.basic.policy import RememberPolicy
    from aether_agent_memory.runtime.contracts.models import Scope

    bodies = Bodies(tmp_path, RememberPolicy(), p2=objects)
    scope = Scope(tenant_id="t1", application_id="app", user_id="u1", agent_id="agent")
    location = bodies.location(scope, "same bytes")
    bodies.remember_verified(location, "same bytes")
    foreign = location.model_copy(update={"provider_instance_id": "another-environment"})
    with pytest.raises(FoundationError) as error:
        if operation == "verified_text":
            bodies.verified_text(foreign)
        else:
            bodies.remember_verified(foreign, "same bytes")
    assert error.value.code == ErrorCode.VERSION_CONFLICT


@pytest.mark.parametrize("foreign", [False, True])
async def test_source_range_uses_bound_ceph_bytes_without_local_spool(objects, tmp_path, foreign):
    from types import SimpleNamespace

    from test_postgres_observability import people

    from aether_agent_memory.remember.basic.content import Bodies
    from aether_agent_memory.remember.basic.policy import RememberPolicy
    from aether_agent_memory.remember.basic.sources import SourceAccess
    from aether_agent_memory.remember.contracts.models import SourceRef
    from azure_test_runtime import Foundation

    # Component metadata only; S3 transport is controlled, not real Ceph evidence.
    foundation = Foundation(tmp_path / "component.db")
    try:
        people(foundation)
        ctx = foundation.identity.context("alice")
        policy = RememberPolicy()
        bodies = Bodies(tmp_path / "unused-spool", policy, p2=objects)
        owner = SimpleNamespace(
            uow=foundation.uow, identity=foundation.identity, bodies=bodies, policy=policy
        )
        access = SourceAccess(owner)
        text = "a中文z"
        payload = text.encode()
        location = bodies.location(ctx.principal.home_scope, text)
        if foreign:
            location = location.model_copy(update={"provider_instance_id": "foreign-environment"})
        source = SourceRef(
            source_id="source",
            source_version=1,
            content_hash=sha256(payload).hexdigest(),
            locator="test",
        )
        with foundation.uow.transaction() as tx:
            tx.write(
                "remember_sources",
                source.source_id,
                {
                    "scope": ctx.principal.home_scope.model_dump(mode="json"),
                    "valid": True,
                    "ref": source.model_dump(mode="json"),
                    "revision": 1,
                    "original_location": location.model_dump(mode="json"),
                },
            )
            access.register(tx, source, text)
        with Stubber(objects.transport.client) as stub:
            if foreign:
                with pytest.raises(FoundationError) as error:
                    await access.read(ctx, source, 1, 3)
                assert error.value.code == ErrorCode.VERSION_CONFLICT
            else:
                stub.add_response(
                    "get_object",
                    response(
                        payload,
                        ContentRange="bytes 0-7/8",
                        ResponseMetadata={"HTTPStatusCode": 206},
                    ),
                    {
                        "Bucket": "p3-contract",
                        "Key": "aether/test-a/v1/" + location.object_key,
                        "Range": "bytes=0-7",
                    },
                )
                result = await access.read(ctx, source, 1, 3)
                assert result["content"] == "中文"
                assert result["range_hash"] == sha256("中文".encode()).hexdigest()
            stub.assert_no_pending_responses()
        assert not list(bodies.root.iterdir())
    finally:
        foundation.close()


def get_parameters(namespace="test-a"):
    return {"Bucket": "p3-contract", "Key": f"aether/{namespace}/v1/body/a"}


def put_parameters(body, namespace="test-a"):
    return {
        **get_parameters(namespace),
        "Body": body,
        "ContentLength": len(body),
        "ContentType": "application/octet-stream",
        "ContentMD5": ANY,
        "Metadata": {"sha256": sha256(body).hexdigest()},
        "IfNoneMatch": "*",
    }


def test_write_confirms_original_bytes_and_uses_environment_prefix(objects):
    with Stubber(objects.transport.client) as stub:
        stub.add_response("put_object", {}, put_parameters(b"first"))
        stub.add_response("get_object", response(b"first"), get_parameters())
        objects.put_object_sync("body/a", b"first")
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "stored,code", [(b"first", None), (b"second", ErrorCode.IDEMPOTENCY_CONFLICT)]
)
def test_same_key_replay_compares_bytes_instead_of_overwriting(objects, stored, code):
    with Stubber(objects.transport.client) as stub:
        stub.add_client_error(
            "put_object",
            "PreconditionFailed",
            http_status_code=412,
            expected_params=put_parameters(b"first"),
        )
        stub.add_response("get_object", response(stored), get_parameters())
        if code is None:
            stub.add_response("get_object", response(stored), get_parameters())
            objects.put_object_sync("body/a", b"first")
        else:
            with pytest.raises(FoundationError) as error:
                objects.put_object_sync("body/a", b"first")
            assert error.value.code == code
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("stored,code", [(b"first", None), (None, ErrorCode.COMMIT_UNCONFIRMED)])
def test_lost_write_response_rechecks_same_object(objects, monkeypatch, stored, code):
    def lost(**kwargs):
        raise ReadTimeoutError(endpoint_url="https://ceph.example/contract-secret")

    monkeypatch.setattr(objects.transport.client, "put_object", lost)
    with Stubber(objects.transport.client) as stub:
        if stored is not None:
            stub.add_response("get_object", response(stored), get_parameters())
        else:
            stub.add_client_error(
                "get_object", "NoSuchKey", http_status_code=404, expected_params=get_parameters()
            )
        if code is None:
            objects.put_object_sync("body/a", b"first")
        else:
            with pytest.raises(FoundationError) as error:
                objects.put_object_sync("body/a", b"first")
            assert error.value.code == code
            assert "contract-secret" not in str(error.value)


@pytest.mark.parametrize("kind", ["hash", "length", "range"])
def test_corrupt_response_is_rejected(objects, kind):
    overrides = {"Metadata": {"sha256": "0" * 64}} if kind == "hash" else {}
    if kind == "length":
        overrides["ContentLength"] = 8
    if kind == "range":
        overrides.update(ContentRange="bytes 1-5/6", ResponseMetadata={"HTTPStatusCode": 206})
    with Stubber(objects.transport.client) as stub:
        params = get_parameters()
        if kind == "range":
            params["Range"] = "bytes=0-4"
        stub.add_response("get_object", response(b"first", **overrides), params)
        with pytest.raises(FoundationError) as error:
            if kind == "range":
                objects.read_range_sync("body/a", 0, 5)
            else:
                objects.get_object_sync("body/a")
        assert error.value.code == ErrorCode.CONTRACT_VIOLATION


def test_missing_object_differs_from_bucket_or_authorization_failure(objects):
    with Stubber(objects.transport.client) as stub:
        stub.add_client_error(
            "get_object", "NoSuchKey", http_status_code=404, expected_params=get_parameters()
        )
        assert objects.get_object_sync("body/a") is None
        for status, code in [(404, "NoSuchBucket"), (403, "AccessDenied")]:
            stub.add_client_error(
                "get_object", code, http_status_code=status, expected_params=get_parameters()
            )
            with pytest.raises(FoundationError) as error:
                objects.get_object_sync("body/a")
            assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE


def test_deletion_and_read_use_the_same_environment_namespace(objects):
    from aether_agent_memory.runtime.storage.objects import CephObjects

    other = CephObjects(objects.transport.config, namespace="test-b")
    try:
        with Stubber(objects.transport.client) as first, Stubber(other.transport.client) as second:
            first.add_response("delete_object", {}, get_parameters())
            second.add_response(
                "get_object", response(b"other environment"), get_parameters("test-b")
            )
            objects.delete_object_sync("body/a")
            assert other.get_object_sync("body/a") == b"other environment"
    finally:
        other.close()


@pytest.mark.parametrize("key", ["../body", "/body/a", "body/../a", "body\\a", "", "body//a"])
def test_invalid_logical_key_cannot_escape_namespace(objects, key):
    with pytest.raises(FoundationError) as error:
        objects.get_object_sync(key)
    assert error.value.code == ErrorCode.INVALID_ARGUMENT


def test_public_endpoint_requires_verified_https_before_loading_credentials():
    from aether_agent_memory.runtime.storage.objects import CephObjects

    with pytest.raises(ValueError, match="HTTPS"):
        CephObjects(
            CephP2Config(endpoint="http://20.191.146.177:8080", bucket="p3"), namespace="test-a"
        )


def test_explicit_http_opt_in_still_requires_named_s3_credentials(monkeypatch):
    from aether_agent_memory.runtime.storage.objects import CephObjects

    monkeypatch.delenv("TEST_HTTP_CEPH_ACCESS", raising=False)
    monkeypatch.delenv("TEST_HTTP_CEPH_SECRET", raising=False)
    with pytest.raises(ValueError, match="credential"):
        CephObjects(
            CephP2Config(
                endpoint="http://20.191.146.177:8080",
                bucket="p3",
                access_key_env="TEST_HTTP_CEPH_ACCESS",
                secret_key_env="TEST_HTTP_CEPH_SECRET",
            ),
            namespace="test-a",
            allow_insecure_http=True,
        )


def test_unknown_ca_file_is_rejected_before_network_access(tmp_path):
    with pytest.raises(ValueError, match="CA"):
        CephP2Config(
            endpoint="https://ceph.example", bucket="p3", ca_file=str(tmp_path / "missing.pem")
        )
