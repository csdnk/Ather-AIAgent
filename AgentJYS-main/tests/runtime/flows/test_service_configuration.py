"""Current Azure deployment and explicit refusal of retired P2 runtime modes."""

import os

import pytest
import yaml
from pydantic import ValidationError

from aether_agent_memory.runtime.flows.config import ServiceConfiguration
from azure_configuration_support import settings


@pytest.mark.parametrize("profile", ["development", "test", "staging", "production"])
def test_current_p2_runtime_is_retired_in_every_profile(tmp_path, profile):
    value = settings(tmp_path, profile)
    value.update(storage_mode="current_p2", metadata_backend="p2", p2_endpoint="127.0.0.1:50052")
    value.pop("azure_storage")
    with pytest.raises(ValidationError, match="current P2 reference metadata is retired"):
        ServiceConfiguration.model_validate(value)


def test_missing_formal_p2_adapters_block_startup_before_data_creation(tmp_path):
    from aether_agent_memory.runtime.flows.application import Service

    value = settings(tmp_path, "production")
    value.update(storage_mode="production_p2", metadata_backend="p2", p2_secure=True,
                 p2_endpoint="127.0.0.1:50052")
    value.pop("azure_storage")
    config = ServiceConfiguration.model_validate(value)
    with pytest.raises(ValueError, match="production P2.*transaction.*cache"):
        Service(config)
    assert not config.data_dir.exists()


@pytest.mark.parametrize("profile", ["development", "test"])
def test_production_serve_refuses_a_development_config_before_startup(
    tmp_path, monkeypatch, capsys, profile
):
    from aether_agent_memory.runtime.flows.cli import main

    config = ServiceConfiguration.model_validate(settings(tmp_path, profile))
    path = tmp_path / "service.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setenv("AETHER_SERVICE_CONFIG", "unchanged")
    monkeypatch.setattr(
        "sys.argv", ["p3", "serve", "--config", str(path), "--require-profile", "production"]
    )
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: pytest.fail("listener reached"))
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "requires profile production" in capsys.readouterr().err
    assert os.environ["AETHER_SERVICE_CONFIG"] == "unchanged"
    assert not config.data_dir.exists()


@pytest.mark.parametrize("required", [[], ["--require-profile", "development"]])
def test_serve_accepts_matching_or_unrestricted_profile(tmp_path, monkeypatch, required):
    from aether_agent_memory.runtime.flows.cli import main

    config = ServiceConfiguration.model_validate(settings(tmp_path))
    path = tmp_path / "service.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["p3", "serve", "--config", str(path), *required])
    monkeypatch.setenv("AETHER_SERVICE_CONFIG", "unchanged")
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert main() == 0
    assert calls == [
        (("aether_agent_memory.runtime.flows.application:application",),
         {"factory": True, "host": "127.0.0.1", "port": 8080, "workers": 1})
    ]
    assert os.environ["AETHER_SERVICE_CONFIG"] == str(path.resolve())


def test_operate_watermarks_have_defaults_and_accept_explicit_thresholds(tmp_path):
    value = settings(tmp_path)
    config = ServiceConfiguration.model_validate(value)
    assert (
        config.operate_buffer_limit,
        config.operate_high_watermark,
        config.operate_low_watermark,
    ) == (1000, 800, 600)
    value.update(operate_buffer_limit=100, operate_high_watermark=80, operate_low_watermark=60)
    config = ServiceConfiguration.model_validate(value)
    assert config.operate_high_watermark == 80


@pytest.mark.parametrize("high,low,limit", [(800, 800, 1000), (1000, 600, 1000), (3, 4, 10)])
def test_operate_watermarks_reject_invalid_order(tmp_path, high, low, limit):
    value = settings(tmp_path)
    value.update(operate_buffer_limit=limit, operate_high_watermark=high, operate_low_watermark=low)
    with pytest.raises(ValidationError, match="operate low < high < buffer limit"):
        ServiceConfiguration.model_validate(value)
