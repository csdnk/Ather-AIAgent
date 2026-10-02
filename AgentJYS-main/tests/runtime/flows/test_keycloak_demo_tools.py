"""Recovery helpers must survive a management-token expiry during cleanup."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest


def load_demo():
    script = Path(__file__).resolve().parents[3] / "scripts/p3/keycloak_organizations_demo.py"
    spec = importlib.util.spec_from_file_location("organizations_demo_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_expired_admin_token_is_renewed_once_to_restore_client(tmp_path, monkeypatch):
    module = load_demo()
    (tmp_path / "credentials.json").write_text(
        json.dumps({"admin_user": "test-admin", "admin_password": "test-only"}), "utf-8"
    )
    tokens = []
    attempts = []

    def handler(request):
        if request.url.path.endswith("/token"):
            tokens.append("token-" + str(len(tokens) + 1))
            return httpx.Response(200, json={"access_token": tokens[-1]})
        assert request.method == "PUT"
        assert json.loads(request.content) == {"enabled": True}
        attempts.append(request.headers["authorization"])
        return httpx.Response(401 if len(attempts) == 1 else 204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: client)
    admin = module.Admin(tmp_path)
    try:
        assert admin.request("PUT", "/clients/directory", json={"enabled": True}).status_code == 204
        assert attempts == ["Bearer token-1", "Bearer token-2"]
        assert len(tokens) == 2
    finally:
        client.close()


def test_forbidden_admin_operation_is_not_retried(tmp_path, monkeypatch):
    module = load_demo()
    (tmp_path / "credentials.json").write_text(
        json.dumps({"admin_user": "test-admin", "admin_password": "test-only"}), "utf-8"
    )
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "test-token"})
        return httpx.Response(403)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: client)
    admin = module.Admin(tmp_path)
    try:
        with pytest.raises(RuntimeError, match=r"Keycloak operation failed:.*\(403\)"):
            admin.request("PUT", "/clients/directory", json={"enabled": True})
        assert len(paths) == 2  # login and one denied request, no permission-bypass retries
    finally:
        client.close()


def test_monitor_callbacks_cover_reload_and_logout_views(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[3] / "scripts/p3/keycloak_demo.py"
    spec = importlib.util.spec_from_file_location("keycloak_callback_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "configure_p3", lambda *args: None)
    monkeypatch.setattr(module, "save", lambda path, text: path.write_text(text, "utf-8"))
    directory = tmp_path / "identity"
    module.prepare(directory, tmp_path / "deployment")
    realm = json.loads((directory / "realm.json").read_text("utf-8"))
    client = next(c for c in realm["clients"] if c["clientId"] == "p3-monitor")
    root = "http://127.0.0.1:5173/"
    returns = {root, root + "?view=demo", root + "?view=monitor"}
    assert set(client["redirectUris"]) == returns | {root + "silent-check-sso.html"}
    assert set(client["attributes"]["post.logout.redirect.uris"].split("##")) == returns
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
