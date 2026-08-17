from pathlib import Path

import pytest

from aether_agent_memory.config.settings import Settings


@pytest.mark.unit
def test_settings_defaults() -> None:
    settings = Settings()
    assert settings.default_ttl_seconds == 3600
    assert settings.max_items_per_session == 200
    assert settings.archive_batch_size == 50
    assert settings.default_budget_tokens == 4096
    assert settings.max_candidates == 100
    assert settings.half_life_hours == 168.0
    assert settings.b1_grpc == "localhost:50051"
    assert settings.p2_grpc == "localhost:50052"
    assert settings.otel_exporter == "localhost:4317"


@pytest.mark.unit
def test_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_B2_DEFAULT_TTL_SECONDS", "120")
    monkeypatch.setenv("AETHER_B2_B1_GRPC", "remote:9999")
    settings = Settings()
    assert settings.default_ttl_seconds == 120
    assert settings.b1_grpc == "remote:9999"


@pytest.mark.unit
def test_settings_from_toml() -> None:
    config_path = Path(__file__).resolve().parents[2] / "configs" / "default.toml"
    settings = Settings.from_toml(config_path)
    assert settings.default_ttl_seconds == 3600
    assert settings.max_items_per_session == 200
    assert settings.archive_batch_size == 50
    assert settings.default_budget_tokens == 4096
    assert settings.max_candidates == 100
    assert settings.half_life_hours == 168.0
    assert settings.b1_grpc == "localhost:50051"
