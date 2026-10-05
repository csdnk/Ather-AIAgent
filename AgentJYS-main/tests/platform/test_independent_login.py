"""Agent form login and Budibase authentication remain independent."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ORIGIN = "http://localhost:19010"


@pytest.fixture
def settings():
    filename = os.environ.get("AETHER_PLATFORM_LAB_CONFIG")
    if not filename:
        pytest.skip("explicit isolated identity lab required")
    path = Path(filename)
    return path, json.loads((path.parent / "test-accounts.private.json").read_text())


def test_agent_login_is_same_page_and_does_not_touch_admin_cookies(settings):
    from aether_platform.auth.bff import create_lab_app

    path, passwords = settings
    with TestClient(create_lab_app(path), base_url=ORIGIN) as client:
        client.cookies.set("budibase:auth", "independent-admin-session")
        response = client.post(
            "/auth/login",
            headers={"origin": ORIGIN},
            json={"username": "user_a", "password": passwords["user_a"]},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert "location" not in response.headers
        assert not any("budibase" in v for v in response.headers.get_list("set-cookie"))
        assert client.cookies.get("budibase:auth") == "independent-admin-session"
        me = client.get("/auth/me").json()
        assert me["role"] == "user"
        assert "management_url" not in me
        assert passwords["user_a"] not in response.text
        for url in ("/ops-api/status", "/admin/users", "/admin-api/users"):
            assert client.get(url).status_code == 404
        response = client.post(
            "/auth/logout", headers={"origin": ORIGIN, "x-csrf-token": me["csrf_token"]}
        )
        assert response.status_code == 200
        assert client.cookies.get("budibase:auth") == "independent-admin-session"
        assert client.get("/auth/me").status_code == 401


def test_agent_login_rejects_wrong_password_admin_and_foreign_origin(settings):
    from aether_platform.auth.bff import create_lab_app

    path, passwords = settings
    with TestClient(create_lab_app(path), base_url=ORIGIN) as client:
        body = {"username": "user_a", "password": passwords["user_a"]}
        assert client.post("/auth/login", json=body).status_code == 403
        assert (
            client.post(
                "/auth/login", headers={"origin": "https://other.invalid"}, json=body
            ).status_code
            == 403
        )
        body["password"] = "invalid-test-password"
        assert client.post("/auth/login", headers={"origin": ORIGIN}, json=body).status_code == 401
        body = {"username": "platform_admin", "password": passwords["platform_admin"]}
        response = client.post("/auth/login", headers={"origin": ORIGIN}, json=body)
        assert response.status_code == 403
        assert client.get("/auth/me").status_code == 401


def test_management_entry_needs_no_agent_login(settings):
    from aether_platform.auth.bff import create_lab_app

    with TestClient(create_lab_app(settings[0]), base_url=ORIGIN) as client:
        response = client.get("/management", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("http://localhost:19000/")
        assert not response.headers.get_list("set-cookie")


def test_form_login_rate_limit_stops_upstream_attempts(settings, monkeypatch):
    from types import SimpleNamespace

    import httpx

    from aether_platform.auth import bff

    calls = []

    class IdentityClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def close(self):
            pass

        def request(self, method, url, **kwargs):
            calls.append(url)
            return httpx.Response(401)

    monkeypatch.setattr(
        bff, "httpx", SimpleNamespace(Client=IdentityClient, HTTPError=httpx.HTTPError)
    )
    with TestClient(bff.create_lab_app(settings[0]), base_url=ORIGIN) as client:
        for _ in range(15):
            response = client.post(
                "/auth/login",
                headers={"origin": ORIGIN},
                json={"username": "limit-test", "password": "incorrect"},
            )
            assert response.status_code == 401
        response = client.post(
            "/auth/login",
            headers={"origin": ORIGIN, "x-forwarded-for": "other-peer"},
            json={"username": "limit-test", "password": "incorrect"},
        )
        assert response.status_code == 429
        assert len(calls) == 15


def test_cloud_gateway_does_not_share_account_login_quota(settings, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    import httpx

    from aether_platform.auth import bff

    class IdentityClient:
        def __init__(self, **kwargs):
            pass

        def close(self):
            pass

        def request(self, *args, **kwargs):
            return httpx.Response(401)

    config = json.loads(settings[0].read_text())
    cloud_settings = replace(bff.RuntimeSettings.from_config(config), secure_cookie=True)
    monkeypatch.setattr(bff.RuntimeSettings, "from_config", lambda config: cloud_settings)
    monkeypatch.setattr(
        bff, "httpx", SimpleNamespace(Client=IdentityClient, HTTPError=httpx.HTTPError)
    )
    with TestClient(bff.create_lab_app(settings[0]), base_url=ORIGIN) as client:
        for index in range(20):
            response = client.post(
                "/auth/login", headers={"origin": ORIGIN},
                json={"username": f"tester-{index}", "password": "incorrect"},
            )
            assert response.status_code == 401
        for _ in range(14):
            assert client.post(
                "/auth/login", headers={"origin": ORIGIN},
                json={"username": "tester-0", "password": "incorrect"},
            ).status_code == 401
        assert client.post(
            "/auth/login", headers={"origin": ORIGIN, "x-forwarded-for": "other-peer"},
            json={"username": "tester-0", "password": "incorrect"},
        ).status_code == 429
