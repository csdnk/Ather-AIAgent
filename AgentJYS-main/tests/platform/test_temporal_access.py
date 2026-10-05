import json
import time
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aether_platform import management
from aether_platform.directory import Actor


@pytest.mark.parametrize(
    "role,expected", [("platform_admin", 200), ("tenant_admin", 403), ("user", 403)]
)
def test_temporal_checks_signed_admin_identity(monkeypatch, role, expected):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public["kid"] = "test"
    token = jwt.encode(
        {
            "iss": "https://identity.test",
            "sub": "subject",
            "aud": "budibase",
            "azp": "budibase",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        key,
        algorithm="RS256",
        headers={"kid": "test"},
    )

    def request(req):
        if req.url.path == "/api/global/self":
            assert req.headers["cookie"] == "budibase:auth=valid-session"
            return httpx.Response(200, json={"oauth2": {"accessToken": token}})
        if req.url.path.endswith("/certs"):
            return httpx.Response(200, json={"keys": [public]})
        return httpx.Response(200, json={"active": True, "sub": "subject"})

    real = httpx.Client
    monkeypatch.setattr(
        management,
        "httpx",
        SimpleNamespace(
            Client=lambda **kw: real(transport=httpx.MockTransport(request), **kw),
            HTTPError=httpx.HTTPError,
        ),
    )
    directory = SimpleNamespace(
        authenticate=lambda issuer, subject: Actor("u1", issuer, subject, "t1", role, 1)
    )
    app = FastAPI()
    management.install_management(
        app,
        directory,
        "https://identity.test",
        "secret",
        budibase_url="http://budibase",
        management_url="https://ui.test/app/admin",
    )
    with TestClient(app) as c:
        assert c.get("/ops/temporal-auth", follow_redirects=False).status_code == 303
        result = c.get(
            "/ops/temporal-auth",
            headers={"Cookie": "budibase:auth=valid-session"},
            follow_redirects=False,
        )
        assert result.status_code == expected
