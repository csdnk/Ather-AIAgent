"""Verify real Keycloak PKCE login and P3 tenant boundaries without printing secrets.

Uses ONLY the local demo prepared by keycloak_demo.py. No password grant, no live P2.
The tenant-disable scenario restores the original enabled flag in finally.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import yaml

ISSUER = "http://127.0.0.1:18080/realms/p3-demo"
P3 = "http://127.0.0.1:8080"
REDIRECT = "http://127.0.0.1:5173/"
TOKEN = ISSUER + "/protocol/openid-connect/token"


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and values.get("id") == "kc-form-login":
            self.action = values.get("action")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def authorization(client, credentials, user, client_id="p3-monitor"):
    # Each simulated person uses a separate browser session.
    client.cookies.clear()
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    state = secrets.token_urlsafe(24)
    response = client.get(
        ISSUER + "/protocol/openid-connect/auth",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": "openid",
            "state": state,
            "nonce": secrets.token_urlsafe(24),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "login",
        },
    )
    require(response.status_code == 200, "login page unavailable")
    form = LoginForm()
    form.feed(response.text)
    require(form.action and form.action.startswith(ISSUER + "/"), "unexpected login destination")
    # Chromium permits Secure cookies on trustworthy loopback HTTP. Python
    # CookieJar does not. Mirror that browser behaviour ONLY on this exact
    # local login URL; do not weaken Keycloak or the general HTTP client policy.
    action = urlparse(form.action)
    require(action.scheme == "http" and action.netloc == "127.0.0.1:18080", "nonlocal login")
    local_cookies = "; ".join(
        f"{cookie.name}={cookie.value}"
        for cookie in client.cookies.jar
        if cookie.domain == "127.0.0.1"
        and action.path.startswith(cookie.path)
        and not cookie.is_expired()
    )
    response = client.post(
        form.action,
        headers={"Cookie": local_cookies},
        data={"username": user, "password": credentials["users"][user]},
        follow_redirects=False,
    )
    # Native Organizations uses an identity-first page, then a password page.
    if response.status_code == 200:
        second = LoginForm()
        second.feed(response.text)
        require(
            second.action and second.action.startswith(ISSUER + "/"),
            "unexpected second login destination",
        )
        action = urlparse(second.action)
        require(action.scheme == "http" and action.netloc == "127.0.0.1:18080", "nonlocal login")
        local_cookies = "; ".join(
            f"{cookie.name}={cookie.value}"
            for cookie in client.cookies.jar
            if cookie.domain == "127.0.0.1"
            and action.path.startswith(cookie.path)
            and not cookie.is_expired()
        )
        response = client.post(
            second.action,
            headers={"Cookie": local_cookies},
            data={"password": credentials["users"][user]},
            follow_redirects=False,
        )
    location = response.headers.get("location", "")
    require(
        response.status_code in (302, 303) and location.startswith(REDIRECT + "?"),
        "login did not return an authorization code",
    )
    query = parse_qs(urlparse(location).query)
    require(query.get("state") == [state] and "code" in query, "login state mismatch")
    return {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code": query["code"][0],
        "code_verifier": verifier,
    }


def login(client, credentials, user, client_id="p3-monitor"):
    data = authorization(client, credentials, user, client_id)
    response = client.post(TOKEN, data=data)
    require(response.status_code == 200, "authorization code exchange failed")
    result = response.json()
    require("access_token" in result and "refresh_token" in result, "tokens missing")
    return result, data


def call(client, token, route):
    return client.get(P3 + route, headers={"Authorization": "Bearer " + token})


def tenant_enabled(identity_file, enabled):
    document = yaml.safe_load(identity_file.read_text("utf-8"))
    tenant = next(t for t in document["tenants"] if t["tenant_id"] == "tenant_a")
    tenant["enabled"] = enabled
    for item in document["identities"]:
        if item["principal"]["home_scope"]["tenant_id"] == "tenant_a":
            item["principal"]["auth_epoch"] += 1
    document["revision"] += 1
    temporary = identity_file.with_suffix(".verify.new.yaml")
    temporary.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), "utf-8")
    os.replace(temporary, identity_file)


def await_status(client, token, expected):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = call(client, token, "/p3/auth/me")
        if response.status_code == expected:
            return
        time.sleep(0.25)
    raise AssertionError("tenant configuration did not reach expected status")


def verify(directory, deployment, report):
    credentials = json.loads((directory / "credentials.json").read_text("utf-8"))
    results = []

    def passed(name, **evidence):
        results.append({"scenario": name, "passed": True, **evidence})

    with httpx.Client(timeout=30, follow_redirects=True, trust_env=False) as client:
        discovery = client.get(ISSUER + "/.well-known/openid-configuration").json()
        require(discovery["issuer"] == ISSUER, "wrong issuer")
        require(client.get(discovery["jwks_uri"]).json()["keys"], "JWKS missing")
        config = client.get(P3 + "/p3/auth/config")
        require(
            config.status_code == 200 and config.json().get("client_id") == "p3-monitor",
            "browser identity not configured",
        )
        passed("真实身份服务发现、公钥和浏览器配置")
        require(client.get(P3 + "/p3/auth/me").status_code == 401, "anonymous request accepted")
        passed("未登录访问被拒绝", http_status=401)

        a, exchange = login(client, credentials, "demo_a")
        b, _ = login(client, credentials, "demo_b")
        for name, session in [("a", a), ("b", b)]:
            me = call(client, session["access_token"], "/p3/auth/me")
            require(me.status_code == 200, "mapped account rejected")
            require(me.json()["scope"]["tenant_id"] == "tenant_" + name, "tenant mapping mismatch")
            passed(
                "真实账号登录与租户映射 " + name.upper(),
                http_status=200,
                tenant=me.json()["scope"]["tenant_id"],
            )
        require(client.post(TOKEN, data=exchange).status_code == 400, "authorization code reused")
        passed("授权码不能重复兑换", http_status=400)
        invalid = authorization(client, credentials, "demo_a")
        invalid["code_verifier"] = secrets.token_urlsafe(48)
        require(client.post(TOKEN, data=invalid).status_code == 400, "wrong PKCE verifier accepted")
        passed("PKCE 校验失败拒绝兑换", http_status=400)

        # A health call writes real P3 node records. Test both directions, including lists.
        for owner, other, label in [(a, b, "A"), (b, a, "B")]:
            health = call(client, owner["access_token"], "/p3/health")
            require(health.status_code == 200, "health call failed")
            trace = health.headers["x-trace-id"]
            own = call(client, owner["access_token"], "/p3/logs/" + trace)
            foreign = call(client, other["access_token"], "/p3/logs/" + trace)
            require(own.status_code == 200 and own.json()["records"], "own trace has no records")
            require(
                foreign.status_code == 403
                or (foreign.status_code == 200 and not foreign.json()["records"]),
                "cross-tenant records exposed",
            )
            listing = call(client, other["access_token"], "/p3/traces")
            require(
                listing.status_code == 200 and trace not in listing.text,
                "cross-tenant trace exposed in list",
            )
            passed(
                "租户 " + label + " 的日志与 Trace 不向另一租户暴露",
                own_records=len(own.json()["records"]),
                foreign_records=0,
                trace_id=trace,
            )

        signed = a["access_token"]
        parts = signed.split(".")
        # Change signed content, not padding bits in the signature.
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        claims["tenant_id"] = "tenant_b"
        parts[1] = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        require(
            call(client, ".".join(parts), "/p3/auth/me").status_code == 401, "tampered JWT accepted"
        )
        passed("篡改令牌拒绝访问", http_status=401)
        for user, client_id, name in [
            ("unbound", "p3-monitor", "合法但未登记的账号被拒绝"),
            ("demo_a", "p3-wrong-audience", "非 P3 受众令牌被拒绝"),
        ]:
            rejected, _ = login(client, credentials, user, client_id)
            require(
                call(client, rejected["access_token"], "/p3/auth/me").status_code == 401,
                "untrusted account or audience accepted",
            )
            passed(name, http_status=401)
        refreshed = client.post(
            TOKEN,
            data={
                "grant_type": "refresh_token",
                "client_id": "p3-monitor",
                "refresh_token": b["refresh_token"],
            },
        )
        require(refreshed.status_code == 200, "refresh failed")
        b = refreshed.json()
        require(
            call(client, b["access_token"], "/p3/auth/me").status_code == 200,
            "refreshed token rejected",
        )
        passed("刷新令牌后仍可访问所属租户", http_status=200)

        identity_file = deployment / "identities.yaml"
        document = yaml.safe_load(identity_file.read_text("utf-8"))
        original = next(t for t in document["tenants"] if t["tenant_id"] == "tenant_a").get(
            "enabled", True
        )
        require(original, "tenant A must be enabled before running this demo")
        try:
            tenant_enabled(identity_file, False)
            await_status(client, a["access_token"], 403)
            require(
                call(client, b["access_token"], "/p3/auth/me").status_code == 200,
                "tenant B affected by tenant A disable",
            )
            passed("停用 A 后旧令牌被拒绝，B 继续工作", a_status=403, b_status=200)
        finally:
            tenant_enabled(identity_file, original)
        await_status(client, a["access_token"], 200)
        passed("恢复 A 后配置重新生效", http_status=200)

        logout = client.post(
            ISSUER + "/protocol/openid-connect/logout",
            data={"client_id": "p3-monitor", "refresh_token": b["refresh_token"]},
        )
        require(logout.status_code in (200, 204), "logout failed")
        require(
            client.post(
                TOKEN,
                data={
                    "grant_type": "refresh_token",
                    "client_id": "p3-monitor",
                    "refresh_token": b["refresh_token"],
                },
            ).status_code
            == 400,
            "refresh token remains valid after logout",
        )
        passed(
            "退出身份会话后不能再刷新",
            refresh_status=400,
            note="已签发 access token 不会因本地 JWT 验签立即失效",
        )
        static = (deployment / "credential").read_text("utf-8").strip()
        require(
            call(client, static, "/p3/auth/me").status_code == 200,
            "legacy admin credential rejected",
        )
        passed("原管理员静态凭据兼容", http_status=200)

    summary = {
        "passed": len(results),
        "failed": 0,
        "results": results,
        "scope": "真实 Keycloak、PostgreSQL、P3 HTTP；不代表真实 P2 或 P4 Agent 已接通",
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify(args.directory, args.deployment, args.report)
    except Exception as exc:
        # HTTP exception text may include authorization codes; never echo it.
        reason = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
        print(json.dumps({"passed": False, "reason": reason}, ensure_ascii=False))
        raise SystemExit(1) from None
