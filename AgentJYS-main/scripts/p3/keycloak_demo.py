"""Prepare a local Keycloak/PostgreSQL demo outside the source checkout.
Existing P3 deployment identities are preserved. Secrets are generated, never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import uuid
from pathlib import Path

import yaml

KEYCLOAK_IMAGE = (
    "quay.io/keycloak/keycloak:26.8.0@sha256:"
    "b0f60d489d51c5d113390bdf5461d4c06e6051be026c05549f2e1e10ec352bcc"
)
POSTGRES_IMAGE = (
    "postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24"
)
ISSUER = "http://127.0.0.1:18080/realms/p3-demo"
REDIRECT = "http://127.0.0.1:5173/"


def save(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def configure_p3(directory: Path, deployment: Path, users: list[dict]) -> None:
    identity_path, service_path = deployment / "identities.yaml", deployment / "service.yaml"
    identities = yaml.safe_load(identity_path.read_text("utf-8"))
    service = yaml.safe_load(service_path.read_text("utf-8"))
    for name, source in (
        ("identities.before.yaml", identity_path),
        ("service.before.yaml", service_path),
    ):
        if not (directory / name).exists():
            save(directory / name, source.read_text("utf-8"))
    existing = {e["principal"]["principal_id"] for e in identities["identities"]}
    tenants = {t["tenant_id"] for t in identities["tenants"]}
    for suffix in ("a", "b"):
        tenant, principal = "tenant_" + suffix, "keycloak_" + suffix
        if tenant not in tenants:
            identities["tenants"].append({"tenant_id": tenant, "enabled": True})
        if principal not in existing:
            identities["identities"].append(
                {
                    "principal": {
                        "principal_id": principal,
                        "auth_epoch": 1,
                        "home_scope": {
                            "tenant_id": tenant,
                            "application_id": "app",
                            "user_id": "demo_" + suffix,
                            "agent_id": "agent",
                        },
                        "permissions": ["memory:read", "memory:write", "maintenance:diagnose"],
                    }
                }
            )
    issuers = identities.setdefault("jwt_issuers", [])
    if not any(i["issuer"] == ISSUER for i in issuers):
        issuers.append(
            {
                "issuer": ISSUER,
                "jwks_url": ISSUER + "/protocol/openid-connect/certs",
                "audience": "aether-p3",
                "algorithms": ["RS256"],
                "leeway_seconds": 0,
                "subject_mappings": [
                    {"subject": u["id"], "principal_id": "keycloak_" + u["username"][-1]}
                    for u in users
                    if u["username"] in {"demo_a", "demo_b"}
                ],
            }
        )
    identities["revision"] += 1
    service["browser_identity"] = {
        "url": "http://127.0.0.1:18080",
        "realm": "p3-demo",
        "client_id": "p3-monitor",
    }
    for target, data in ((identity_path, identities), (service_path, service)):
        temporary = target.with_suffix(".new.yaml")
        save(temporary, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        os.replace(temporary, target)


def prepare(directory: Path, deployment: Path) -> None:
    directory, deployment = directory.resolve(), deployment.resolve()
    if directory.is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("put credentials and runtime data outside the source checkout")
    directory.mkdir(parents=True, exist_ok=True)
    private = directory / "credentials.json"
    if private.exists():
        credentials = json.loads(private.read_text("utf-8"))
    else:
        credentials = {
            "admin_user": "p3_admin",
            "admin_password": secrets.token_urlsafe(28),
            "database_password": secrets.token_urlsafe(28),
            "users": {n: secrets.token_urlsafe(18) for n in ("demo_a", "demo_b", "unbound")},
        }
        save(private, json.dumps(credentials, indent=2))
    users = [
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, ISSUER + "/users/" + n)),
            "username": n,
            "enabled": True,
            "emailVerified": True,
            "email": n + "@example.invalid",
            "firstName": "Demo",
            "lastName": n,
            "credentials": [{"type": "password", "value": p, "temporary": False}],
        }
        for n, p in credentials["users"].items()
    ]
    clients = []
    for client_id, audience in (
        ("p3-monitor", "aether-p3"),
        ("p3-wrong-audience", "another-service"),
    ):
        clients.append(
            {
                "clientId": client_id,
                "protocol": "openid-connect",
                "publicClient": True,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "implicitFlowEnabled": False,
                "serviceAccountsEnabled": False,
                "redirectUris": [
                    REDIRECT,
                    REDIRECT + "?view=demo",
                    REDIRECT + "?view=monitor",
                    REDIRECT + "silent-check-sso.html",
                ],
                "webOrigins": ["http://127.0.0.1:5173"],
                "attributes": {
                    "pkce.code.challenge.method": "S256",
                    "post.logout.redirect.uris": "##".join(
                        [REDIRECT, REDIRECT + "?view=demo", REDIRECT + "?view=monitor"]
                    ),
                },
                "protocolMappers": [
                    {
                        "name": "p3-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.custom.audience": audience,
                            "access.token.claim": "true",
                            "id.token.claim": "false",
                        },
                    }
                ],
            }
        )
    realm = {
        "realm": "p3-demo",
        "enabled": True,
        "displayName": "P3 身份平台（本地验证）",
        "sslRequired": "none",
        "registrationAllowed": False,
        "resetPasswordAllowed": False,
        "accessTokenLifespan": 180,
        "ssoSessionIdleTimeout": 1800,
        "internationalizationEnabled": True,
        "supportedLocales": ["zh-CN", "en"],
        "defaultLocale": "zh-CN",
        "clients": clients,
        "users": users,
    }
    save(directory / "realm.json", json.dumps(realm, ensure_ascii=False, indent=2))
    save(
        directory / ".env",
        "\n".join(
            [
                "POSTGRES_PASSWORD=" + credentials["database_password"],
                "KC_BOOTSTRAP_ADMIN_USERNAME=" + credentials["admin_user"],
                "KC_BOOTSTRAP_ADMIN_PASSWORD=" + credentials["admin_password"],
                "",
            ]
        ),
    )

    def ref(key: str) -> str:
        return "$" + "{" + key + "}"

    compose = {
        "name": "aether-p3-identity-demo",
        "services": {
            "identity-db": {
                "image": POSTGRES_IMAGE,
                "environment": {
                    "POSTGRES_DB": "keycloak",
                    "POSTGRES_USER": "keycloak",
                    "POSTGRES_PASSWORD": ref("POSTGRES_PASSWORD"),
                },
                "volumes": ["identity-data:/var/lib/postgresql/data"],
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U keycloak -d keycloak"],
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 20,
                },
            },
            "keycloak": {
                "image": KEYCLOAK_IMAGE,
                "command": ["start-dev", "--import-realm"],
                "ports": ["127.0.0.1:18080:8080"],
                "environment": {
                    "KC_DB": "postgres",
                    "KC_DB_URL": "jdbc:postgresql://identity-db:5432/keycloak",
                    "KC_DB_USERNAME": "keycloak",
                    "KC_DB_PASSWORD": ref("POSTGRES_PASSWORD"),
                    "KC_HOSTNAME": "http://127.0.0.1:18080",
                    "KC_HEALTH_ENABLED": "true",
                    "KC_BOOTSTRAP_ADMIN_USERNAME": ref("KC_BOOTSTRAP_ADMIN_USERNAME"),
                    "KC_BOOTSTRAP_ADMIN_PASSWORD": ref("KC_BOOTSTRAP_ADMIN_PASSWORD"),
                },
                "volumes": ["./realm.json:/opt/keycloak/data/import/p3-demo-realm.json:ro"],
                "depends_on": {"identity-db": {"condition": "service_healthy"}},
            },
        },
        "volumes": {"identity-data": {}},
    }
    save(directory / "compose.yaml", yaml.safe_dump(compose, sort_keys=False))
    configure_p3(directory, deployment, users)
    print(
        json.dumps(
            {
                "directory": str(directory),
                "credentials_file": str(private),
                "issuer": ISSUER,
                "users": list(credentials["users"]),
                "p3_configuration": str(deployment / "service.yaml"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.directory, args.deployment)
