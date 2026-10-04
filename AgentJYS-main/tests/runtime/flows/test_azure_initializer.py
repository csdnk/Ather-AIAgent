"""Deployment initialization resolves assets and preserves existing files."""

import json

import pytest
import yaml
from test_azure_storage_configuration import azure_settings

from aether_agent_memory.runtime.flows.cli import initialize
from aether_agent_memory.runtime.flows.config import ServiceConfiguration


def template_at(tmp_path):
    value = azure_settings(tmp_path)
    value["recall_config"] = "recall.json"
    value["embedding_config"] = "embedding.json"
    path = tmp_path / "template.yaml"
    path.write_text(yaml.safe_dump(json.loads(json.dumps(value, default=str))), "utf-8")
    return path


def test_init_resolves_template_assets_before_validation(tmp_path):
    template = template_at(tmp_path)
    target = initialize(tmp_path / "deployment", template=template)
    result = ServiceConfiguration.load(target)
    assert result.storage_mode == "azure"
    assert result.recall_config == tmp_path / "recall.json"
    assert result.embedding_config == tmp_path / "embedding.json"
    assert not (target.parent / "credential").exists()


def test_init_preserves_existing_deployment_bytes(tmp_path):
    template = template_at(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    existing = deployment / "service.yaml"
    existing.write_bytes(b"user is editing this file")
    with pytest.raises(FileExistsError):
        initialize(deployment, template=template)
    assert existing.read_bytes() == b"user is editing this file"


def test_init_rejects_retired_reference_template_without_creating_target(tmp_path):
    template = tmp_path / "retired.yaml"
    template.write_text("storage_mode: current_p2\n", "utf-8")
    with pytest.raises(ValueError, match="Azure"):
        initialize(tmp_path / "deployment", template=template)
    assert not (tmp_path / "deployment").exists()
