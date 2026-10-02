"""Exercise native Keycloak Organizations against the running local P3 demo.

Uses PKCE login, restores temporary member/role/client changes, and emits no secrets.
Run after keycloak_organizations_demo.py with P3 and Keycloak already running.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx
from keycloak_organizations_demo import BASE, Admin
from verify_keycloak import ISSUER, P3, login, require


def verify(directory: Path, deployment: Path, report: Path) -> None:
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"status": "running", "results": []}), "utf-8")
    ids = json.loads((directory / "organization-demo.json").read_text("utf-8"))
    credentials = json.loads((directory / "credentials.json").read_text("utf-8"))
    admin = Admin(directory)
    results = []

    def passed(name, **evidence):
        results.append({"scenario": name, "passed": True, **evidence})
        print("PASS: " + name, flush=True)

    with httpx.Client(timeout=30, trust_env=False, follow_redirects=True) as client:
        sessions = {
            name: login(client, credentials, name)[0]["access_token"]
            for name in ("demo_a", "demo_b", "demo_multi", "demo_member", "demo_viewer", "unbound")
        }
        static = (deployment / "credential").read_text("utf-8").strip()

        def call(name, route="/p3/auth/me", tenant=None):
            headers = {"Authorization": "Bearer " + sessions.get(name, static)}
            if tenant:
                headers["X-P3-Tenant"] = tenant
            return client.get(P3 + route, headers=headers)

        def eventually(name, status=200, role=None, tenant=None):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                r = call(name, tenant=tenant)
                if r.status_code == status and (role is None or r.json().get("roles") == [role]):
                    return r
                time.sleep(0.25)
            raise AssertionError("identity did not converge: " + name + " expected " + str(status))

        for name, tenant, role in (
            ("demo_a", "tenant_a", "organization-admin"),
            ("demo_b", "tenant_b", "organization-admin"),
            ("demo_member", "tenant_a", "member"),
            ("demo_viewer", "tenant_a", "viewer"),
        ):
            r = eventually(name, role=role)
            require(r.json()["scope"]["tenant_id"] == tenant, "wrong tenant")
            require(r.json()["identity_source"] == "keycloak_organizations", "wrong source")
            passed("原生成员与角色映射：" + name, tenant=tenant, role=role)
        require(call("unbound").status_code == 401, "unbound identity allowed")
        passed("未加入授权组织的账号被拒绝", http_status=401)
        require(call("demo_a", tenant="tenant_b").status_code == 403, "selector granted membership")
        passed("修改组织请求头不能进入他人组织", http_status=403)
        a, b = call("demo_multi", tenant="tenant_a"), call("demo_multi", tenant="tenant_b")
        require(a.status_code == b.status_code == 200, "multi-org rejected")
        require(
            a.json()["principal_id"] != b.json()["principal_id"],
            "principal shared across organizations",
        )
        require(
            "memory:delete" in a.json()["permissions"]
            and "memory:write" not in b.json()["permissions"],
            "roles merged",
        )
        passed("同一账号在 A 为管理员、B 为只读成员，权限不合并")
        require(call("demo_member", "/p3/health").status_code == 403, "member diagnosed")
        require(call("demo_viewer", "/p3/health").status_code == 200, "viewer cannot diagnose")
        passed("实际诊断接口执行角色限制：普通成员 403，只读诊断 200")

        for own, other in (("tenant_a", "tenant_b"), ("tenant_b", "tenant_a")):
            health = call("demo_multi", "/p3/health", own)
            require(health.status_code == 200, "health failed")
            trace = health.headers["x-trace-id"]
            logs = call("demo_multi", "/p3/logs/" + trace, own)
            foreign = call("demo_multi", "/p3/logs/" + trace, other)
            listing = call("demo_multi", "/p3/traces", other)
            require(logs.status_code == 200 and logs.json()["records"], "own logs missing")
            require(
                foreign.status_code == 403
                or (foreign.status_code == 200 and not foreign.json()["records"]),
                "foreign logs visible",
            )
            require(
                listing.status_code == 200 and trace not in listing.text, "foreign trace listed"
            )
            passed(
                "切换组织后的日志与 Trace 隔离：" + own,
                trace_id=trace,
                records=len(logs.json()["records"]),
            )

        org_a = "/organizations/" + ids["organizations"]["tenant_a"]
        org_b = "/organizations/" + ids["organizations"]["tenant_b"]
        for user in ("demo_a", "demo_multi"):
            headers = {"Authorization": "Bearer " + sessions[user]}
            require(
                client.get(BASE + org_a, headers=headers).status_code == 200,
                "own organization administration denied",
            )
            require(
                client.get(BASE + org_b, headers=headers).status_code == 403,
                "foreign administration allowed",
            )
            own = admin.get(org_a)
            require(
                client.put(BASE + org_a, headers=headers, json=own).status_code == 204,
                "own organization manage denied",
            )
            require(
                client.put(BASE + org_b, headers=headers, json=admin.get(org_b)).status_code == 403,
                "foreign manage allowed",
            )
        require(
            client.get(
                BASE + org_a, headers={"Authorization": "Bearer " + sessions["demo_member"]}
            ).status_code
            == 403,
            "member managed organization",
        )
        passed("Keycloak 原生组织基本信息 GET/PUT：仅允许自己的组织")

        # Native member APIs require additional user permissions. Keep this
        # boundary explicit; never broaden realm privileges to hide a UI limit.
        scoped_admin = {"Authorization": "Bearer " + sessions["demo_a"]}
        require(
            client.get(BASE + org_a + "/members", headers=scoped_admin).status_code == 403,
            "delegated member-read boundary changed",
        )
        existing_membership = (
            org_a
            + "/groups/"
            + ids["groups"]["tenant_a:member"]
            + "/members/"
            + ids["users"]["demo_member"]
        )
        require(
            client.put(BASE + existing_membership, headers=scoped_admin).status_code == 403,
            "delegated group-membership boundary changed",
        )
        passed("当前委派限制：组织管理员不能自助查看成员或修改成员分组")

        user_id = ids["users"]["demo_member"]
        group_paths = {
            r: org_a + "/groups/" + ids["groups"]["tenant_a:" + r] + "/members/" + user_id
            for r in ("member", "viewer")
        }
        try:
            admin.request("PUT", group_paths["viewer"])
            admin.request("DELETE", group_paths["member"])
            eventually("demo_member", role="viewer")
            require(call("demo_member", "/p3/health").status_code == 200, "changed role not used")
            passed("只修改 Keycloak 分组，同一访问令牌获得更新后的权限")
        finally:
            admin.request("PUT", group_paths["member"])
            admin.request("DELETE", group_paths["viewer"])
        eventually("demo_member", role="member")
        try:
            admin.request("DELETE", org_a + "/members/" + user_id)
            eventually("demo_member", status=401)
            passed("从组织移除成员后，仍未过期的访问令牌也被 P3 拒绝")
        finally:
            present = admin.get(org_a + "/members", params={"max": 100})
            if user_id not in {u["id"] for u in present}:
                admin.request("POST", org_a + "/members", json=user_id)
            admin.request("PUT", group_paths["member"])
        eventually("demo_member", role="member")
        passed("重新加入原生组织和分组后可恢复访问")

        old_user = admin.get("/users/" + user_id)
        try:
            admin.request("PUT", "/users/" + user_id, json={**old_user, "enabled": False})
            eventually("demo_member", status=401)
            passed("Keycloak 停用用户后，旧访问令牌失去 P3 访问权")
        finally:
            admin.request("PUT", "/users/" + user_id, json=old_user)
        eventually("demo_member", role="member")

        old_org = admin.get(org_a)
        try:
            admin.request("PUT", org_a, json={**old_org, "enabled": False})
            eventually("demo_a", status=401)
            require(call("demo_b").status_code == 200, "unrelated organization blocked")
            passed("停用组织 A 后禁止 A 访问，组织 B 正常")
        finally:
            admin.request("PUT", org_a, json=old_org)
        eventually("demo_a", role="organization-admin")

        secret = (directory / "directory-client-secret").read_text("utf-8").strip()
        token = client.post(
            ISSUER + "/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "p3-directory",
                "client_secret": secret,
            },
        )
        require(token.status_code == 200, "directory credential failed")
        require(
            client.put(
                BASE + org_a,
                headers={"Authorization": "Bearer " + token.json()["access_token"]},
                json=old_org,
            ).status_code
            == 403,
            "directory client can write",
        )
        passed("P3 目录客户端只有读取权，不能修改 Keycloak 组织")
        client_path = "/clients/" + ids["directory_client"]
        old_client = admin.get(client_path)
        try:
            admin.request("PUT", client_path, json={**old_client, "enabled": False})
            eventually("demo_a", status=503)
            require(call("static").status_code == 200, "static recovery identity unavailable")
            passed("目录不可用超过快照期限后拒绝动态身份，静态管理入口仍可用")
        finally:
            admin.request("PUT", client_path, json=old_client)
        eventually("demo_a", role="organization-admin")
        passed("目录服务恢复后组织身份自动恢复")
        report.write_text(
            json.dumps(
                {"status": "passed", "count": len(results), "results": results},
                ensure_ascii=False,
                indent=2,
            ),
            "utf-8",
        )
        print("ALL PASSED: " + str(len(results)))
    admin.http.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    verify(args.directory, args.deployment, args.report)
