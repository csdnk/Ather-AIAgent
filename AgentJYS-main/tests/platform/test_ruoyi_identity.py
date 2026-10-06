"""Opaque token validation must follow current authority and preserve old ownership."""

import importlib.util
import time

import httpx
import pytest


def test_ruoyi_identity_adapter_exists():
    assert importlib.util.find_spec("aether_platform.auth.ruoyi") is not None


def fixture(tmp_path):
    from aether_platform.auth.ruoyi import RuoyiIdentityVerifier

    secret = tmp_path / "client.secret"
    secret.write_text("test-only-client-secret")
    config = {
        "ruoyi": {
            "base_url": "http://127.0.0.1:48080",
            "issuer": "ruoyi:test",
            "client_id": "aether",
            "client_secret_file": str(secret),
            "allowed_client_ids": ["default"],
            "mappings": [
                {
                    "ruoyi_user_id": "17",
                    "ruoyi_tenant_id": "2",
                    "business_user_id": "old-user",
                    "business_tenant_id": "old-tenant",
                }
            ],
        }
    }
    state = {
        "user_id": "17",
        "tenant_id": "2",
        "user_enabled": True,
        "tenant_enabled": True,
        "role_codes": ["aether_user"],
        "permissions": ["aether:memory:read"],
    }
    token = {
        "user_id": 17,
        "tenant_id": 2,
        "user_type": 2,
        "client_id": "default",
        "exp": int(time.time()) + 300,
    }

    def respond(request):
        if request.url.path.endswith("/check-token"):
            assert not request.url.query
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"code": 0, "data": token})
        return httpx.Response(200, json={"code": 0, "data": state})

    class ExistingDirectory:
        def mapped_user(self, user_id):
            return {"id": "old-user", "tenant_id": "old-tenant", "version": 4}

    verifier = RuoyiIdentityVerifier(
        config, ExistingDirectory(), client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    return verifier, state, token, config


def test_preserves_old_business_ids_without_username_matching(tmp_path):
    verifier, state, token, _ = fixture(tmp_path)
    actor = verifier.verify_ruoyi_actor("opaque-token")
    assert (actor.id, actor.tenant_id, actor.role) == ("old-user", "old-tenant", "user")
    assert actor.permissions == ("aether:memory:read",)
    state["role_codes"] = ["aether_tenant_admin"]
    assert verifier.verify_ruoyi_actor("opaque-token").role == "tenant_admin"


@pytest.mark.parametrize(
    "change",
    [
        {"user_enabled": False},
        {"tenant_enabled": False},
        {"tenant_id": "3"},
        {"user_id": "99"},
        {"role_codes": []},
        {"role_codes": ["super_admin"]},
    ],
)
def test_current_identity_changes_fail_closed(tmp_path, change):
    from aether_platform.directory import AccessDeniedError

    verifier, state, _, _ = fixture(tmp_path)
    verifier.verify_ruoyi_actor("opaque-token")
    state.update(change)
    with pytest.raises(AccessDeniedError):
        verifier.verify_ruoyi_actor("opaque-token")


@pytest.mark.parametrize(
    "change",
    [
        {"exp": 1},
        {"client_id": "untrusted"},
        {"user_type": 1},
        {"tenant_id": 3},
    ],
)
def test_expiry_wrong_client_and_tenant_cannot_use_live_self(tmp_path, change):
    from aether_platform.directory import AccessDeniedError

    verifier, _, token, _ = fixture(tmp_path)
    token.update(change)
    with pytest.raises(AccessDeniedError):
        verifier.verify_ruoyi_actor("opaque-token")


def test_missing_mapping_never_creates_user(tmp_path):
    from aether_platform.auth.ruoyi import RuoyiIdentityVerifier
    from aether_platform.directory import AccessDeniedError

    verifier, _, _, config = fixture(tmp_path)
    config["ruoyi"]["mappings"] = []
    with pytest.raises((ValueError, AccessDeniedError)):
        RuoyiIdentityVerifier(config, verifier.directory, client=verifier.http).verify_ruoyi_actor(
            "opaque-token"
        )


def test_ruoyi_cloud_configuration_needs_no_keycloak(tmp_path):
    from aether_platform.configuration import RuntimeSettings

    _, _, _, config = fixture(tmp_path)
    config.update(
        mode="cloud",
        auth_provider="ruoyi",
        public_origin="https://aether.example.com",
        database_dsn="postgresql://platform:secret@postgres:5432/platform",
        management_url="https://aether.example.com/",
        path_prefix="/agent",
    )
    settings = RuntimeSettings.from_config(config)
    assert settings.issuer == "ruoyi:test"
    assert settings.secure_cookie


def test_ruoyi_bff_revalidates_session_and_rejects_tenant_input(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aether_platform.auth import ruoyi_bff
    from aether_platform.auth.ruoyi import RuoyiDirectory

    verifier, state, _, config = fixture(tmp_path)
    config.update(
        mode="lab",
        auth_provider="ruoyi",
        public_origin="http://localhost:19010",
        database_dsn="postgresql://platform:secret@127.0.0.1:19432/platform",
        management_url="http://localhost:19000/",
        path_prefix="",
    )
    monkeypatch.setattr(ruoyi_bff, "install_chat", lambda *a, **k: None)
    monkeypatch.setattr(ruoyi_bff, "install_memory", lambda *a, **k: None)
    monkeypatch.setattr(RuoyiDirectory, "ensure_ruoyi_user", lambda *a: None)
    monkeypatch.setattr(
        RuoyiDirectory,
        "mapped_user",
        lambda *a: {"id": "old-user", "tenant_id": "old-tenant", "version": 4},
    )
    app = ruoyi_bff.create_ruoyi_app(config, identity_client=verifier.http)
    original = app.state.ruoyi_verifier.remote

    def remote(method, path, **kwargs):
        if path.endswith("/login"):
            assert set(kwargs["json"]) == {"username", "password"}
            return {"accessToken": "opaque-token"}
        return original(method, path, **kwargs)

    monkeypatch.setattr(app.state.ruoyi_verifier, "remote", remote)
    client = TestClient(app, base_url="http://localhost:19010")
    headers = {"origin": "http://localhost:19010"}
    assert (
        client.post(
            "/auth/login",
            json={"username": "u", "password": "p", "tenant_id": "2"},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/auth/login", json={"username": "u", "password": "p"}, headers=headers
        ).status_code
        == 200
    )
    assert client.get("/auth/me").json()["user_id"] == "old-user"
    state["user_enabled"] = False
    assert client.get("/auth/me").status_code == 401


def test_new_native_user_receives_namespaced_business_ids(tmp_path):
    verifier, state, token, config = fixture(tmp_path)
    verifier.config["auto_provision"] = True
    state["user_id"] = token["user_id"] = 99

    class NewDirectory:
        def ensure_ruoyi_user(self, authority, binding, current):
            assert binding["business_user_id"] == "ry_user_99"
            assert binding["business_tenant_id"] == "old-tenant"

        def mapped_user(self, user_id):
            return {"id": user_id, "tenant_id": "old-tenant", "version": 1}

    verifier.directory = NewDirectory()
    assert verifier.verify_ruoyi_actor("opaque-token").id == "ry_user_99"


@pytest.mark.parametrize("end_first_session", ["revoke", "logout"])
def test_bff_same_user_sessions_revoke_and_logout_independently(
    tmp_path, monkeypatch, end_first_session
):
    from urllib.parse import parse_qs

    from fastapi.testclient import TestClient

    from aether_platform.auth import ruoyi_bff
    from aether_platform.auth.ruoyi import RuoyiDirectory

    _, state, claims, config = fixture(tmp_path)
    config.update(
        mode="lab",
        auth_provider="ruoyi",
        public_origin="http://localhost:19010",
        database_dsn="postgresql://platform:secret@127.0.0.1:19432/platform",
        management_url="http://localhost:19000/",
        path_prefix="",
    )
    monkeypatch.setattr(ruoyi_bff, "install_chat", lambda *a, **k: None)
    monkeypatch.setattr(ruoyi_bff, "install_memory", lambda *a, **k: None)
    monkeypatch.setattr(RuoyiDirectory, "ensure_ruoyi_user", lambda *a: None)
    monkeypatch.setattr(
        RuoyiDirectory,
        "mapped_user",
        lambda *a: {"id": "old-user", "tenant_id": "old-tenant", "version": 4},
    )
    issued = iter(["first-token", "second-token"])
    revoked = set()

    def respond(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"code": 0, "data": {"accessToken": next(issued)}})
        if request.url.path.endswith("/check-token"):
            credential = parse_qs(request.content.decode())["token"][0]
            return httpx.Response(
                200,
                json={"code": 401, "data": None}
                if credential in revoked
                else {"code": 0, "data": claims},
            )
        credential = request.headers["authorization"].removeprefix("Bearer ")
        if request.url.path.endswith("/logout"):
            revoked.add(credential)
            return httpx.Response(200, json={"code": 0, "data": True})
        assert request.url.path.endswith("/self")
        assert credential not in revoked
        return httpx.Response(200, json={"code": 0, "data": state})

    with httpx.Client(transport=httpx.MockTransport(respond)) as authority:
        app = ruoyi_bff.create_ruoyi_app(config, identity_client=authority)
        first = TestClient(app, base_url="http://localhost:19010")
        second = TestClient(app, base_url="http://localhost:19010")
        headers = {"origin": "http://localhost:19010"}
        for browser in (first, second):
            assert (
                browser.post(
                    "/auth/login", json={"username": "same-user", "password": "p"}, headers=headers
                ).status_code
                == 200
            )
        assert first.cookies["aether_session"] != second.cookies["aether_session"]
        first_me = first.get("/auth/me")
        second_me = second.get("/auth/me")
        assert first_me.status_code == second_me.status_code == 200
        assert first_me.json()["user_id"] == second_me.json()["user_id"] == "old-user"

        if end_first_session == "revoke":
            revoked.add("first-token")
        else:
            response = first.post(
                "/auth/logout",
                headers={**headers, "x-csrf-token": first_me.json()["csrf_token"]},
            )
            assert response.status_code == 200
            assert response.json()["identity_session_revoked"] is True
            assert revoked == {"first-token"}

        assert first.get("/auth/me").status_code == 401
        assert second.get("/auth/me").status_code == 200
