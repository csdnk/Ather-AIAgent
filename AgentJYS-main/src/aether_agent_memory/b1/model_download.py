"""Download and verify the configured B1 embedding model."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from aether_agent_memory.recall.embedding.backends import BackendConfig, FastEmbedOnnxBackend

MODEL_NAME = "BAAI/bge-small-zh-v1.5"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download the B1 ONNX model and verify it with a real warm-up inference."
    )
    parser.add_argument(
        "--model-name",
        default=os.getenv("AETHER_B1_MODEL_NAME", MODEL_NAME),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(
            os.getenv("AETHER_B1_CACHE_DIR", str(Path.cwd() / ".aether" / "b1" / "models"))
        ),
    )
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args()
    if args.threads <= 0:
        parser.error("--threads must be positive")

    backend = FastEmbedOnnxBackend(
        BackendConfig(
            model_name=args.model_name,
            cache_dir=args.cache_dir,
            model_path=None,
            threads=args.threads,
        )
    )
    backend.load()
    files = [path for path in args.cache_dir.rglob("*") if path.is_file()]
    result = {
        "status": "downloaded_and_verified",
        "model": backend.model_name,
        "dimension": backend.dimension,
        "cache_dir": str(args.cache_dir.resolve()),
        "file_count": len(files),
        "size_bytes": sum(path.stat().st_size for path in files),
        "runtime": backend.runtime_details(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
