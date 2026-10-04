"""Storage selection is independent of deployment profile; no reference fallback."""

import json

import pytest
from pydantic import ValidationError

from aether_agent_memory.runtime.flows.config import ServiceConfiguration
from azure_configuration_support import settings


def azure_settings(tmp_path, profile="test"):
    return settings(tmp_path, profile)


@pytest.mark.parametrize("profile", ["development", "test", "staging", "production"])
def test_azure_profile_uses_explicit_four_backends_without_p2_endpoint(tmp_path, profile):
    config = ServiceConfiguration.model_validate(azure_settings(tmp_path, profile))
    assert config.storage_mode == "azure" and config.metadata_backend == "postgresql"
    assert config.p2_endpoint is None
    assert config.azure_storage.namespace == profile


@pytest.mark.parametrize("missing", ["postgres", "redis", "milvus", "ceph"])
def test_azure_configuration_rejects_incomplete_bundle(tmp_path, missing):
    value = azure_settings(tmp_path)
    del value["azure_storage"][missing]
    with pytest.raises(ValidationError, match=missing):
        ServiceConfiguration.model_validate(value)


@pytest.mark.parametrize(
    "extra", [{"metadata_backend": "reference"}, {"p2_endpoint": "p2.internal:50052"}]
)
def test_azure_configuration_rejects_reference_or_p2_mixture(tmp_path, extra):
    with pytest.raises(ValidationError) as rejected:
        ServiceConfiguration.model_validate({**azure_settings(tmp_path), **extra})
    if "metadata_backend" in extra:
        assert rejected.value.errors()[0]["loc"] == ("metadata_backend",)
        assert rejected.value.errors()[0]["type"] == "literal_error"
    else:
        assert "Azure" in str(rejected.value)


@pytest.mark.parametrize("resource", ["milvus", "ceph"])
def test_azure_public_endpoints_require_https(tmp_path, resource):
    value = azure_settings(tmp_path)
    field = "uri" if resource == "milvus" else "endpoint"
    value["azure_storage"][resource][field] = "http://20.191.146.177:8080"
    with pytest.raises(ValidationError, match="HTTPS"):
        ServiceConfiguration.model_validate(value)


def test_explicit_ceph_http_configuration_is_accepted_without_weakening_milvus(tmp_path):
    value = azure_settings(tmp_path)
    value["azure_storage"]["ceph"].update(
        endpoint="http://20.191.146.177:8080", allow_insecure_http=True
    )
    config = ServiceConfiguration.model_validate(value)
    assert config.azure_storage.ceph.endpoint == "http://20.191.146.177:8080"
    value["azure_storage"]["milvus"]["uri"] = "http://milvus.internal:19530"
    with pytest.raises(ValidationError, match="HTTPS"):
        ServiceConfiguration.model_validate(value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://user:password@ceph.example",
        "http://ceph.example/path",
        "http://ceph.example?token=secret",
        "ftp://ceph.example",
    ],
)
def test_ceph_http_opt_in_still_rejects_invalid_origins(tmp_path, endpoint):
    value = azure_settings(tmp_path)
    value["azure_storage"]["ceph"].update(endpoint=endpoint, allow_insecure_http=True)
    with pytest.raises(ValidationError):
        ServiceConfiguration.model_validate(value)


def test_azure_cannot_silently_use_a_second_vector_configuration(tmp_path):
    value = azure_settings(tmp_path)
    value["recall_config"].write_text(json.dumps({"milvus_uri": "https://other.internal:19530"}))
    with pytest.raises(ValidationError, match="Azure"):
        ServiceConfiguration.model_validate(value)


def test_postgres_credentials_require_verified_tls_and_exact_schema(tmp_path, monkeypatch):
    config = ServiceConfiguration.model_validate(azure_settings(tmp_path))
    monkeypatch.setenv(
        "TEST_P3_POSTGRES_DSN", "host=pg.internal dbname=p3 user=p3 password=secret sslmode=disable"
    )
    with pytest.raises(ValueError, match="TLS"):
        config.azure_storage.postgres.resolve_dsn()


def test_missing_ceph_secret_stops_before_any_database_is_opened(tmp_path, monkeypatch):
    from aether_agent_memory.runtime.storage.azure import StorageProviders

    config = ServiceConfiguration.model_validate(azure_settings(tmp_path))
    monkeypatch.setenv(
        "TEST_P3_POSTGRES_DSN",
        "host=pg.internal dbname=p3 user=p3 password=secret sslmode=verify-full sslrootcert=ca.pem",
    )
    monkeypatch.setenv("TEST_REDIS_PASSWORD", "redis-contract-secret")
    monkeypatch.setenv("TEST_MILVUS_TOKEN", "milvus-contract-secret")
    monkeypatch.setenv("TEST_CEPH_ACCESS", "ceph-contract-access")
    monkeypatch.delenv("TEST_CEPH_SECRET", raising=False)
    with pytest.raises(ValueError, match="TEST_CEPH_SECRET"):
        StorageProviders(config.azure_storage, tmp_path / "anchor", config.remember)
    assert not (tmp_path / "anchor").exists()
    from aether_agent_memory.runtime.flows.application import Service

    with pytest.raises(ValueError, match="TEST_CEPH_SECRET"):
        Service(config)
    assert not config.data_dir.exists()
