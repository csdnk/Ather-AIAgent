"""Best-effort CPU and inference-runtime capability discovery for B1."""

from __future__ import annotations

import importlib
import os
import platform
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from typing import Any

SIMD_KEYS = (
    "SSE2",
    "AVX",
    "FMA3",
    "AVX2",
    "AVX512F",
    "AVX512_VNNI",
    "AVX512_BF16",
    "AMX_TILE",
    "AMX_INT8",
    "AMX_BF16",
)


def _numpy_cpu_features() -> tuple[dict[str, bool], str]:
    try:
        module = importlib.import_module("numpy._core._multiarray_umath")
        cpu_features = vars(module)["__cpu_features__"]
    except (ImportError, KeyError):
        return {}, "unavailable"
    raw = {str(key).upper(): bool(value) for key, value in cpu_features.items()}
    return {key: raw.get(key, False) for key in SIMD_KEYS}, "numpy-runtime-dispatch"


def _onnxruntime_providers() -> list[str]:
    try:
        import onnxruntime as ort
    except ImportError:
        return []
    return [str(provider) for provider in ort.get_available_providers()]


def _simd_tier(features: dict[str, bool]) -> str:
    if any(features.get(name, False) for name in ("AMX_TILE", "AMX_INT8", "AMX_BF16")):
        return "amx"
    if features.get("AVX512F", False):
        return "avx512"
    if features.get("AVX2", False):
        return "avx2"
    if features.get("AVX", False):
        return "avx"
    if features.get("SSE2", False):
        return "sse2"
    return "scalar-or-unknown"


def _validated_simd_tier(features: dict[str, bool], enabled: bool) -> str:
    if not enabled:
        return "disabled"
    if features.get("AVX2", False):
        return "avx2"
    if features.get("AVX", False):
        return "avx"
    if features.get("SSE2", False):
        return "sse2"
    return "portable-baseline"


def _runtime_versions() -> dict[str, str | None]:
    packages = {
        "numpy": "numpy",
        "onnxruntime": "onnxruntime",
        "openvino": "openvino",
        "torch": "torch",
        "ipex": "intel-extension-for-pytorch",
    }
    result: dict[str, str | None] = {}
    for key, package in packages.items():
        try:
            result[key] = version(package)
        except PackageNotFoundError:
            result[key] = None
    return result


def _runtime_availability() -> dict[str, bool]:
    result: dict[str, bool] = {}
    for name in ("onnxruntime", "openvino", "torch", "intel_extension_for_pytorch"):
        try:
            result[name] = find_spec(name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            result[name] = False
    return result


def detect_runtime_capabilities(simd_enabled: bool = True) -> dict[str, Any]:
    """Return observable capabilities without promising a specific executed kernel."""

    features, source = _numpy_cpu_features()
    highest_tier = _simd_tier(features)
    validated_tier = _validated_simd_tier(features, simd_enabled)
    return {
        "cpu": os.getenv("PROCESSOR_IDENTIFIER") or platform.processor() or "unknown",
        "architecture": platform.machine(),
        "logical_cpu_count": os.cpu_count(),
        "platform": platform.platform(),
        "simd_detection_source": source,
        "simd_features": features,
        "highest_detected_simd_tier": highest_tier,
        "validated_simd_tier": validated_tier,
        "active_simd_policy": (
            f"numpy-and-inference-runtime-auto-dispatch;validated-tier:{validated_tier}"
            if simd_enabled
            else "sidecar-simd-optimization-disabled;inference-runtime-managed"
        ),
        "simd_enabled": simd_enabled,
        "explicit_avx512_kernel_enabled": False,
        "explicit_amx_kernel_enabled": False,
        "framework_dispatch_ceiling_enforced": False,
        "onnxruntime_available_providers": _onnxruntime_providers(),
        "runtime_available": _runtime_availability(),
        "runtime_versions": _runtime_versions(),
        "notes": [
            (
                "Detected means the runtime reports support; it does not prove every model "
                "operator used it."
            ),
            (
                "The production backends delegate model-kernel selection to ONNX Runtime, "
                "OpenVINO/oneDNN, or IPEX/oneDNN."
            ),
            (
                "Phase-2 acceptance covers vectorized NumPy normalization and runtime dispatch "
                "through AVX2; explicit AVX-512 and AMX kernels are not implemented. Framework "
                "runtimes may independently select a higher ISA when the host supports it."
            ),
        ],
    }
