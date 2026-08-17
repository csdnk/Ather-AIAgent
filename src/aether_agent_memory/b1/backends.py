"""Pluggable B1 embedding backends.

Only the ONNX strategy is production-capable today. OpenVINO and IPEX are
declared as reserved strategies so configuration and observability stay stable
while their adapters are implemented and qualified later.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Protocol

import numpy as np


class BackendUnavailableError(RuntimeError):
    """Raised when a configured backend has no usable adapter/runtime."""


class SidecarEmbeddingBackend(Protocol):
    backend_key: str
    engine_name: str
    model_name: str
    dimension: int | None

    def load(self) -> None: ...

    def embed(
        self,
        texts: list[str],
        input_types: list[str],
        batch_size: int,
    ) -> list[np.ndarray]: ...

    def runtime_details(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class BackendConfig:
    model_name: str
    cache_dir: Path
    model_path: Path | None
    threads: int


@dataclass(frozen=True)
class BackendDescriptor:
    key: str
    engine: str
    implementation_status: str
    adapter_implemented: bool
    runtime_package: str
    install_hint: str
    intended_simd_policy: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "engine": self.engine,
            "implementation_status": self.implementation_status,
            "adapter_implemented": self.adapter_implemented,
            "runtime_package": self.runtime_package,
            "runtime_package_installed": find_spec(self.runtime_package) is not None,
            "install_hint": self.install_hint,
            "intended_simd_policy": self.intended_simd_policy,
        }


BACKEND_DESCRIPTORS: dict[str, BackendDescriptor] = {
    "onnx": BackendDescriptor(
        key="onnx",
        engine="FastEmbed + ONNX Runtime CPUExecutionProvider",
        implementation_status="available",
        adapter_implemented=True,
        runtime_package="onnxruntime",
        install_hint="pip install -e '.[b1-sidecar]'",
        intended_simd_policy="ONNX Runtime automatic CPU dispatch",
    ),
    "openvino": BackendDescriptor(
        key="openvino",
        engine="OpenVINO Runtime",
        implementation_status="reserved-not-implemented",
        adapter_implemented=False,
        runtime_package="openvino",
        install_hint="Install and qualify an OpenVINO adapter before selecting this strategy.",
        intended_simd_policy="OpenVINO/oneDNN automatic CPU dispatch",
    ),
    "ipex": BackendDescriptor(
        key="ipex",
        engine="Intel Extension for PyTorch",
        implementation_status="reserved-not-implemented",
        adapter_implemented=False,
        runtime_package="intel_extension_for_pytorch",
        install_hint="Install and qualify an IPEX adapter on a supported OS/PyTorch matrix.",
        intended_simd_policy="IPEX/oneDNN automatic CPU dispatch",
    ),
}


class FastEmbedOnnxBackend:
    backend_key = "onnx"
    engine_name = "fastembed-onnxruntime-cpu"

    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self.model_name = config.model_name
        self.dimension: int | None = None
        self._model: Any = None

    def load(self) -> None:
        if self._model is not None:
            return
        from fastembed import TextEmbedding

        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {}
        if self.config.model_path is not None:
            if not self.config.model_path.is_dir():
                raise FileNotFoundError(
                    f"AETHER_B1_MODEL_PATH does not exist: {self.config.model_path}"
                )
            kwargs["specific_model_path"] = str(self.config.model_path)
        self._model = TextEmbedding(
            model_name=self.model_name,
            cache_dir=str(self.config.cache_dir),
            threads=self.config.threads,
            providers=["CPUExecutionProvider"],
            **kwargs,
        )
        warmup = next(iter(self._model.passage_embed(["B1 sidecar warmup"])))
        self.dimension = int(np.asarray(warmup).size)

    def embed(
        self,
        texts: list[str],
        input_types: list[str],
        batch_size: int,
    ) -> list[np.ndarray]:
        if self._model is None:
            raise RuntimeError("embedding model is not loaded")
        output: list[np.ndarray | None] = [None] * len(texts)
        for input_type in ("passage", "query"):
            positions = [index for index, value in enumerate(input_types) if value == input_type]
            if not positions:
                continue
            selected = [texts[index] for index in positions]
            method = (
                self._model.passage_embed if input_type == "passage" else self._model.query_embed
            )
            vectors = list(method(selected, batch_size=batch_size))
            if len(vectors) != len(positions):
                raise ValueError("embedding result count mismatch")
            for position, vector in zip(positions, vectors, strict=True):
                output[position] = np.asarray(vector, dtype=np.float32)
        if any(vector is None for vector in output):
            raise ValueError("embedding backend returned an incomplete result")
        return [vector for vector in output if vector is not None]

    def runtime_details(self) -> dict[str, Any]:
        try:
            import onnxruntime as ort
        except ImportError:
            providers: list[str] = []
            version = None
        else:
            providers = [str(provider) for provider in ort.get_available_providers()]
            version = ort.__version__
        return {
            "backend_key": self.backend_key,
            "engine": self.engine_name,
            "provider": "CPUExecutionProvider",
            "available_providers": providers,
            "onnxruntime_version": version,
        }


BackendFactory = Callable[[BackendConfig], SidecarEmbeddingBackend]
_BACKEND_FACTORIES: dict[str, BackendFactory] = {"onnx": FastEmbedOnnxBackend}


def register_backend(key: str, factory: BackendFactory, descriptor: BackendDescriptor) -> None:
    """Register a future backend without changing Sidecar request processing."""

    normalized = key.strip().lower()
    if not normalized:
        raise ValueError("backend key must not be empty")
    _BACKEND_FACTORIES[normalized] = factory
    BACKEND_DESCRIPTORS[normalized] = descriptor


def create_backend(key: str, config: BackendConfig) -> SidecarEmbeddingBackend:
    normalized = key.strip().lower()
    factory = _BACKEND_FACTORIES.get(normalized)
    if factory is not None:
        return factory(config)
    descriptor = BACKEND_DESCRIPTORS.get(normalized)
    if descriptor is not None:
        raise BackendUnavailableError(
            f"backend '{normalized}' is {descriptor.implementation_status}: "
            f"{descriptor.install_hint}"
        )
    supported = ", ".join(sorted(BACKEND_DESCRIPTORS))
    raise BackendUnavailableError(f"unknown backend '{normalized}'; configured keys: {supported}")


def describe_backends(selected: str) -> dict[str, Any]:
    return {
        "selected": selected,
        "strategies": [
            {**descriptor.as_dict(), "selected": key == selected}
            for key, descriptor in BACKEND_DESCRIPTORS.items()
        ],
        "replacement_contract": (
            "Implement SidecarEmbeddingBackend and call register_backend(); "
            "the HTTP/service layer remains unchanged."
        ),
    }
