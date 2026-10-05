"""The isolated identity lab never modifies a resident P3 deployment."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/platform/prepare_identity_lab.py"


def prepare(directory):
    assert SCRIPT.is_file(), "The reproducible identity lab preparer is missing"
    spec = importlib.util.spec_from_file_location("identity_lab", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prepare(directory)


def test_lab_has_loopback_ports_and_no_p3_service(tmp_path):
    prepare(tmp_path / "lab")
    config = yaml.safe_load((tmp_path / "lab/compose.yaml").read_text())
    assert set(config["services"]) == {"keycloak", "budibase"}
    for service in config["services"].values():
        assert all(port.startswith("127.0.0.1:") for port in service["ports"])
        assert service["restart"] == "no"
    assert config["services"]["budibase"]["image"] == "budibase/budibase:v3.47.0"


def test_browser_and_budibase_share_the_same_reachable_oidc_issuer(tmp_path):
    prepare(tmp_path / "lab")
    config = yaml.safe_load((tmp_path / "lab/compose.yaml").read_text())
    kc, bb = config["services"]["keycloak"], config["services"]["budibase"]
    assert kc.get("network_mode") == "service:budibase"
    assert kc["environment"]["KC_HOSTNAME"] == "http://localhost:19080"
    assert "127.0.0.1:19080:19080" in bb["ports"]
    assert "--http-port=19080" in kc["command"]


def test_accounts_and_clients_do_not_share_passwords_or_wildcard_redirects(tmp_path):
    prepare(tmp_path / "lab")
    realm = json.loads((tmp_path / "lab/realm/aether-lab.json").read_text())
    users = realm["users"]
    assert {u["username"] for u in users} == {
        "platform_admin",
        "tenant_admin_a",
        "tenant_admin_b",
        "user_a",
        "user_b",
    }
    passwords = [u["credentials"][0]["value"] for u in users]
    assert len(set(passwords)) == len(users)
    assert all(len(p) >= 24 for p in passwords)
    for client in realm["clients"]:
        assert client["directAccessGrantsEnabled"] is (client["clientId"] == "platform-bff")
        assert client["publicClient"] is False
        assert not client["serviceAccountsEnabled"]
        assert all("*" not in uri for uri in client["redirectUris"])
    assert realm["registrationAllowed"] is False


def test_existing_directory_is_preserved(tmp_path):
    directory = tmp_path / "lab"
    directory.mkdir()
    marker = directory / "user-file.txt"
    marker.write_text("keep me")
    with pytest.raises(FileExistsError):
        prepare(directory)
    assert marker.read_text() == "keep me"
    assert list(directory.iterdir()) == [marker]


def test_new_lab_directory_cannot_reuse_previous_identity_volumes(tmp_path):
    prepare(tmp_path / "first")
    prepare(tmp_path / "second")
    first = yaml.safe_load((tmp_path / "first/compose.yaml").read_text())
    second = yaml.safe_load((tmp_path / "second/compose.yaml").read_text())
    assert first["name"] != second["name"]
