"""Best-effort CPU and inference-runtime capability discovery for B1."""

from __future__ import annotations

import os
import platform
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
        from numpy._core._multiarray_umath import __cpu_features__  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        return {}, "unavailable"
    raw = {str(key).upper(): bool(value) for key, value in __cpu_features__.items()}
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


def detect_runtime_capabilities() -> dict[str, Any]:
    """Return observable capabilities without promising a specific executed kernel."""

    features, source = _numpy_cpu_features()
    return {
        "cpu": os.getenv("PROCESSOR_IDENTIFIER") or platform.processor() or "unknown",
        "architecture": platform.machine(),
        "logical_cpu_count": os.cpu_count(),
        "platform": platform.platform(),
        "simd_detection_source": source,
        "simd_features": features,
        "highest_detected_simd_tier": _simd_tier(features),
        "active_simd_policy": "runtime-auto-dispatch",
        "onnxruntime_available_providers": _onnxruntime_providers(),
        "notes": [
            (
                "Detected means the runtime reports support; it does not prove every model "
                "operator used it."
            ),
            (
                "The current production backend delegates kernel selection to ONNX Runtime "
                "and its CPU libraries."
            ),
        ],
    }
