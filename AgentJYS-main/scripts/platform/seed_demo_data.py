"""Import synthetic accounts into the exact isolated local lab, without resets.

Credentials remain in external private files. No P3 memories or LLM responses
are inserted: conversation_scenarios are inputs for later real acceptance.
"""

import argparse
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

import httpx

from aether_platform.directory import Directory

KC = "http://localhost:19080"
ISSUER = KC + "/realms/aether-lab"
EXISTING = {"platform_admin", "tenant_admin_a", "tenant_admin_b", "user_a", "user_b"}


def write_receipts(path: Path, receipts: dict) -> None:
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(receipts, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_target(config: dict) -> None:
    target = urlparse(config["database_dsn"])
    if (
        target.scheme != "postgresql"
        or target.hostname != "127.0.0.1"
        or target.port != 19432
        or target.path != "/aether_platform_lab"
        or target.query
        or target.fragment
        or config["issuer"] != ISSUER
    ):
        raise ValueError("Demo import is restricted to the dedicated local lab")


def checked(response: httpx.Response, expected: int = 200):
    if response.status_code != expected:
        raise RuntimeError(f"Lab identity operation failed: HTTP {response.status_code}")
    return response


def seed(config_path: Path, data_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_target(config)
    if Path(__file__).resolve().parents[3] in config_path.resolve().parents:
        raise ValueError("Lab configuration must be outside the checkout")
    data = json.loads(data_path.read_text(encoding="utf-8"))
    if data["dataset"] != "aether-agent-demo-v1":
        raise ValueError("Unsupported fixture dataset")
    identity_dir = Path(config["identity_lab_directory"])
    private = json.loads((identity_dir / "private.json").read_text(encoding="utf-8"))
    credentials_path = config_path.parent / "test-accounts.private.json"
    if credentials_path.exists():
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    else:
        credentials = {
            user["username"]: private.get(user["username"] + "_password")
            or secrets.token_urlsafe(24)
            for user in data["users"]
        }
        with credentials_path.open("x", encoding="utf-8") as output:
            json.dump(credentials, output, indent=2)
        credentials_path.chmod(0o600)
    db = Directory(config["database_dsn"])
    db.migrate()
    # Check the fixture marker before provisioning identity accounts.
    db.seed(data["dataset"], data["tenants"], [])
    receipts_path = config_path.parent / "created-identities.json"
    try:
        receipts = json.loads(receipts_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        receipts = {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        # A damaged local receipt has no authority. Recover only after reading
        # the admin-only ownership marker from the fixed identity server.
        backup = receipts_path.with_name(receipts_path.name + ".invalid-" + secrets.token_hex(8))
        backup.write_bytes(receipts_path.read_bytes())
        receipts = {}
    if not isinstance(receipts, dict):
        receipts = {}
    users = []
    with httpx.Client(base_url=KC, timeout=30, trust_env=False, follow_redirects=False) as client:
        token = checked(
            client.post(
                "/realms/master/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": "lab-admin",
                    "password": private["keycloak_admin_password"],
                },
            )
        ).json()["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        profile_url = "/admin/realms/aether-lab/users/profile"
        profile = checked(client.get(profile_url)).json()
        marker = next((a for a in profile["attributes"] if a["name"] == "aether_fixture"), None)
        if marker is None:
            profile["attributes"].append(
                {
                    "name": "aether_fixture",
                    "displayName": "Lab fixture ownership",
                    "permissions": {"view": ["admin"], "edit": ["admin"]},
                    "multivalued": False,
                }
            )
            checked(client.put(profile_url, json=profile))
        verified_profile = checked(client.get(profile_url)).json()
        marker = next(a for a in verified_profile["attributes"] if a["name"] == "aether_fixture")
        if marker.get("permissions") != {"view": ["admin"], "edit": ["admin"]}:
            raise ValueError("Fixture ownership must be editable only by identity administrators")
        # Keycloak 26.8 requires the introspecting client in the access-token audience.
        clients = checked(
            client.get("/admin/realms/aether-lab/clients", params={"clientId": "platform-bff"})
        ).json()
        if len(clients) != 1:
            raise ValueError("Unique platform-bff client required")
        mapper_url = (
            "/admin/realms/aether-lab/clients/" + clients[0]["id"] + "/protocol-mappers/models"
        )
        mappers = checked(client.get(mapper_url)).json()
        if not any(m.get("name") == "platform-bff-audience" for m in mappers):
            checked(
                client.post(
                    mapper_url,
                    json={
                        "name": "platform-bff-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.client.audience": "platform-bff",
                            "id.token.claim": "false",
                            "access.token.claim": "true",
                            "introspection.token.claim": "true",
                        },
                    },
                ),
                201,
            )
        endpoint = "/admin/realms/aether-lab/users"
        for user in data["users"]:
            username = user["username"]
            found = checked(
                client.get(endpoint, params={"username": username, "exact": "true"})
            ).json()
            if not found:
                created = checked(
                    client.post(
                        endpoint,
                        json={
                            "username": username,
                            "enabled": user["enabled"],
                            "email": username + "@aether-lab.invalid",
                            "emailVerified": True,
                            "firstName": user["display_name"],
                            "lastName": "Test",
                            "attributes": {"aether_fixture": [data["dataset"]]},
                            "credentials": [
                                {
                                    "type": "password",
                                    "value": credentials[username],
                                    "temporary": False,
                                }
                            ],
                        },
                    ),
                    201,
                )
                receipts[username] = created.headers["location"].rsplit("/", 1)[1]
                write_receipts(receipts_path, receipts)
                found = checked(
                    client.get(endpoint, params={"username": username, "exact": "true"})
                ).json()
            if len(found) != 1:
                raise ValueError("Ambiguous fixture identity")
            account = checked(client.get(endpoint + "/" + found[0]["id"])).json()
            marked = account.get("attributes", {}).get("aether_fixture") == [data["dataset"]]
            if (
                account["username"] != username
                or account.get("email") != username + "@aether-lab.invalid"
                or (
                    username not in EXISTING
                    and receipts.get(username) != account["id"]
                    and not marked
                )
            ):
                raise ValueError("Existing account does not belong to this fixture")
            if not marked:
                attributes = {**account.get("attributes", {}), "aether_fixture": [data["dataset"]]}
                checked(
                    client.put(
                        endpoint + "/" + account["id"], json={**account, "attributes": attributes}
                    ),
                    204,
                )
                readback = checked(client.get(endpoint + "/" + account["id"])).json()
                if readback.get("attributes", {}).get("aether_fixture") != [data["dataset"]]:
                    raise ValueError("Fixture ownership was not persisted")
                if any(
                    readback.get(key) != account.get(key)
                    for key in ("email", "firstName", "lastName", "enabled", "emailVerified")
                ):
                    raise ValueError("Identity provider changed existing profile fields")
            if username not in EXISTING and receipts.get(username) != account["id"]:
                receipts[username] = account["id"]
                write_receipts(receipts_path, receipts)
            users.append(
                {
                    **user,
                    "issuer": ISSUER,
                    "subject": account["id"],
                    "enabled": user["enabled"] and account["enabled"],
                }
            )
    db.seed(data["dataset"], data["tenants"], users)
    # No update/reset of an existing user's password, enabled state or profile.
    manifest = {
        "dataset": data["dataset"],
        "tenants": len(data["tenants"]),
        "accounts": len(users),
        "conversation_scenarios": len(data["conversation_scenarios"]),
        "p3_imported": False,
        "projection_status": "identity_only",
    }
    (config_path.parent / "fixture-result.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--data",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "deploy/platform/demo-data.json",
    )
    args = parser.parse_args()
    print(json.dumps(seed(args.config, args.data)))


if __name__ == "__main__":
    main()
