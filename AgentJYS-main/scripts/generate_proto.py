"""Generate Python gRPC stubs from the Rust engine's canonical proto file."""

from __future__ import annotations

import sys
from pathlib import Path
from subprocess import run

ROOT = Path(__file__).resolve().parents[1]
PROTO_DIR = ROOT / "engine" / "proto"
PROTO = PROTO_DIR / "aether_engine.proto"
OUTPUT = ROOT / "src" / "aether_agent_memory" / "p2" / "generated"


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "__init__.py").touch()
    result = run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"-I{PROTO_DIR}",
            f"--python_out={OUTPUT}",
            f"--grpc_python_out={OUTPUT}",
            str(PROTO),
        ],
        check=False,
    )
    if result.returncode != 0:
        return result.returncode
    grpc_module = OUTPUT / "aether_engine_pb2_grpc.py"
    text = grpc_module.read_text(encoding="utf-8")
    grpc_module.write_text(
        text.replace(
            "import aether_engine_pb2 as aether__engine__pb2",
            "from . import aether_engine_pb2 as aether__engine__pb2",
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
