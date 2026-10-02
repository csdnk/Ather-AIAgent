"""Configure native Keycloak Organizations demo; private runtime files stay external."""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path

import httpx
import yaml

ISSUER = "http://127.0.0.1:18080/realms/p3-demo"
BASE = "http://127.0.0.1:18080/admin/realms/p3-demo"


class Admin:
    def __init__(self, directory: Path):
        self.credentials = json.loads((directory / "credentials.json").read_text("utf-8"))
        self.http = httpx.Client(timeout=15, follow_redirects=False)
        self._authenticate()

    def _authenticate(self):
        response = self.http.post(
            "http://127.0.0.1:18080/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.credentials["admin_user"],
                "password": self.credentials["admin_password"],
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"Keycloak admin authentication failed ({response.status_code})")
        self.http.headers["Authorization"] = "Bearer " + response.json()["access_token"]

    def request(self, method, path, **kwargs):
        response = self.http.request(method, BASE + path, **kwargs)
        if response.status_code == 401:
            # A long verification/setup may outlive the admin token. A 401 has
            # no authorized side effect; renew once, including during cleanup.
            self._authenticate()
            response = self.http.request(method, BASE + path, **kwargs)
        if response.status_code not in (200, 201, 204):
            raise RuntimeError(
                f"Keycloak operation failed: {method} {path} ({response.status_code})"
            )
        return response

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs).json()

    def client(self, name):
        matches = self.get("/clients", params={"clientId": name})
        if len(matches) != 1:
            raise RuntimeError("expected exactly one configured client")
        return matches[0]["id"]


def prepare(directory: Path, deployment: Path):
    repository = Path(__file__).resolve().parents[2]
    directory, deployment = directory.resolve(), deployment.resolve()
    if directory.is_relative_to(repository) or deployment.is_relative_to(repository):
        raise ValueError("private deployment files must be outside the repository")
    admin = Admin(directory)
    realm = admin.get("")
    realm.update(organizationsEnabled=True, adminPermissionsEnabled=True)
    admin.request("PUT", "", json=realm)
    management = admin.client("realm-management")
    monitor = admin.client("p3-monitor")
    permissions_client = admin.client("admin-permissions")
    root = f"/clients/{permissions_client}/authz/resource-server"
    roles = {}
    for name in ("organization-admin", "member", "viewer"):
        existing = admin.get(f"/clients/{monitor}/roles")
        if not any(r["name"] == name for r in existing):
            admin.request("POST", f"/clients/{monitor}/roles", json={"name": name})
        roles[name] = admin.get(f"/clients/{monitor}/roles/{name}")
    if not admin.get("/clients", params={"clientId": "p3-directory"}):
        admin.request(
            "POST",
            "/clients",
            json={
                "clientId": "p3-directory",
                "enabled": True,
                "publicClient": False,
                "serviceAccountsEnabled": True,
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "implicitFlowEnabled": False,
            },
        )
    directory_client = admin.client("p3-directory")
    service_user = admin.get(f"/clients/{directory_client}/service-account-user")["id"]
    readonly_roles = [
        admin.get(f"/clients/{management}/roles/{r}")
        for r in ("view-organizations", "query-organizations", "view-clients", "view-users")
    ]
    admin.request(
        "POST", f"/users/{service_user}/role-mappings/clients/{management}", json=readonly_roles
    )
    secret = admin.get(f"/clients/{directory_client}/client-secret")["value"]
    (directory / "directory-client-secret").write_text(secret, encoding="utf-8")
    users = {}
    for name in ("demo_a", "demo_b", "demo_multi", "demo_member", "demo_viewer"):
        found = admin.get("/users", params={"username": name, "exact": "true"})
        if not found:
            password = secrets.token_urlsafe(24)
            admin.request(
                "POST",
                "/users",
                json={
                    "username": name,
                    "enabled": True,
                    "firstName": name,
                    "lastName": "P3",
                    "email": name + "@p3.example.test",
                    "emailVerified": True,
                    "credentials": [{"type": "password", "value": password, "temporary": False}],
                },
            )
            admin.credentials["users"][name] = password
            # Persist each generated credential before proceeding to the next remote operation.
            (directory / "credentials.json").write_text(
                json.dumps(admin.credentials, indent=2), encoding="utf-8"
            )
            found = admin.get("/users", params={"username": name, "exact": "true"})
        users[name] = found[0]["id"]
    organizations = {}
    group_ids = {}
    membership = {
        "tenant_a": {
            "demo_a": "organization-admin",
            "demo_multi": "organization-admin",
            "demo_member": "member",
            "demo_viewer": "viewer",
        },
        "tenant_b": {"demo_b": "organization-admin", "demo_multi": "viewer"},
    }
    group_names = {"organization-admin": "组织管理员", "member": "普通成员", "viewer": "只读成员"}
    for tenant, assigned in membership.items():
        alias = tenant.replace("_", "-")
        orgs = admin.get("/organizations", params={"max": 100})
        org = next((org for org in orgs if org.get("alias") == alias), None)
        if org is None:
            response = admin.request(
                "POST",
                "/organizations",
                json={
                    "name": "组织 " + tenant[-1].upper(),
                    "alias": alias,
                    "enabled": True,
                    "description": "P3 原生组织隔离演示",
                },
            )
            org = admin.get("/organizations/" + response.headers["location"].rsplit("/", 1)[-1])
        org_id = org["id"]
        organizations[tenant] = org_id
        path = "/organizations/" + org_id
        for role, name in group_names.items():
            groups = admin.get(path + "/groups", params={"max": 100})
            group = next((g for g in groups if g["name"] == name), None)
            if group is None:
                response = admin.request("POST", path + "/groups", json={"name": name})
                group = admin.get(
                    path + "/groups/" + response.headers["location"].rsplit("/", 1)[-1]
                )
            group_ids[tenant + ":" + role] = group["id"]
            admin.request(
                "POST",
                path + f"/groups/{group['id']}/role-mappings/clients/{monitor}",
                json=[roles[role]],
            )
        members = {m["id"] for m in admin.get(path + "/members", params={"max": 100})}
        for name, role in assigned.items():
            if users[name] not in members:
                admin.request("POST", path + "/members", json=users[name])
            current_groups = admin.get(path + f"/members/{users[name]}/groups")
            if not any(g["id"] == group_ids[tenant + ":" + role] for g in current_groups):
                admin.request(
                    "PUT", path + f"/groups/{group_ids[tenant + ':' + role]}/members/{users[name]}"
                )
        # FGAP currently accepts realm groups, not organization groups. Native user
        # policies delegate administration separately from P3 business roles.
        admin_users = [
            users[name] for name, role in assigned.items() if role == "organization-admin"
        ]
        query_role = admin.get(f"/clients/{management}/roles/query-organizations")
        for user_id in admin_users:
            admin.request(
                "POST", f"/users/{user_id}/role-mappings/clients/{management}", json=[query_role]
            )
        policy_name = "p3-admins-" + tenant
        policy = {"name": policy_name, "logic": "POSITIVE", "users": admin_users}
        policies = admin.get(root + "/policy/user")
        old = next((p for p in policies if p["name"] == policy_name), None)
        admin.request(
            "PUT" if old else "POST",
            root + "/policy/user" + ("/" + old["id"] if old else ""),
            json=policy,
        )
        permission_name = "p3-manage-" + tenant
        permission = {
            "name": permission_name,
            "resourceType": "Organizations",
            "scopes": ["view", "manage"],
            "resources": [org_id],
            "policies": [policy_name],
        }
        old = next(
            (p for p in admin.get(root + "/permission/scope") if p["name"] == permission_name), None
        )
        admin.request(
            "PUT" if old else "POST",
            root + "/permission/scope" + ("/" + old["id"] if old else ""),
            json=permission,
        )
    identity_file = deployment / "identities.yaml"
    before = directory / "identities.before-organizations.yaml"
    if not before.exists():
        before.write_bytes(identity_file.read_bytes())
    config = yaml.safe_load(identity_file.read_text("utf-8"))
    old_mapped = set()
    for issuer in config["jwt_issuers"]:
        if issuer["issuer"] == ISSUER:
            old_mapped.update(m["principal_id"] for m in issuer.get("subject_mappings", []))
            issuer["subject_mappings"] = []
            issuer["directory"] = {
                "client_id": "p3-directory",
                "client_secret_file": str(directory / "directory-client-secret"),
                "roles_client_id": "p3-monitor",
                "organizations": [
                    {
                        "organization_id": oid,
                        "tenant_id": tenant,
                        "application_id": "p3",
                        "agent_id": "p3-agent",
                    }
                    for tenant, oid in organizations.items()
                ],
                "role_permissions": {
                    "organization-admin": [
                        "memory:read",
                        "memory:write",
                        "memory:correct",
                        "memory:delete",
                        "memory:history",
                        "maintenance:diagnose",
                        "maintenance:recover",
                        "maintenance:configure",
                    ],
                    "member": ["memory:read", "memory:write", "memory:correct", "memory:history"],
                    "viewer": ["memory:read", "memory:history", "maintenance:diagnose"],
                },
                "refresh_seconds": 5,
                "stale_after_seconds": 20,
                "timeout_seconds": 3,
            }
    config["identities"] = [
        i
        for i in config["identities"]
        if i["principal"]["principal_id"] not in old_mapped or i.get("credential_sha256")
    ]
    config["revision"] += 1
    from aether_agent_memory.runtime.flows.config import IdentityConfiguration

    IdentityConfiguration.model_validate(config)
    identity_file.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    state = {
        "organizations": organizations,
        "groups": group_ids,
        "users": users,
        "monitor_client": monitor,
        "management_client": management,
        "directory_client": directory_client,
    }
    (directory / "organization-demo.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "organizations": organizations,
                "accounts": list(users),
                "identity_file": str(identity_file),
            },
            ensure_ascii=False,
        )
    )
    admin.http.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.directory, args.deployment)
