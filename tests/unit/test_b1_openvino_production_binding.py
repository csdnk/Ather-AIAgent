"""Production binding checks for the B1 OpenVINO INT8 acceptance path."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_can_build_b1_with_openvino_runtime() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG INSTALL_B1_OPENVINO=false" in dockerfile
    assert ".[b1-accelerated]" in dockerfile
    assert 'INSTALL_B1_OPENVINO" = "true"' in dockerfile


def test_production_compose_binds_b1_to_openvino_int8_without_fallback() -> None:
    compose = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")

    required_fragments = [
        'INSTALL_B1_OPENVINO: "true"',
        'AETHER_B1_BACKEND: "openvino"',
        'AETHER_B1_PRECISION: "int8"',
        'AETHER_B1_FALLBACK_BACKENDS: ""',
        'AETHER_B1_ALLOW_BACKEND_FALLBACK: "false"',
        'AETHER_B1_FAIL_MODE: "closed"',
        'AETHER_B1_DYNAMIC_BATCHING: "true"',
        'AETHER_B1_OPENVINO_ASYNC: "true"',
    ]
    for fragment in required_fragments:
        assert fragment in compose
