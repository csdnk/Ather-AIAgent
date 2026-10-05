"""Cloud URL trust boundaries and mounted Agent routes."""

import pytest

from aether_platform.configuration import RuntimeSettings


def cloud(**updates):
    values = dict(
        mode="cloud", public_origin="https://aether.example.com", path_prefix="/agent",
        issuer="https://aether.example.com/identity/realms/aether-lab",
        identity_connect_issuer="http://aether-keycloak:8080/identity/realms/aether-lab",
        management_url="https://aether.example.com/app/default%20workspace/aether-admin",
        database_dsn="postgresql://platform:secret@aether-platform-db:5432/platform",
    )
    values.update(updates)
    return RuntimeSettings.from_config(values)


def test_cloud_public_identity_and_transport_are_separate():
    settings = cloud()
    assert settings.secure_cookie is True
    assert settings.cookie_path == "/agent/"
    assert settings.issuer.startswith("https://")
    assert settings.connect_issuer.startswith("http://aether-keycloak:")


@pytest.mark.parametrize("change", [
    {"public_origin": "http://aether.example.com"},
    {"public_origin": "https://aether.example.com/agent"},
    {"issuer": "http://aether.example.com/identity/realms/aether-lab"},
    {"identity_connect_issuer": "http://aether-keycloak:8080/other/realms/aether-lab"},
    {"identity_connect_issuer": "http://user:password@aether-keycloak:8080/identity/realms/aether-lab"},
    {"database_dsn": "postgresql://user:pass@localhost:19432/platform"},
    {"path_prefix": "//other.example"},
    {"mode": "production-typo"},
])
def test_cloud_rejects_unsafe_or_ambiguous_configuration(change):
    with pytest.raises(ValueError):
        cloud(**change)


def test_lab_defaults_stay_local_and_reject_remote_database():
    settings = RuntimeSettings.from_config({
        "issuer": "http://localhost:19080/realms/aether-lab",
        "database_dsn": "postgresql://user:pass@127.0.0.1:19432/aether_platform_lab",
    })
    assert settings.origin == "http://localhost:19010"
    assert not settings.secure_cookie
    with pytest.raises(ValueError):
        RuntimeSettings.from_config({"issuer": settings.issuer, "database_dsn": "postgresql://remote/db"})
