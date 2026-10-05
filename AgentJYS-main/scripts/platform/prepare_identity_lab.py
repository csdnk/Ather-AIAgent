"""Prepare an isolated, local-only identity compatibility lab outside the checkout.

This lab uses Keycloak start-dev solely for the P0 compatibility gate. It does
not provision P3, cloud resources or production accounts. Secrets stay in the
requested private directory and are never printed.
"""

import argparse
import json
import secrets
from pathlib import Path

import yaml

KEYCLOAK_IMAGE = (
    "quay.io/keycloak/keycloak:26.8.0@sha256:"
    "b0f60d489d51c5d113390bdf5461d4c06e6051be026c05549f2e1e10ec352bcc"
)
BUDIBASE_IMAGE = "budibase/budibase:v3.47.0"
ISSUER = "http://localhost:19080/realms/aether-lab"


def prepare(directory: Path) -> None:
    directory = directory.resolve()
    repository = Path(__file__).resolve().parents[3]
    if directory == repository or repository in directory.parents:
        raise ValueError("Identity lab secrets and runtime files must be outside the repository")
    directory.mkdir(parents=True, exist_ok=False)
    directory.chmod(0o700)
    (directory / "realm").mkdir()
    private = {
        "keycloak_admin_password": secrets.token_urlsafe(32),
        "budibase_admin_email": "builder@aether-lab.invalid",
        "budibase_admin_password": secrets.token_urlsafe(32),
        "budibase_client_secret": secrets.token_urlsafe(32),
        "bff_client_secret": secrets.token_urlsafe(32),
    }
    users = []
    for username in ("platform_admin", "tenant_admin_a", "tenant_admin_b", "user_a", "user_b"):
        password = secrets.token_urlsafe(24)
        private[username + "_password"] = password
        users.append(
            {
                "username": username,
                "enabled": True,
                "emailVerified": True,
                "email": username + "@aether-lab.invalid",
                "firstName": username,
                "lastName": "Lab",
                "credentials": [{"type": "password", "value": password, "temporary": False}],
            }
        )
    realm = {
        "realm": "aether-lab",
        "enabled": True,
        "registrationAllowed": False,
        "resetPasswordAllowed": False,
        "sslRequired": "none",
        "users": users,
        "clients": [
            {
                "clientId": "budibase",
                "protocol": "openid-connect",
                "publicClient": False,
                "secret": private["budibase_client_secret"],
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": False,
                "redirectUris": ["http://localhost:19000/api/global/auth/oidc/callback"],
                "webOrigins": ["http://localhost:19000"],
                "protocolMappers": [
                    {
                        "name": "budibase-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.client.audience": "budibase",
                            "id.token.claim": "false",
                            "access.token.claim": "true",
                        },
                    }
                ],
            },
            {
                "clientId": "platform-bff",
                "protocol": "openid-connect",
                "publicClient": False,
                "secret": private["bff_client_secret"],
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": True,
                "serviceAccountsEnabled": False,
                "redirectUris": ["http://localhost:19010/auth/callback"],
                "webOrigins": ["http://localhost:19010"],
                "attributes": {"pkce.code.challenge.method": "S256"},
                "protocolMappers": [
                    {
                        "name": "platform-bff-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.client.audience": "platform-bff",
                            "id.token.claim": "false",
                            "access.token.claim": "true",
                            "introspection.token.claim": "true",
                        },
                    }
                ],
            },
        ],
    }
    compose = {
        "name": "aether-agent-lab-" + secrets.token_hex(6),
        "services": {
            "keycloak": {
                "image": KEYCLOAK_IMAGE,
                "restart": "no",
                "mem_limit": "1g",
                "command": ["start-dev", "--import-realm", "--http-port=19080"],
                "network_mode": "service:budibase",
                "ports": [],
                "environment": {
                    "KC_BOOTSTRAP_ADMIN_USERNAME": "lab-admin",
                    "KC_BOOTSTRAP_ADMIN_PASSWORD": private["keycloak_admin_password"],
                    "KC_HOSTNAME": "http://localhost:19080",
                },
                "volumes": [
                    "./realm:/opt/keycloak/data/import:ro",
                    "keycloak-data:/opt/keycloak/data",
                ],
            },
            "budibase": {
                "image": BUDIBASE_IMAGE,
                "restart": "no",
                "mem_limit": "4g",
                "ports": ["127.0.0.1:19000:80", "127.0.0.1:19080:19080"],
                "environment": {
                    "BB_ADMIN_USER_EMAIL": private["budibase_admin_email"],
                    "BB_ADMIN_USER_PASSWORD": private["budibase_admin_password"],
                    "ENABLE_ANALYTICS": "false",
                },
                "volumes": ["budibase-data:/data"],
            },
        },
        "volumes": {"keycloak-data": {}, "budibase-data": {}},
    }
    for name, data in (("private.json", private), ("realm/aether-lab.json", realm)):
        path = directory / name
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        path.chmod(0o600)
    path = directory / "compose.yaml"
    path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.directory)
    print("Identity lab prepared; secrets were not printed. Use the generated compose.yaml.")


if __name__ == "__main__":
    main()
