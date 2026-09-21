"""Pluggable and recoverable CPU embedding backends owned by Recall."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np

Precision = Literal["fp32", "int8"]
DEFAULT_MODEL_BATCH_SIZE = 8


class BackendUnavailableError(RuntimeError):
    """Raised when a configured backend has no usable adapter/runtime."""


class SidecarEmbeddingBackend(Protocol):
    @property
    def backend_key(self) -> str: ...

    @property
    def engine_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int | None: ...

    @dimension.setter
    def dimension(self, value: int | None) -> None: ...

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
    model_batch_size: int = DEFAULT_MODEL_BATCH_SIZE
    precision: Precision = "fp32"
    max_length: int = 512
    inter_op_threads: int = 1
    enable_simd: bool = True
    openvino_async: bool = False
    openvino_infer_requests: int = 0
    openvino_num_streams: str = "AUTO"

    def __post_init__(self) -> None:
        if self.model_batch_size <= 0:
            raise ValueError("model_batch_size must be positive")
        if self.openvino_infer_requests < 0:
            raise ValueError("openvino_infer_requests must not be negative")


@dataclass(frozen=True)
class BackendDescriptor:
    key: str
    engine: str
    implementation_status: str
    adapter_implemented: bool
    runtime_package: str
    install_hint: str
    intended_simd_policy: str
    supported_precisions: tuple[str, ...] = ("fp32",)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "engine": self.engine,
            "implementation_status": self.implementation_status,
            "adapter_implemented": self.adapter_implemented,
            "runtime_package": self.runtime_package,
            "runtime_package_installed": _module_available(self.runtime_package),
            "install_hint": self.install_hint,
            "intended_simd_policy": self.intended_simd_policy,
            "supported_precisions": list(self.supported_precisions),
        }


BACKEND_DESCRIPTORS: dict[str, BackendDescriptor] = {
    "onnx": BackendDescriptor(
        key="onnx",
        engine="FastEmbed + ONNX Runtime CPUExecutionProvider",
        implementation_status="available",
        adapter_implemented=True,
        runtime_package="onnxruntime",
        install_hint="pip install -e '.[embedding-onnx]' (INT8 also needs '.[embedding-int8]')",
        intended_simd_policy=(
            "ONNX Runtime automatic CPU dispatch; project acceptance is validated through AVX2"
        ),
        supported_precisions=("fp32", "int8"),
    ),
    "openvino": BackendDescriptor(
        key="openvino",
        engine="OpenVINO Runtime through Optimum Intel",
        implementation_status="available-optional",
        adapter_implemented=True,
        runtime_package="openvino",
        install_hint="pip install -e '.[embedding-openvino]'",
        intended_simd_policy="OpenVINO/oneDNN automatic CPU dispatch",
        supported_precisions=("fp32", "int8"),
    ),
    "ipex": BackendDescriptor(
        key="ipex",
        engine="Intel Extension for PyTorch",
        implementation_status="available-optional",
        adapter_implemented=True,
        runtime_package="intel_extension_for_pytorch",
        install_hint=(
            "Install a matching PyTorch/IPEX pair from the Intel wheel index, then install "
            "'.[embedding-ipex]'."
        ),
        intended_simd_policy="IPEX/oneDNN automatic CPU dispatch",
        supported_precisions=("fp32",),
    ),
}


def _module_available(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _identifier_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _files_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda value: value.name):
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def _model_source(config: BackendConfig) -> str:
    if config.model_path is None:
        return config.model_name
    if not config.model_path.exists():
        raise FileNotFoundError(f"model_path does not exist: {config.model_path}")
    return str(config.model_path)


def _batch_positions(length: int, batch_size: int) -> list[tuple[int, int]]:
    return [(start, min(start + batch_size, length)) for start in range(0, length, batch_size)]


class FastEmbedOnnxBackend:
    backend_key = "onnx"
    engine_name = "fastembed-onnxruntime-cpu"
    device = "CPU"

    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self.model_name = config.model_name
        self.dimension: int | None = None
        self.precision = config.precision.upper()
        self.model_hash: str | None = None
        self._model: Any = None
        self._model_file: Path | None = None

    def _new_model(self, *, specific_model_path: Path | None, lazy_load: bool = False) -> Any:
        from fastembed import TextEmbedding

        kwargs: dict[str, Any] = {}
        if specific_model_path is not None:
            if not specific_model_path.is_dir():
                raise FileNotFoundError(
                    f"model_path must be a model directory: {specific_model_path}"
                )
            kwargs["specific_model_path"] = str(specific_model_path)
        return TextEmbedding(
            model_name=self.model_name,
            cache_dir=str(self.config.cache_dir),
            threads=self.config.threads,
            providers=["CPUExecutionProvider"],
            cuda=False,
            lazy_load=lazy_load,
            **kwargs,
        )

    @staticmethod
    def _fastembed_model_location(model: Any) -> tuple[Path, str]:
        implementation = getattr(model, "model", None)
        model_dir = getattr(implementation, "_model_dir", None)
        description = getattr(implementation, "model_description", None)
        model_file = getattr(description, "model_file", None)
        if model_dir is None or not isinstance(model_file, str):
            raise BackendUnavailableError(
                "FastEmbed did not expose a quantizable ONNX model directory; "
                "use the supported fastembed version from pyproject.toml"
            )
        return Path(model_dir), model_file

    def _quantized_model_dir(self, source_dir: Path, model_file: str) -> Path:
        source_model = source_dir / model_file
        if not source_model.is_file():
            raise FileNotFoundError(f"ONNX source model does not exist: {source_model}")
        stat = source_model.stat()
        cache_key = hashlib.sha256(
            f"{source_model.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()
        ).hexdigest()[:16]
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.model_name).strip("-")
        destination = self.config.cache_dir / "_aether_int8" / f"{safe_name}-{cache_key}"
        target_model = destination / model_file
        if target_model.is_file():
            self._model_file = target_model
            return destination

        try:
            from onnxruntime.quantization import QuantType, quantize_dynamic
        except ImportError as exc:
            raise BackendUnavailableError(
                "ONNX INT8 quantization needs the optional 'onnx' package; "
                "install with: pip install -e '.[embedding-int8]'"
            ) from exc

        destination.mkdir(parents=True, exist_ok=True)
        for source in source_dir.rglob("*"):
            if not source.is_file() or source == source_model:
                continue
            relative = source.relative_to(source_dir)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)

        target_model.parent.mkdir(parents=True, exist_ok=True)
        temporary = target_model.with_name(f".{target_model.name}.{uuid.uuid4().hex}.tmp")
        try:
            quantize_dynamic(
                model_input=str(source_model),
                model_output=str(temporary),
                weight_type=QuantType.QInt8,
            )
            os.replace(temporary, target_model)
        finally:
            temporary.unlink(missing_ok=True)
        self._model_file = target_model
        return destination

    def load(self) -> None:
        if self._model is not None:
            return
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        specific_path = self.config.model_path
        if self.config.precision == "int8":
            bootstrap = self._new_model(specific_model_path=specific_path, lazy_load=True)
            source_dir, model_file = self._fastembed_model_location(bootstrap)
            specific_path = self._quantized_model_dir(source_dir, model_file)
        self._model = self._new_model(specific_model_path=specific_path)
        model_dir, model_file = self._fastembed_model_location(self._model)
        self._model_file = model_dir / model_file
        warmup_texts = ["Recall embedding warmup"] * self.config.model_batch_size
        warmup = next(
            iter(self._model.passage_embed(warmup_texts, batch_size=self.config.model_batch_size))
        )
        self.dimension = int(np.asarray(warmup).size)
        if self._model_file.is_file():
            self.model_hash = _file_hash(self._model_file)

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
            runtime_version = None
        else:
            providers = [str(provider) for provider in ort.get_available_providers()]
            runtime_version = ort.__version__
        return {
            "backend_key": self.backend_key,
            "engine": self.engine_name,
            "provider": "CPUExecutionProvider",
            "device": self.device,
            "precision": self.precision,
            "quantization_mode": (
                "onnxruntime-dynamic-qint8" if self.config.precision == "int8" else "none"
            ),
            "model_hash": self.model_hash,
            "model_file": str(self._model_file) if self._model_file is not None else None,
            "available_providers": providers,
            "onnxruntime_version": runtime_version,
            "graph_optimization_level": "ORT_ENABLE_ALL",
            "intra_op_threads": self.config.threads,
            "inter_op_threads": self.config.threads,
            "simd_policy": (
                "onnxruntime-runtime-dispatch" if self.config.enable_simd else "runtime-baseline"
            ),
        }


class OpenVinoBackend:
    backend_key = "openvino"
    engine_name = "optimum-intel-openvino"
    device = "CPU"

    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self.model_name = config.model_name
        self.dimension: int | None = None
        self.precision = config.precision.upper()
        self.model_hash: str | None = None
        self.int8_weight_constants: int | None = None
        self._model: Any = None
        self._tokenizer: Any = None
        self._compiled_model: Any = None
        self._async_enabled = False
        self._async_error: str | None = None
        self._optimal_infer_requests: int | None = None
        self._actual_num_streams: Any = None
        self._async_lock = threading.Lock()

    @staticmethod
    def _count_int8_weight_constants(model_wrapper: Any) -> int:
        model = getattr(model_wrapper, "model", None)
        get_ops = getattr(model, "get_ops", None)
        if model is None or not callable(get_ops):
            raise BackendUnavailableError("OpenVINO model graph is unavailable for INT8 validation")
        count = 0
        for operation in get_ops():
            if operation.get_type_name() != "Constant":
                continue
            for output_index in range(operation.get_output_size()):
                element_type = operation.get_output_element_type(output_index).get_type_name()
                if element_type in {"i8", "u8"}:
                    count += 1
        return count

    def load(self) -> None:
        if self._model is not None:
            return
        if not _module_available("openvino") or not _module_available("optimum.intel.openvino"):
            raise BackendUnavailableError(
                "OpenVINO runtime is missing; install: pip install -e '.[embedding-openvino]'"
            )
        from optimum.intel.openvino import OVModelForFeatureExtraction
        from transformers import AutoTokenizer

        source = _model_source(self.config)
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        source_path = Path(source)
        is_openvino_ir = source_path.is_dir() and any(source_path.glob("*.xml"))
        source_requires_export = not is_openvino_ir
        exported_cache = (
            self.config.cache_dir
            / "_aether_openvino"
            / (
                f"{re.sub(r'[^A-Za-z0-9_.-]+', '-', self.model_name).strip('-')}"
                f"-{_identifier_hash(source)}-{self.config.precision}"
            )
        )
        export_marker = exported_cache / ".aether-export-complete"
        loaded_from_sidecar_cache = False
        if (
            export_marker.is_file()
            and any(exported_cache.glob("*.xml"))
            and any(exported_cache.glob("*.bin"))
        ):
            source = str(exported_cache)
            source_path = exported_cache
            is_openvino_ir = True
            loaded_from_sidecar_cache = True
        ov_config = self._openvino_runtime_config()
        model_kwargs: dict[str, Any] = {
            "cache_dir": str(self.config.cache_dir),
            "device": "CPU",
            "export": not is_openvino_ir,
            "ov_config": ov_config,
        }
        # Existing OpenVINO IR directories are treated as immutable artifacts:
        # load them as-is, then verify INT8 weights below. Re-applying
        # quantization to a pre-compressed acceptance IR fails in Optimum Intel.
        if self.config.precision == "int8" and not is_openvino_ir and not loaded_from_sidecar_cache:
            from optimum.intel.openvino.configuration import OVWeightQuantizationConfig

            model_kwargs["quantization_config"] = OVWeightQuantizationConfig(
                bits=8,
                dtype="int8",
            )
        self._tokenizer = AutoTokenizer.from_pretrained(
            source,
            cache_dir=str(self.config.cache_dir),
        )
        self._model = OVModelForFeatureExtraction.from_pretrained(source, **model_kwargs)
        if self.config.precision == "int8":
            self.int8_weight_constants = self._count_int8_weight_constants(self._model)
            if self.int8_weight_constants <= 0:
                raise BackendUnavailableError(
                    "OpenVINO INT8 compression completed without INT8 weight constants"
                )
        should_cache_model = (
            not loaded_from_sidecar_cache
            and not is_openvino_ir
            and (source_requires_export or self.config.precision == "int8")
        )
        if should_cache_model:
            exported_cache.mkdir(parents=True, exist_ok=True)
            self._model.save_pretrained(exported_cache)
            self._tokenizer.save_pretrained(exported_cache)
            export_marker.write_text("ready\n", encoding="ascii")
            source_path = exported_cache
        if self.config.openvino_async:
            self._configure_async_runtime(ov_config)
        warmup_size = self.config.model_batch_size
        warmup = self.embed(
            ["Recall embedding warmup"] * warmup_size,
            ["passage"] * warmup_size,
            warmup_size,
        )[0]
        self.dimension = int(warmup.size)
        if source_path.is_dir():
            model_files = [*source_path.glob("*.xml"), *source_path.glob("*.bin")]
            self.model_hash = _files_hash(model_files) if model_files else _identifier_hash(source)
        else:
            self.model_hash = _identifier_hash(source)

    def _openvino_runtime_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "PERFORMANCE_HINT": "THROUGHPUT",
            "INFERENCE_NUM_THREADS": self.config.threads,
        }
        if self.config.openvino_num_streams.strip():
            config["NUM_STREAMS"] = self.config.openvino_num_streams.strip()
        return config

    def _configure_async_runtime(self, ov_config: dict[str, Any]) -> None:
        if self._model is None:
            return
        try:
            import openvino as ov

            model = getattr(self._model, "model", None)
            if model is None:
                raise BackendUnavailableError("OpenVINO model graph is unavailable")
            core = ov.Core()
            try:
                compiled = core.compile_model(model, "CPU", ov_config)
            except Exception:
                if "NUM_STREAMS" not in ov_config:
                    raise
                fallback_config = dict(ov_config)
                fallback_config.pop("NUM_STREAMS", None)
                compiled = core.compile_model(model, "CPU", fallback_config)
                self._actual_num_streams = "unsupported-by-runtime"
            self._compiled_model = compiled
            self._optimal_infer_requests = self._compiled_property_int(
                "OPTIMAL_NUMBER_OF_INFER_REQUESTS"
            )
            if self._actual_num_streams is None:
                self._actual_num_streams = self._compiled_property("NUM_STREAMS")
            self._async_enabled = True
            self._async_error = None
        except Exception as exc:
            self._compiled_model = None
            self._async_enabled = False
            self._async_error = f"{type(exc).__name__}: {exc}"[:1000]

    def _compiled_property(self, name: str) -> Any:
        if self._compiled_model is None:
            return None
        try:
            return self._compiled_model.get_property(name)
        except Exception:
            return None

    def _compiled_property_int(self, name: str) -> int | None:
        value = self._compiled_property(name)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _hidden_to_vectors(hidden: np.ndarray) -> list[np.ndarray]:
        values = np.asarray(hidden, dtype=np.float32)
        if values.ndim == 3:
            return [np.asarray(row, dtype=np.float32) for row in values[:, 0, :]]
        if values.ndim == 2:
            return [np.asarray(row, dtype=np.float32) for row in values]
        raise ValueError(f"unexpected OpenVINO embedding output shape: {values.shape}")

    def _sync_infer_batch(self, encoded: Any) -> list[np.ndarray]:
        if self._model is None:
            raise RuntimeError("OpenVINO model is not loaded")
        output = self._model(**dict(encoded))
        hidden = np.asarray(output.last_hidden_state, dtype=np.float32)
        return self._hidden_to_vectors(hidden)

    @staticmethod
    def _request_output_array(request: Any) -> np.ndarray:
        output_tensors = getattr(request, "output_tensors", None)
        tensor = output_tensors[0] if output_tensors else request.get_output_tensor(0)
        data = getattr(tensor, "data", tensor)
        return np.asarray(data, dtype=np.float32).copy()

    def _async_infer_batches(self, encoded_batches: list[Any]) -> list[np.ndarray]:
        if self._compiled_model is None:
            raise RuntimeError("OpenVINO async compiled model is not available")
        import openvino as ov

        outputs: dict[int, np.ndarray] = {}
        errors: dict[int, BaseException] = {}

        def callback(request: Any, userdata: Any) -> None:
            index = int(userdata)
            try:
                outputs[index] = self._request_output_array(request)
            except BaseException as exc:
                errors[index] = exc

        with self._async_lock:
            queue = ov.AsyncInferQueue(
                self._compiled_model,
                self.config.openvino_infer_requests,
            )
            queue.set_callback(callback)
            for index, encoded in enumerate(encoded_batches):
                queue.start_async(dict(encoded), userdata=index, share_inputs=False)
            queue.wait_all()
        if errors:
            raise next(iter(errors.values()))
        vectors: list[np.ndarray] = []
        for index in range(len(encoded_batches)):
            if index not in outputs:
                raise RuntimeError("OpenVINO async inference did not return all outputs")
            vectors.extend(self._hidden_to_vectors(outputs[index]))
        return vectors

    def embed(
        self,
        texts: list[str],
        input_types: list[str],
        batch_size: int,
    ) -> list[np.ndarray]:
        if len(input_types) != len(texts):
            raise ValueError("input_types length must match texts length")
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("OpenVINO model is not loaded")
        encoded_batches: list[Any] = []
        for start, end in _batch_positions(len(texts), batch_size):
            encoded = self._tokenizer(
                texts[start:end],
                padding=True,
                truncation=True,
                max_length=self.config.max_length,
                return_tensors="np",
            )
            encoded_batches.append(encoded)
        if self._async_enabled:
            return self._async_infer_batches(encoded_batches)
        vectors: list[np.ndarray] = []
        for encoded in encoded_batches:
            vectors.extend(self._sync_infer_batch(encoded))
        return vectors

    def runtime_details(self) -> dict[str, Any]:
        return {
            "backend_key": self.backend_key,
            "engine": self.engine_name,
            "provider": "OpenVINO CPU plugin",
            "device": self.device,
            "precision": self.precision,
            "quantization_mode": (
                "openvino-nncf-int8-weight-only" if self.config.precision == "int8" else "none"
            ),
            "int8_weight_constants": self.int8_weight_constants,
            "quantization_verified": (
                bool(self.int8_weight_constants) if self.config.precision == "int8" else True
            ),
            "model_hash": self.model_hash,
            "openvino_version": _package_version("openvino"),
            "optimum_intel_version": _package_version("optimum-intel"),
            "inference_threads": self.config.threads,
            "inference_mode": "openvino_async" if self._async_enabled else "sync",
            "async_inference": self._async_enabled,
            "async_error": self._async_error,
            "num_streams": self._actual_num_streams,
            "requested_num_streams": self.config.openvino_num_streams,
            "infer_requests": self.config.openvino_infer_requests,
            "optimal_number_of_infer_requests": self._optimal_infer_requests,
            "performance_hint": "THROUGHPUT",
            "query_passage_semantics": (
                "input_types are preserved by the sidecar and dynamic scheduler; "
                "this OpenVINO path currently uses the base tokenizer/model without "
                "query/passage prompt differentiation"
            ),
            "simd_policy": "openvino-onednn-runtime-dispatch",
        }


class IpexBackend:
    backend_key = "ipex"
    engine_name = "pytorch-ipex-cpu"
    device = "CPU"

    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self.model_name = config.model_name
        self.dimension: int | None = None
        self.precision = config.precision.upper()
        self.model_hash: str | None = None
        self._model: Any = None
        self._tokenizer: Any = None
        self._torch: Any = None
        self._optimizer = "unloaded"

    def load(self) -> None:
        if self._model is not None:
            return
        if self.config.precision != "fp32":
            raise BackendUnavailableError(
                "IPEX INT8 needs a calibrated static-quantized model; falling back to ONNX INT8"
            )
        if not _module_available("intel_extension_for_pytorch"):
            raise BackendUnavailableError(
                "Intel Extension for PyTorch is not installed or unsupported on this platform; "
                "install a PyTorch-version-matched IPEX wheel"
            )
        torch = import_module("torch")
        ipex = import_module("intel_extension_for_pytorch")
        transformers = import_module("transformers")
        source = _model_source(self.config)
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        torch.set_num_threads(self.config.threads)
        if hasattr(torch, "set_num_interop_threads"):
            with suppress(RuntimeError):
                torch.set_num_interop_threads(self.config.inter_op_threads)
        self._tokenizer = transformers.AutoTokenizer.from_pretrained(
            source,
            cache_dir=str(self.config.cache_dir),
        )
        model = transformers.AutoModel.from_pretrained(
            source,
            cache_dir=str(self.config.cache_dir),
        ).eval()
        optimize_transformers = getattr(ipex, "optimize_transformers", None)
        if optimize_transformers is not None:
            try:
                model = optimize_transformers(model, dtype=torch.float32, inplace=True)
                self._optimizer = "ipex.optimize_transformers"
            except Exception:
                model = ipex.optimize(model, dtype=torch.float32, inplace=True)
                self._optimizer = "ipex.optimize"
        else:
            model = ipex.optimize(model, dtype=torch.float32, inplace=True)
            self._optimizer = "ipex.optimize"
        self._torch = torch
        self._model = model
        warmup_size = self.config.model_batch_size
        warmup = self.embed(
            ["Recall embedding warmup"] * warmup_size,
            ["passage"] * warmup_size,
            warmup_size,
        )[0]
        self.dimension = int(warmup.size)
        source_path = Path(source)
        # A path/name hash is not evidence of the weights that performed inference.
        # Native binding requires local weight artifacts for this backend.
        weight_files = (
            [*source_path.glob("*.safetensors"), *source_path.glob("pytorch_model*.bin")]
            if source_path.is_dir()
            else []
        )
        self.model_hash = _files_hash(weight_files) if weight_files else None

    def embed(
        self,
        texts: list[str],
        input_types: list[str],
        batch_size: int,
    ) -> list[np.ndarray]:
        del input_types
        if self._model is None or self._tokenizer is None or self._torch is None:
            raise RuntimeError("IPEX model is not loaded")
        vectors: list[np.ndarray] = []
        for start, end in _batch_positions(len(texts), batch_size):
            encoded = self._tokenizer(
                texts[start:end],
                padding=True,
                truncation=True,
                max_length=self.config.max_length,
                return_tensors="pt",
            )
            with self._torch.inference_mode():
                output = self._model(**encoded)
            hidden = output.last_hidden_state[:, 0, :]
            values = hidden.detach().float().cpu().numpy().astype(np.float32, copy=False)
            vectors.extend(values)
        return vectors

    def runtime_details(self) -> dict[str, Any]:
        return {
            "backend_key": self.backend_key,
            "engine": self.engine_name,
            "provider": "IPEXCPU",
            "device": self.device,
            "precision": self.precision,
            "model_hash": self.model_hash,
            "torch_version": _package_version("torch"),
            "ipex_version": _package_version("intel-extension-for-pytorch"),
            "optimizer": self._optimizer,
            "intra_op_threads": self.config.threads,
            "inter_op_threads": self.config.inter_op_threads,
            "simd_policy": "ipex-onednn-runtime-dispatch",
        }


BackendFactory = Callable[[BackendConfig], SidecarEmbeddingBackend]
_BACKEND_FACTORIES: dict[str, BackendFactory] = {
    "onnx": FastEmbedOnnxBackend,
    "openvino": OpenVinoBackend,
    "ipex": IpexBackend,
}


def register_backend(key: str, factory: BackendFactory, descriptor: BackendDescriptor) -> None:
    """Register an out-of-tree backend without changing Sidecar request processing."""

    normalized = key.strip().lower()
    if not normalized:
        raise ValueError("backend key must not be empty")
    _BACKEND_FACTORIES[normalized] = factory
    BACKEND_DESCRIPTORS[normalized] = descriptor


def create_backend(key: str, config: BackendConfig) -> SidecarEmbeddingBackend:
    """Create one exact backend; loading and runtime fallback are handled by the chain."""

    normalized = key.strip().lower()
    if normalized == "onnx-int8":
        normalized = "onnx"
        config = replace(config, precision="int8")
    factory = _BACKEND_FACTORIES.get(normalized)
    descriptor = BACKEND_DESCRIPTORS.get(normalized)
    if factory is None or descriptor is None:
        supported = ", ".join(sorted(BACKEND_DESCRIPTORS))
        raise BackendUnavailableError(
            f"unknown backend '{normalized}'; configured keys: {supported}"
        )
    if normalized in {"openvino", "ipex"} and not _module_available(descriptor.runtime_package):
        raise BackendUnavailableError(
            f"backend '{normalized}' adapter is implemented but its optional runtime is "
            f"unavailable on this environment: {descriptor.install_hint}"
        )
    if config.precision not in descriptor.supported_precisions:
        supported_precisions = ", ".join(descriptor.supported_precisions)
        raise BackendUnavailableError(
            f"backend '{normalized}' does not support {config.precision}; "
            f"supported precision: {supported_precisions}"
        )
    return factory(config)


class FallbackEmbeddingBackend:
    """Select the first usable backend and fail over once a runtime becomes unhealthy."""

    def __init__(
        self,
        requested_backend: str,
        config: BackendConfig,
        fallback_backends: tuple[str, ...] = ("onnx",),
        allow_fallback: bool = True,
    ) -> None:
        normalized_backend = requested_backend.strip().lower()
        if normalized_backend == "onnx-int8":
            normalized_backend = "onnx"
            config = replace(config, precision="int8")
        self.requested_backend = normalized_backend
        self.config = config
        self.allow_fallback = allow_fallback
        self._lock = threading.RLock()
        self._active: SidecarEmbeddingBackend | None = None
        self._active_index = -1
        self._history: list[dict[str, str]] = []
        self._expected_dimension: int | None = None
        self._candidate_specs = self._build_candidates(fallback_backends)

    def _build_candidates(self, fallback_backends: tuple[str, ...]) -> list[tuple[str, Precision]]:
        requested = self.requested_backend
        requested_keys = ("openvino", "ipex", "onnx") if requested == "auto" else (requested,)
        candidates: list[tuple[str, Precision]] = [
            (key, self.config.precision) for key in requested_keys
        ]
        if self.allow_fallback:
            candidates.extend(
                (key.strip().lower(), self.config.precision) for key in fallback_backends
            )
            if self.config.precision == "int8":
                candidates.append(("onnx", "fp32"))
        unique: list[tuple[str, Precision]] = []
        for candidate in candidates:
            if candidate[0] and candidate not in unique:
                unique.append(candidate)
        return unique

    @property
    def backend_key(self) -> str:
        return self._active.backend_key if self._active is not None else self.requested_backend

    @property
    def engine_name(self) -> str:
        return self._active.engine_name if self._active is not None else "unavailable"

    @property
    def model_name(self) -> str:
        return self._active.model_name if self._active is not None else self.config.model_name

    @property
    def dimension(self) -> int | None:
        return self._active.dimension if self._active is not None else None

    @dimension.setter
    def dimension(self, value: int | None) -> None:
        if self._active is not None:
            self._active.dimension = value

    @property
    def precision(self) -> str:
        return (
            str(getattr(self._active, "precision", "FP32"))
            if self._active is not None
            else self.config.precision.upper()
        )

    @property
    def device(self) -> str:
        return str(getattr(self._active, "device", "CPU")) if self._active is not None else "CPU"

    @property
    def model_hash(self) -> str | None:
        return getattr(self._active, "model_hash", None) if self._active is not None else None

    @property
    def fallback_used(self) -> bool:
        if self._active_index < 0:
            return False
        first_key, first_precision = self._candidate_specs[0]
        return self.backend_key != first_key or self.precision.lower() != first_precision

    def _activate(self, start_index: int, phase: str) -> None:
        errors: list[str] = []
        for index in range(start_index, len(self._candidate_specs)):
            key, precision = self._candidate_specs[index]
            label = f"{key}:{precision}"
            try:
                backend = create_backend(key, replace(self.config, precision=precision))
                backend.load()
                if (
                    self._expected_dimension is not None
                    and backend.dimension != self._expected_dimension
                ):
                    raise ValueError(
                        f"fallback dimension {backend.dimension} does not match "
                        f"{self._expected_dimension}"
                    )
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"[:1000]
                self._history.append(
                    {"candidate": label, "phase": phase, "status": "failed", "error": message}
                )
                errors.append(f"{label} -> {message}")
                continue
            self._active = backend
            self._active_index = index
            if self._expected_dimension is None:
                self._expected_dimension = backend.dimension
            self._history.append(
                {"candidate": label, "phase": phase, "status": "selected", "error": ""}
            )
            return
        detail = "; ".join(errors) or "fallback chain is empty"
        raise BackendUnavailableError(f"no usable embedding backend: {detail}")

    def load(self) -> None:
        with self._lock:
            if self._active is None:
                self._activate(0, "load")

    def embed(
        self,
        texts: list[str],
        input_types: list[str],
        batch_size: int,
    ) -> list[np.ndarray]:
        if self._active is None:
            raise RuntimeError("embedding backend chain is not loaded")
        while True:
            active = self._active
            if active is None:
                raise BackendUnavailableError("runtime fallback did not select a backend")
            try:
                return active.embed(texts, input_types, batch_size)
            except Exception as primary_error:
                if not self.allow_fallback:
                    raise
                with self._lock:
                    if self._active is active:
                        self._history.append(
                            {
                                "candidate": (
                                    f"{active.backend_key}:"
                                    f"{str(getattr(active, 'precision', 'FP32')).lower()}"
                                ),
                                "phase": "inference",
                                "status": "failed",
                                "error": f"{type(primary_error).__name__}: {primary_error}"[:1000],
                            }
                        )
                        self._activate(self._active_index + 1, "runtime-fallback")
                # Retry the newly selected backend. If it also fails, the loop
                # advances through the rest of the chain in this same request.

    def runtime_details(self) -> dict[str, Any]:
        current = self._active.runtime_details() if self._active is not None else {}
        return {
            **current,
            "requested_backend": self.requested_backend,
            "actual_backend": self.backend_key if self._active is not None else None,
            "requested_precision": self.config.precision.upper(),
            "actual_precision": self.precision if self._active is not None else None,
            "fallback_enabled": self.allow_fallback,
            "fallback_used": self.fallback_used,
            "fallback_chain": [f"{key}:{precision}" for key, precision in self._candidate_specs],
            "fallback_history": list(self._history),
        }


def create_backend_chain(
    key: str,
    config: BackendConfig,
    fallback_backends: tuple[str, ...] = ("onnx",),
    allow_fallback: bool = True,
) -> FallbackEmbeddingBackend:
    return FallbackEmbeddingBackend(key, config, fallback_backends, allow_fallback)


def describe_backends(selected: str, actual: str | None = None) -> dict[str, Any]:
    return {
        "selected": selected,
        "actual": actual,
        "strategies": [
            {
                **descriptor.as_dict(),
                "selected": key == selected,
                "active": key == actual,
            }
            for key, descriptor in BACKEND_DESCRIPTORS.items()
        ],
        "replacement_contract": (
            "Implement SidecarEmbeddingBackend and call register_backend(); "
            "the HTTP/service layer remains unchanged."
        ),
    }
