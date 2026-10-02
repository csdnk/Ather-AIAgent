"""Regression for incremental local Keycloak account configuration."""

import importlib.util
import json
from pathlib import Path

import pytest


def load_setup(monkeypatch):
    scripts = Path(__file__).resolve().parents[3] / "scripts/p3"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "self_service_setup_test", scripts / "keycloak_self_service.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_incremental_setup_preserves_identity_and_saves_recovery_snapshot(tmp_path, monkeypatch):
    module = load_setup(monkeypatch)
    state = {
        "realm": "p3-demo",
        "organizationsEnabled": True,
        "registrationAllowed": False,
        "custom_setting": "preserved",
        "smtpServer": {},
    }
    writes = []

    class Admin:
        http = type("HTTP", (), {"close": lambda self: None})()

        def __init__(self, directory):
            pass

        def get(self, path):
            assert path == ""
            return dict(state)

        def request(self, method, path, **kwargs):
            assert (method, path) == ("PUT", "")
            writes.append(kwargs["json"])
            state.update(kwargs["json"])

    monkeypatch.setattr(module, "Admin", Admin)
    imported = {
        "realm": "p3-demo",
        "users": [{"username": "existing"}],
        "clients": [{"clientId": "existing"}],
    }
    (tmp_path / "realm.json").write_text(json.dumps(imported), "utf-8")
    module.apply(tmp_path, start_mail=False)
    assert len(writes) == 1
    assert state["custom_setting"] == "preserved"
    assert state["organizationsEnabled"] is True
    assert state["verifyEmail"] is True
    updated = json.loads((tmp_path / "realm.json").read_text("utf-8"))
    assert updated["users"] == imported["users"]
    assert updated["clients"] == imported["clients"]
    backups = list((tmp_path / "backups").glob("self-service-*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text("utf-8"))["registrationAllowed"] is False


def test_existing_mail_service_is_never_silently_replaced(tmp_path, monkeypatch):
    module = load_setup(monkeypatch)

    class Admin:
        http = type("HTTP", (), {"close": lambda self: None})()

        def __init__(self, directory):
            pass

        def get(self, path):
            return {"smtpServer": {"host": "existing.example"}}

        def request(self, *args, **kwargs):
            pytest.fail("must not change the realm")

    monkeypatch.setattr(module, "Admin", Admin)
    with pytest.raises(ValueError, match="refusing to replace"):
        module.apply(tmp_path, start_mail=False)
    assert not (tmp_path / "compose.self-service-mail.yaml").exists()


def test_mail_sink_is_loopback_only_and_has_no_outbound_relay(monkeypatch):
    module = load_setup(monkeypatch)
    service = module.local_mail_compose()["services"]["mailpit"]
    assert all(port.startswith("127.0.0.1:") for port in service["ports"])
    assert not any("RELAY" in key or "FORWARD" in key for key in service["environment"])
    assert module.SMTP["host"] == "mailpit"
    assert module.REALM_OPTIONS["adminEventsDetailsEnabled"] is False
