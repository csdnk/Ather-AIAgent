"""Actual form login, business directory and independent Agent session checks."""

import importlib.util
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ORIGIN = "http://localhost:19010"


@pytest.mark.skipif(
    not os.environ.get("AETHER_PLATFORM_LAB_CONFIG"),
    reason="explicit seeded identity/PostgreSQL lab required",
)
def test_live_refresh_and_logout_revoke_background_lease(monkeypatch):
    import time

    from aether_platform.auth import bff
    from aether_platform.p3 import P3Error

    leases = []
    original = bff.SessionTokens

    def capture(*args, **kwargs):
        lease = original(*args, **kwargs)
        leases.append(lease)
        return lease

    monkeypatch.setattr(bff, "SessionTokens", capture)
    application = bff.create_lab_app(Path(os.environ["AETHER_PLATFORM_LAB_CONFIG"]))
    with TestClient(application, base_url=ORIGIN) as client:
        assert authenticate(client, "user_a").status_code == 200
        lease = leases[-1]
        expires = lease.session["expires"]
        lease.session["access_expires"] = time.time() - 1
        assert client.get("/auth/me").status_code == 200
        assert lease.session["access_expires"] > time.time() + 100
        assert lease.session["expires"] == expires
        assert client.post("/auth/logout", headers=csrf(client)).status_code == 200
        with pytest.raises(P3Error):
            lease.get()


def test_bff_adapter_exists():
    assert importlib.util.find_spec("aether_platform.auth.bff") is not None


@pytest.fixture
def app():
    config_path = os.environ.get("AETHER_PLATFORM_LAB_CONFIG")
    if not config_path:
        pytest.skip("explicit seeded identity/PostgreSQL lab required")
    from aether_platform.auth.bff import create_lab_app

    return create_lab_app(Path(config_path))


def authenticate(client, username):
    config_path = Path(os.environ["AETHER_PLATFORM_LAB_CONFIG"])
    passwords = json.loads((config_path.parent / "test-accounts.private.json").read_text())
    return client.post(
        "/auth/login",
        headers={"origin": ORIGIN},
        json={"username": username, "password": passwords[username]},
    )


def csrf(client):
    response = client.get("/auth/me")
    assert response.status_code == 200
    return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}


def test_user_login_hides_tenant_and_rejects_admin_api(app):
    with TestClient(app, base_url=ORIGIN) as client:
        assert authenticate(client, "user_a").status_code == 200
        response = client.get("/auth/me")
        assert response.status_code == 200
        assert response.json()["user_id"] == "demo_user_a"
        assert "tenant" not in response.text
        assert client.get("/admin/users").status_code == 404
        assert client.post("/admin/delegations", headers=csrf(client)).status_code == 404


def test_callback_csrf_and_logout_reject_replay(app):
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/auth/callback?code=forged&state=forged").status_code == 404
        assert authenticate(client, "user_a").status_code == 200
        header = csrf(client)
        cookie = client.cookies.get("aether_session")
        assert (
            client.post(
                "/auth/logout", headers={**header, "origin": "https://attacker.invalid"}
            ).status_code
            == 403
        )
        assert client.post("/auth/logout", headers={"origin": ORIGIN}).status_code == 403
        assert client.post("/auth/logout", headers=header).status_code == 200
        client.cookies.set("aether_session", cookie)
        assert client.get("/auth/me").status_code == 401


def test_logout_clears_stale_cookie_and_is_idempotent(app):
    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set("aether_session", "expired-session")
        response = client.post("/auth/logout", headers={"origin": ORIGIN})
        assert response.status_code == 200
        assert response.json()["logged_out"] is True
        assert "Max-Age=0" in response.headers["set-cookie"]
        assert client.post("/auth/logout", headers={"origin": ORIGIN}).status_code == 200
        assert client.get("/auth/me").status_code == 401


def test_logout_without_session_still_rejects_foreign_origin(app):
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post("/auth/logout").status_code == 403
        assert (
            client.post("/auth/logout", headers={"origin": "https://attacker.invalid"}).status_code
            == 403
        )


def test_disabled_tenant_and_disabled_account_cannot_enter(app):
    with TestClient(app, base_url=ORIGIN) as client:
        assert authenticate(client, "user_c").status_code == 403
        assert client.get("/auth/me").status_code == 401
        assert authenticate(client, "disabled_user_a").status_code == 401
        assert client.get("/auth/me").status_code == 401


def test_new_login_revokes_previous_session_before_account_switch(app):
    with TestClient(app, base_url=ORIGIN) as client, TestClient(app, base_url=ORIGIN) as stale:
        assert authenticate(client, "user_a").status_code == 200
        stale.cookies.update(client.cookies)
        assert authenticate(client, "user_b").status_code == 200
        assert stale.get("/auth/me").status_code == 401
        assert client.get("/auth/me").json()["user_id"] == "demo_user_b"


def test_validation_never_echoes_password(app):
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            "/auth/login",
            headers={"origin": ORIGIN},
            json={"username": "", "password": "private-sentinel-value"},
        )
        assert response.status_code == 422
        assert "private-sentinel-value" not in response.text


def test_independent_users_cannot_override_actor_with_headers(app):
    with TestClient(app, base_url=ORIGIN) as a, TestClient(app, base_url=ORIGIN) as b:
        assert authenticate(a, "user_a").status_code == 200
        assert authenticate(b, "user_b").status_code == 200
        response = a.get(
            "/auth/me", headers={"x-user-id": "demo_user_b", "x-tenant-id": "demo_tenant_b"}
        )
        assert response.json()["user_id"] == "demo_user_a"
        assert b.get("/auth/me").json()["user_id"] == "demo_user_b"
