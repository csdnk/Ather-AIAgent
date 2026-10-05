"""Opt-in real Budibase/Keycloak protocol tests; these do not test Aether RBAC.

Set AETHER_IDENTITY_LAB_DIRECTORY to a directory made by prepare_identity_lab.py.
Only that isolated lab is configured; no external identity servers are accepted.
"""

import base64
import hashlib
import json
import os
import secrets
from html.parser import HTMLParser
from http.cookiejar import DefaultCookiePolicy
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

import httpx
import jwt
import pytest

from aether_platform.auth.budibase import IdentityLinkError, verify_identity

BB = "http://localhost:19000"
KC = "http://localhost:19080/realms/aether-lab"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("AETHER_IDENTITY_LAB_DIRECTORY"),
        reason="explicit isolated identity lab required",
    ),
]


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "form" and data.get("id") == "kc-form-login":
            self.action = data.get("action")
        if tag == "input" and data.get("type") == "hidden" and data.get("name"):
            self.fields[data["name"]] = data.get("value", "")


class LocalhostBrowserPolicy(DefaultCookiePolicy):
    """Browsers allow Secure cookies on localhost; stdlib CookieJar does not.

    Model that exception only for this lab's exact loopback origins. The
    server still issues Secure cookies; no production cookie policy changes.
    """

    def return_ok_secure(self, cookie, request):
        target = urlparse(request.get_full_url())
        if target.scheme == "http" and target.netloc in {"localhost:19000", "localhost:19080"}:
            return True
        return super().return_ok_secure(cookie, request)


def checked(response, expected=200):
    # Do not include callback URLs, tokens or credential payloads in assertions.
    assert response.status_code == expected, (
        f"HTTP status {response.status_code}, expected {expected}"
    )
    return response


@pytest.fixture(scope="module")
def lab():
    directory = Path(os.environ["AETHER_IDENTITY_LAB_DIRECTORY"])
    private = json.loads((directory / "private.json").read_text(encoding="utf-8"))
    with httpx.Client(base_url=BB, timeout=30, trust_env=False) as client:
        checked(
            client.post(
                "/api/global/auth/default/login",
                json={
                    "username": private["budibase_admin_email"],
                    "password": private["budibase_admin_password"],
                },
            )
        )
        actor = checked(client.get("/api/global/self")).json()
        client.headers["x-csrf-token"] = actor["csrfToken"]
        settings = checked(client.get("/api/global/configs/settings")).json()
        settings["config"].update(company="Aether identity lab", platformUrl=BB)
        checked(client.post("/api/global/configs", json=settings))
        checked(
            client.post(
                "/api/global/configs",
                json={
                    "type": "oidc",
                    "config": {
                        "configs": [
                            {
                                "uuid": "aether-lab-oidc",
                                "name": "Aether lab",
                                "clientID": "budibase",
                                "clientSecret": private["budibase_client_secret"],
                                "configUrl": KC + "/.well-known/openid-configuration",
                                "activated": True,
                                "scopes": ["openid", "profile", "email"],
                                "allowUnverifiedEmailLinking": False,
                            }
                        ]
                    },
                },
            )
        )
    return private


def browser_client():
    client = httpx.Client(timeout=30, trust_env=False, follow_redirects=True)
    client.cookies.jar.set_policy(LocalhostBrowserPolicy())

    def browser_cookies(request):
        # HTTPX copies Cookies into a new jar when merging request cookies,
        # which loses the custom policy. Apply it through the public hook.
        outgoing = Request(str(request.url))
        client.cookies.jar.add_cookie_header(outgoing)
        value = outgoing.get_header("Cookie")
        if value:
            request.headers["cookie"] = value

    client.event_hooks["request"].append(browser_cookies)
    return client


def login(lab, username):
    client = browser_client()
    try:
        response = client.get(BB + "/api/global/auth/default/oidc/configs/aether-lab-oidc")
        checked(response)
        form = LoginForm()
        form.feed(response.text)
        assert form.action is not None, "Keycloak did not present its login form"
        target = urlparse(form.action)
        assert (target.scheme, target.netloc) == ("http", "localhost:19080")
        form.fields.update(username=username, password=lab[username + "_password"])
        checked(client.post(form.action, data=form.fields))
        actor = checked(client.get(BB + "/api/global/self")).json()
        return client, actor
    except BaseException:
        client.close()
        raise


def bff_subject(lab, username):
    """Execute a separate Code+PKCE login, stopping before the BFF callback."""
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    with browser_client() as client:
        response = checked(
            client.get(
                KC + "/protocol/openid-connect/auth",
                params={
                    "client_id": "platform-bff",
                    "response_type": "code",
                    "redirect_uri": "http://localhost:19010/auth/callback",
                    "scope": "openid profile",
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
            )
        )
        form = LoginForm()
        form.feed(response.text)
        assert form.action and urlparse(form.action).netloc == "localhost:19080"
        response = client.post(
            form.action,
            data={
                **form.fields,
                "username": username,
                "password": lab[username + "_password"],
            },
            follow_redirects=False,
        )
        assert response.status_code in {302, 303}
        location = urlparse(response.headers["location"])
        assert (location.netloc, location.path) == ("localhost:19010", "/auth/callback")
        params = parse_qs(location.query)
        assert params["state"] == [state]
        tokens = checked(
            client.post(
                KC + "/protocol/openid-connect/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": "platform-bff",
                    "client_secret": lab["bff_client_secret"],
                    "code": params["code"][0],
                    "redirect_uri": "http://localhost:19010/auth/callback",
                    "code_verifier": verifier,
                },
            )
        ).json()
        keys = checked(client.get(KC + "/protocol/openid-connect/certs")).json()
        kid = jwt.get_unverified_header(tokens["id_token"])["kid"]
        key = next(key.key for key in jwt.PyJWKSet.from_dict(keys).keys if key.key_id == kid)
        claims = jwt.decode(
            tokens["id_token"],
            key,
            algorithms=["RS256"],
            issuer=KC,
            audience="platform-bff",
            options={"require": ["sub", "exp", "nonce"]},
        )
        assert claims["nonce"] == nonce
        return claims["sub"], keys


def test_unauthenticated_self_request_is_rejected(lab):
    with httpx.Client(timeout=10, trust_env=False) as client:
        assert client.get(BB + "/api/global/self").status_code in {401, 403}


def test_real_oidc_distinguishes_two_admin_subjects(lab):
    a, actor_a = login(lab, "tenant_admin_a")
    b, actor_b = login(lab, "tenant_admin_b")
    try:
        assert actor_a["_id"] != actor_b["_id"]
        assert actor_a["provider"] == actor_b["provider"] == KC
        # This validates the provider association, not business tenant authority.
        assert actor_a["email"] == "tenant_admin_a@aether-lab.invalid"
        assert actor_b["email"] == "tenant_admin_b@aether-lab.invalid"
    finally:
        a.close()
        b.close()


def test_jit_user_cannot_change_global_configuration(lab):
    client, actor = login(lab, "user_a")
    try:
        client.headers["x-csrf-token"] = actor["csrfToken"]
        response = client.post(
            BB + "/api/global/configs",
            json={
                "type": "settings",
                "config": {"company": "unauthorized change"},
            },
        )
        assert response.status_code == 403
    finally:
        client.close()


def test_forged_session_and_actor_headers_do_not_authenticate(lab):
    with httpx.Client(timeout=10, trust_env=False) as client:
        response = client.get(
            BB + "/api/global/self",
            headers={
                "x-budibase-token": "forged",
                "x-user-id": "platform_admin",
                "x-tenant-id": "tenant_a",
            },
        )
        assert response.status_code in {401, 403}


def test_signed_budibase_proof_matches_independent_bff_login(lab):
    client, actor = login(lab, "tenant_admin_a")
    try:
        subject, keys = bff_subject(lab, "tenant_admin_a")
        linked = verify_identity(
            actor, keys, issuer=KC, client_id="budibase", expected_subject=subject
        )
        assert linked.subject == subject
        assert linked.budibase_user_id == actor["_id"]
        other, _ = bff_subject(lab, "tenant_admin_b")
        with pytest.raises(IdentityLinkError):
            verify_identity(actor, keys, issuer=KC, client_id="budibase", expected_subject=other)
        assert actor["license"]["plan"]["type"] == "free"
    finally:
        client.close()
