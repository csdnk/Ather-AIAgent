from __future__ import annotations

import argparse
import contextlib
import ctypes
import io
import json
import os
import platform
import statistics
import time
from pathlib import Path

for variable in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[variable] = "1"

import numpy as np  # noqa: E402 - thread limits must be set before NumPy loads BLAS
from threadpoolctl import threadpool_info  # noqa: E402


def pin_to_cpu_zero() -> bool:
    if os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    kernel32.SetProcessAffinityMask.restype = ctypes.c_int
    process = kernel32.GetCurrentProcess()
    return bool(kernel32.SetProcessAffinityMask(process, 1))


def matrix(size: int) -> np.ndarray:
    row = np.arange(size, dtype=np.uint64)[:, None]
    column = np.arange(size, dtype=np.uint64)[None, :]
    values = ((row * 1315423911 + column * 2654435761) % 1000).astype(np.float32)
    return values / np.float32(1000.0) - np.float32(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Single-core NumPy float32 matrix benchmark")
    parser.add_argument("--sizes", nargs="+", type=int, default=[128, 256, 512, 1024])
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repetitions <= 0 or any(size <= 0 for size in args.sizes):
        parser.error("sizes and repetitions must be positive")

    affinity = pin_to_cpu_zero()
    runtime_output = io.StringIO()
    try:
        with contextlib.redirect_stdout(runtime_output):
            np.show_runtime()
    except Exception:
        pass

    records = []
    for size in args.sizes:
        left = matrix(size)
        right = matrix(size).T.copy()
        _ = left @ right
        times = []
        output = None
        for _ in range(args.repetitions):
            started = time.perf_counter()
            output = left @ right
            times.append(time.perf_counter() - started)
        assert output is not None and np.isfinite(output).all()
        median = statistics.median(times)
        records.append(
            {
                "implementation": "numpy_matmul",
                "size": size,
                "dtype": "float32",
                "repetitions": args.repetitions,
                "median_ms": median * 1000.0,
                "min_ms": min(times) * 1000.0,
                "gflops_median": (2.0 * size**3) / median / 1e9,
                "checksum": float(np.sum(output, dtype=np.float64)),
                "finite": True,
            }
        )

    pools = threadpool_info()
    result = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "affinity_cpu0": affinity,
        "thread_env": {key: os.environ[key] for key in os.environ if key.endswith("NUM_THREADS")},
        "thread_pools": pools,
        "single_thread_verified": bool(pools)
        and all(int(pool.get("num_threads", 0)) == 1 for pool in pools),
        "runtime": runtime_output.getvalue(),
        "results": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(result, indent=2, default=str)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
