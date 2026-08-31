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


def test_production_requires_redis_even_with_sqlite_memory_store() -> None:
    settings = AppSettings(
        profile="production",
        memory_store="sqlite",
        redis_url="",
        b1_sidecar_url="http://b1:18081",
        p2_endpoint="engine:50052",
    )

    with pytest.raises(ValueError, match="Redis URL for runtime state and traces"):
        settings.validate_for_profile()


def test_production_rejects_sqlite_memory_store_with_redis_configured() -> None:
    settings = AppSettings(
        profile="production",
        memory_store="sqlite",
        redis_url="redis://r:6379/0",
        b1_sidecar_url="http://b1:18081",
        p2_endpoint="engine:50052",
    )

    with pytest.raises(ValueError, match="Redis-backed memory_store"):
        settings.validate_for_profile()


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


def test_retrieval_trace_retention_reads_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("AETHER_P3_RETRIEVAL_TRACE_TTL_SECONDS", "7200")
    monkeypatch.setenv("AETHER_P3_RETRIEVAL_TRACE_MAX_ENTRIES", "250")
    monkeypatch.setenv("AETHER_P3_RETRIEVAL_TRACE_TIMEOUT_SECONDS", "0.5")

    settings = AppSettings()

    assert settings.retrieval_trace_ttl_seconds == 7200
    assert settings.retrieval_trace_max_entries == 250
    assert settings.retrieval_trace_timeout_seconds == 0.5


def test_retrieval_trace_retention_must_be_positive() -> None:
    with pytest.raises(ValueError):
        AppSettings(retrieval_trace_ttl_seconds=0)
    with pytest.raises(ValueError):
        AppSettings(retrieval_trace_max_entries=0)
    with pytest.raises(ValueError):
        AppSettings(retrieval_trace_timeout_seconds=0)


def test_context_facts_are_persistent_unless_ttl_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv("AETHER_P3_CONTEXT_FACT_TTL_SECONDS", raising=False)
    assert AppSettings().context_fact_ttl_seconds is None

    monkeypatch.setenv("AETHER_P3_CONTEXT_FACT_TTL_SECONDS", "86400")
    assert AppSettings().context_fact_ttl_seconds == 86400

    with pytest.raises(ValueError):
        AppSettings(context_fact_ttl_seconds=0)


def test_context_reindex_bounds_read_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("AETHER_P3_CONTEXT_REINDEX_MAX_ITEMS", "500")
    monkeypatch.setenv("AETHER_P3_CONTEXT_REINDEX_MAX_CHILDREN", "50")

    settings = AppSettings()

    assert settings.context_reindex_max_items == 500
    assert settings.context_reindex_max_children == 50
