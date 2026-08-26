"""AppSettings profile validation tests."""

from __future__ import annotations

import pytest

from aether_agent_memory.config.app_settings import AppSettings


def test_production_rejects_demo() -> None:
    with pytest.raises(ValueError, match="cannot enable demo"):
        AppSettings(profile="production", enable_demo=True).validate_for_profile()


def test_production_requires_real_b1() -> None:
    settings = AppSettings(
        profile="production",
        b1_sidecar_url="",
        b1_embedding_url="",
        enable_demo=False,
    )
    with pytest.raises(ValueError, match="real B1 sidecar URL"):
        settings.validate_for_profile()


def test_production_accepts_configured_dependencies() -> None:
    AppSettings(
        profile="production",
        b1_sidecar_url="http://b1:18081",
        p2_endpoint="engine:50052",
        redis_url="redis://r:6379/0",
        enable_demo=False,
    ).validate_for_profile()


def test_demo_and_integration_do_not_require_b1() -> None:
    AppSettings(profile="demo").validate_for_profile()
    AppSettings(profile="integration").validate_for_profile()


def test_profile_normalization() -> None:
    assert AppSettings(profile="local").profile == "integration"
    assert AppSettings(profile="prod").profile == "production"
    assert AppSettings(profile="mock").profile == "demo"


def test_profile_reads_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("AETHER_RUNTIME_PROFILE", "production")
    assert AppSettings().profile == "production"
    monkeypatch.setenv("AETHER_RUNTIME_PROFILE", "demo")
    assert AppSettings().profile == "demo"
    monkeypatch.delenv("AETHER_RUNTIME_PROFILE")
    assert AppSettings().profile == "integration"


def test_production_gate_fails_closed_via_environment(monkeypatch) -> None:
    monkeypatch.setenv("AETHER_RUNTIME_PROFILE", "production")
    monkeypatch.setenv("AETHER_B1_SIDECAR_URL", "")
    monkeypatch.setenv("AETHER_B1_EMBEDDING_URL", "")
    monkeypatch.setenv("AETHER_ENABLE_DEMO", "false")
    with pytest.raises(ValueError, match="real B1 sidecar URL"):
        AppSettings().validate_for_profile()


def test_safe_status_does_not_leak_secrets() -> None:
    status = AppSettings(profile="demo", redis_url="redis://secret:6379/0").safe_status()
    assert "redis_url" not in status
    assert status["profile"] == "demo"
    assert status["version"]
