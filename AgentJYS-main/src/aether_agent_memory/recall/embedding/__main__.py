"""Inspect a real local deployment; print observed bindings without approving them."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from aether_agent_memory.recall.embedding.native import (
    NativeEmbeddingBackend,
    NativeEmbeddingSettings,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load and inspect a real Recall embedding backend")
    parser.add_argument(
        "--config", type=Path, help="NativeEmbeddingSettings JSON; defaults to ONNX"
    )
    args = parser.parse_args()
    settings = (
        NativeEmbeddingSettings.model_validate_json(args.config.read_text(encoding="utf-8"))
        if args.config
        else NativeEmbeddingSettings()
    )
    backend = NativeEmbeddingBackend(settings)
    try:
        print(
            json.dumps(
                {"status": "loaded", "approved": False, **backend.describe()},
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        asyncio.run(backend.close())


if __name__ == "__main__":
    main()
