"""Asset preparation protects repository paths and existing developer files."""

import importlib.util
import json
from pathlib import Path

import pytest


def assets_module():
    path = Path(__file__).resolve().parents[2] / "scripts/p3/prepare_aks_test_assets.py"
    spec = importlib.util.spec_from_file_location("prepare_aks_test_assets", path)
    assert spec is not None, "AKS asset preparation is missing"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_assets_cannot_be_written_into_repository():
    root = Path(__file__).resolve().parents[3]
    with pytest.raises(SystemExit) as error:
        assets_module().main(["--directory", str(root / "unsafe-assets")])
    assert error.value.code == 2


def test_existing_assets_are_preserved(tmp_path):
    (tmp_path / "native.json").write_text("developer configuration")
    with pytest.raises(SystemExit) as error:
        assets_module().main(["--directory", str(tmp_path)])
    assert error.value.code == 2
    assert (tmp_path / "native.json").read_text() == "developer configuration"


def test_prepared_config_and_tokenizer_cache_live_in_external_directory(tmp_path, monkeypatch):
    module = assets_module()
    directory = tmp_path / "new-assets"

    def model(target):
        target.mkdir()
        (target / "model_optimized.onnx").write_bytes(b"asset fixture")

    def temporal(target):
        target.write_bytes(b"verified Linux CLI fixture")

    monkeypatch.setattr(module, "download_model", model)
    monkeypatch.setattr(module, "download_temporal", temporal)
    monkeypatch.setattr(module, "download_tokenizers", lambda target: target.mkdir())
    assert module.main(["--directory", str(directory)]) == 0
    native = json.loads((directory / "native.json").read_text())
    assert native["model_path"] == str(directory / "model")
    assert Path(native["cache_dir"]).is_relative_to(directory)
    assert (directory / "temporal").is_file()


def test_model_download_uses_fastembed_description_object(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import fastembed

    module = assets_module()
    description = SimpleNamespace(model=module.MODEL_NAME, sources=object())
    source = tmp_path / "downloaded"
    source.mkdir()
    (source / "model_optimized.onnx").write_bytes(b"model")

    class Model:
        @staticmethod
        def list_supported_models():
            return [{"model": module.MODEL_NAME}]

        @staticmethod
        def _get_model_description(name):
            assert name == module.MODEL_NAME
            return description

        @staticmethod
        def download_model(value, cache_dir):
            assert value is description
            assert len(str(Path(cache_dir).resolve())) < 160
            return source

    monkeypatch.setattr(fastembed, "TextEmbedding", Model)
    long_parent = tmp_path / ("external-assets-" * 9)
    long_parent.mkdir()
    target = long_parent / "model"
    module.download_model(target)
    assert (target / "model_optimized.onnx").read_bytes() == b"model"
