"""Local native Keycloak registration/invitation/reset acceptance check (no external mail).
Creates one dedicated disposable account; never alters existing demo users.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import secrets
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from keycloak_organizations_demo import Admin
from verify_keycloak import ISSUER, P3, REDIRECT, login, require

MAIL = "http://127.0.0.1:18025"


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.links = []
        self.text = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.current = {"id": a.get("id"), "action": a.get("action"), "inputs": {}}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None and a.get("name"):
            self.current["inputs"][a["name"]] = a.get("value", "")
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None

    def handle_data(self, text):
        if text.strip():
            self.text.append(text.strip())

    def form(self, id):
        return next(f for f in self.forms if f["id"] == id)


# http.cookiejar omits Secure cookies on loopback HTTP unlike Chromium.
# Match cookies only to this exact local issuer and never follow remote redirects.
def request(c, method, url, **kw):
    require(url.startswith(ISSUER + "/"), "Nonlocal identity action rejected")
    path = urlparse(url).path
    cookies = "; ".join(
        f"{x.name}={x.value}"
        for x in c.cookies.jar
        if x.domain == "127.0.0.1" and path.startswith(x.path) and not x.is_expired()
    )
    r = c.request(method, url, headers={"Cookie": cookies}, follow_redirects=False, **kw)
    for _ in range(8):
        if r.status_code not in (302, 303):
            break
        target = urljoin(str(r.url), r.headers["location"])
        if target.startswith(REDIRECT):
            return r
        require(target.startswith(ISSUER + "/"), "Unexpected redirect")
        path = urlparse(target).path
        cookies = "; ".join(
            f"{x.name}={x.value}"
            for x in c.cookies.jar
            if x.domain == "127.0.0.1" and path.startswith(x.path) and not x.is_expired()
        )
        r = c.get(target, headers={"Cookie": cookies}, follow_redirects=False)
    return r


def start(c, registration=False):
    c.cookies.clear()
    v = secrets.token_urlsafe(48)
    return request(
        c,
        "GET",
        ISSUER + "/protocol/openid-connect/" + ("registrations" if registration else "auth"),
        params={
            "client_id": "p3-monitor",
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": "openid",
            "state": secrets.token_urlsafe(24),
            "nonce": secrets.token_urlsafe(24),
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest())
            .decode()
            .rstrip("="),
            "code_challenge_method": "S256",
            "prompt": "login",
        },
    )


def submit(c, r, id, data):
    f = Page(r.text).form(id)
    return request(c, "POST", f["action"], data={**f["inputs"], **data})


def summary(r):
    p = Page(r.text)
    # Only form structure, no action tokens or entered values.
    return {
        "status": r.status_code,
        "forms": [{"id": f["id"], "fields": list(f["inputs"])} for f in p.forms],
        "title": re.findall(r"<title>(.*?)</title>", r.text, re.S),
    }


def verify(directory, report):
    # Invalidate a previous success report before touching any online state.
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"status": "running", "count": 0, "results": []}), "utf-8")
    results = []
    a = Admin(directory)
    uid = None
    message_ids = set()
    completed = False
    smtp = a.get("").get("smtpServer", {})
    require(
        smtp.get("host") == "mailpit" and smtp.get("port") == "1025",
        "This verifier requires the local Mailpit sink",
    )
    name = "selftest_" + datetime.now().strftime("%m%d%H%M%S") + "_" + secrets.token_hex(3)
    email = name + "@example.test"
    password = "DemoA9!" + secrets.token_urlsafe(20)
    ids = json.loads((directory / "organization-demo.json").read_text("utf-8"))
    org = "/organizations/" + ids["organizations"]["tenant_a"]

    def passed(s, **e):
        results.append({"scenario": s, "passed": True, **e})
        print("PASS: " + s, flush=True)

    with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as c:

        def mail_link():
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                listing = c.get(MAIL + "/api/v1/messages").json()["messages"]
                for m in listing:
                    if m["ID"] in message_ids or not any(t["Address"] == email for t in m["To"]):
                        continue
                    full = c.get(MAIL + "/api/v1/message/" + m["ID"]).json()
                    links = Page(full.get("HTML", "")).links
                    if not links:
                        links = re.findall(r"https?://[^\s<>]+", full.get("Text", ""))
                    matches = [u for u in links if u.startswith(ISSUER + "/login-actions/")]
                    if matches:
                        message_ids.add(m["ID"])
                        return matches[0]
                time.sleep(0.25)
            raise AssertionError("Expected local test email was not received")

        def me(token, tenant=None):
            h = {"Authorization": "Bearer " + token}
            if tenant:
                h["X-P3-Tenant"] = tenant
            return c.get(P3 + "/p3/auth/me", headers=h)

        try:
            r = start(c, True)
            r = submit(
                c,
                r,
                "kc-register-form",
                {
                    "username": name,
                    "email": email,
                    "firstName": "Local",
                    "lastName": "Test",
                    "password": password,
                    "password-confirm": password,
                },
            )
            found = a.get("/users", params={"username": name, "exact": "true"})
            require(len(found) == 1, "Registration did not create the dedicated user")
            uid = found[0]["id"]
            require(not found[0]["emailVerified"], "Email verified before clicking the link")
            passed("原生公开注册创建账号，验证邮箱前未标记可信")
            r = request(c, "GET", mail_link())
            require(a.get("/users/" + uid)["emailVerified"], "Email verification failed")
            passed("本地邮件验证成功，未向外部邮箱发送")
            if any(f["id"] == "kc-passwd-update-form" for f in Page(r.text).forms):
                r = submit(
                    c,
                    r,
                    "kc-passwd-update-form",
                    {"password-new": password, "password-confirm": password},
                )
            passed("邮箱验证后完成原生密码设置")
            credentials = {"users": {name: password}}
            token = login(c, credentials, name)[0]["access_token"]
            require(me(token).status_code == 401, "Unbound registration gained tenant access")
            passed("注册并验证邮箱后，未加入组织仍被 P3 拒绝", status=401)
            a.request("POST", org + "/members/invite-user", data={"email": email})
            r = request(c, "GET", mail_link())
            accept = [
                u
                for u in Page(r.text).links
                if u.startswith(ISSUER + "/login-actions/action-token")
            ]
            require(len(accept) == 1, "Expected explicit organization join confirmation")
            r = request(c, "GET", accept[0])
            members = a.get(org + "/members", params={"max": 100})
            require(uid in {u["id"] for u in members}, "Invitation did not create membership")
            passed("管理员发送原生邀请，已有注册账号接受后加入组织")
            require(me(token).status_code == 401, "Membership without role gained access")
            passed("仅接受邀请、未分配组织角色，仍不能访问 P3", status=401)
            a.request(
                "PUT", org + "/groups/" + ids["groups"]["tenant_a:viewer"] + "/members/" + uid
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                identity = me(token)
                if identity.status_code == 200:
                    break
                time.sleep(0.25)
            require(identity.status_code == 200, "Trusted organization role did not converge")
            require(identity.json()["roles"] == ["viewer"], "Wrong P3 role")
            require(identity.json()["scope"]["tenant_id"] == "tenant_a", "Wrong tenant")
            require(
                c.get(P3 + "/p3/health", headers={"Authorization": "Bearer " + token}).status_code
                == 200,
                "Viewer cannot diagnose",
            )
            require(
                me(token, "tenant_b").status_code == 403, "Foreign tenant selection granted access"
            )
            passed("分配组织 viewer 后获得 A 的诊断权限，伪造 B 被拒绝")
            r = start(c)
            r = submit(c, r, "kc-form-login", {"username": name})
            links = [u for u in Page(r.text).links if "/login-actions/reset-credentials" in u]
            require(len(links) == 1, "Forgot-password link missing")
            r = request(c, "GET", urljoin(str(r.url), links[0]))
            r = submit(c, r, "kc-reset-password-form", {"username": name})
            r = request(c, "GET", mail_link())
            r = submit(
                c,
                r,
                "kc-passwd-update-form",
                {"password-new": "short", "password-confirm": "short"},
            )
            require(
                any(f["id"] == "kc-passwd-update-form" for f in Page(r.text).forms),
                "Weak password was accepted",
            )
            passed("原生密码策略拒绝弱密码")
            password = "ResetB8!" + secrets.token_urlsafe(20)
            r = submit(
                c,
                r,
                "kc-passwd-update-form",
                {"password-new": password, "password-confirm": password},
            )
            credentials["users"][name] = password
            token = login(c, credentials, name)[0]["access_token"]
            require(me(token).status_code == 200, "New password login failed")
            passed("原生找回密码邮件生效，新密码重新登录成功")
            events = a.get("/events", params={"user": uid, "max": 100})
            types = {e["type"] for e in events}
            require(
                {"REGISTER", "VERIFY_EMAIL", "LOGIN", "UPDATE_PASSWORD"}.issubset(types),
                "Expected identity events missing",
            )
            passed("身份审计记录注册、邮箱验证、登录和密码更新", types=sorted(types))
            completed = True
        finally:
            # Only this run's generated account and test inbox items.
            if uid is None:
                found = a.get("/users", params={"username": name, "exact": "true"})
                uid = found[0]["id"] if found else None
            if uid:
                a.request("DELETE", "/users/" + uid)
            for m in c.get(MAIL + "/api/v1/messages").json()["messages"]:
                if any(t["Address"] == email for t in m["To"]):
                    response = c.request(
                        "DELETE", MAIL + "/api/v1/messages", json={"IDs": [m["ID"]]}
                    )
                    require(response.status_code == 200, "Test email cleanup failed")
            a.http.close()
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                json.dumps(
                    {
                        "status": "passed" if completed else "failed",
                        "count": len(results),
                        "executed_at": datetime.now().astimezone().isoformat(),
                        "results": results,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "utf-8",
            )
    print("Completed " + str(len(results)) + " native self-service checks", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    verify(args.directory, args.report)
