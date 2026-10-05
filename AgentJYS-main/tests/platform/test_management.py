"""Real signed Budibase identities must still obey the business directory."""

import json
import os
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_bff_live import app  # noqa: F401
from test_identity_lab_live import login


@pytest.mark.parametrize(
    "username,count", [("platform_admin", 3), ("tenant_admin_a", 1), ("tenant_admin_b", 1)]
)
def test_management_scope_comes_from_signed_current_identity(app, username, count):  # noqa: F811
    config_path = Path(os.environ["AETHER_PLATFORM_LAB_CONFIG"])
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    private = json.loads((Path(config["identity_lab_directory"]) / "private.json").read_text())
    passwords = json.loads((config_path.parent / "test-accounts.private.json").read_text())
    private.update({k + "_password": v for k, v in passwords.items()})
    browser, identity = login(private, username)
    with closing(browser), TestClient(app) as client:
        headers = {
            "authorization": "Bearer " + identity["oauth2"]["accessToken"],
            "x-tenant-id": "demo_tenant_b",
            "x-role": "platform_admin",
        }
        result = client.get("/management-api/tenants", headers=headers)
        assert result.status_code == 200
        assert len(result.json()) == count
        if username == "platform_admin":
            assert client.post(
                "/management-api/disable", headers=headers, json={"username": "platform_admin"}
            ).status_code in (403, 409)
        rows = client.get("/management-api/users", headers=headers).json()
        if username == "tenant_admin_a":
            assert (
                client.post(
                    "/management-api/disable", headers=headers, json={"username": "user_b"}
                ).status_code
                == 403
            )
            assert {row["tenant_id"] for row in rows} == {"demo_tenant_a"}
            assert (
                client.post(
                    "/management-api/profile",
                    headers=headers,
                    json={"username": "user_b", "display_name": "forbidden"},
                ).status_code
                == 403
            )
            assert (
                client.post(
                    "/management-api/profile",
                    headers=headers,
                    json={"username": "tenant_admin_a", "display_name": "forbidden"},
                ).status_code
                == 403
            )
        assert (
            client.get(
                "/management-api/users", headers={"authorization": "Bearer forged"}
            ).status_code
            == 401
        )


def test_ordinary_user_cannot_access_management(app):  # noqa: F811
    config_path = Path(os.environ["AETHER_PLATFORM_LAB_CONFIG"])
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    private = json.loads((Path(config["identity_lab_directory"]) / "private.json").read_text())
    browser, identity = login(private, "user_a")
    with closing(browser), TestClient(app) as client:
        headers = {"authorization": "Bearer " + identity["oauth2"]["accessToken"]}
        assert client.get("/management-api/users", headers=headers).status_code == 403
        assert client.get("/management-api/tenants", headers=headers).status_code == 403
        assert (
            client.post(
                "/management-api/disable", headers=headers, json={"username": "user_b"}
            ).status_code
            == 403
        )
