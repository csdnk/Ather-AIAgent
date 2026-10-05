"""Enable first-party form login only on the isolated Agent's confidential client."""

import argparse
import json
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if config["issuer"] != "http://localhost:19080/realms/aether-lab":
        raise ValueError("Only the isolated localhost lab is supported")
    private = json.loads((Path(config["identity_lab_directory"]) / "private.json").read_text())
    with httpx.Client(base_url="http://localhost:19080", trust_env=False, timeout=20) as client:
        response = client.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "lab-admin",
                "password": private["keycloak_admin_password"],
            },
        )
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        response = client.get(
            "/admin/realms/aether-lab/clients", params={"clientId": "platform-bff"}
        )
        response.raise_for_status()
        matches = response.json()
        if len(matches) != 1 or matches[0]["publicClient"]:
            raise ValueError("Expected one confidential Agent client")
        target = matches[0]
        target["directAccessGrantsEnabled"] = True
        response = client.put("/admin/realms/aether-lab/clients/" + target["id"], json=target)
        response.raise_for_status()
        verified = client.get("/admin/realms/aether-lab/clients/" + target["id"])
        verified.raise_for_status()
        assert verified.json()["directAccessGrantsEnabled"] is True
    print("Agent confidential client form login enabled; Budibase client unchanged.")


if __name__ == "__main__":
    main()
