"""Real native model + P3 identity/transactions/flows; replaces the mock-store smoke example."""

import argparse
import asyncio
import hashlib
import json
import math
import platform
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from demo_flows import demo  # noqa: E402

from aether_agent_memory.recall.contracts.models import EmbeddingRequest  # noqa: E402
from aether_agent_memory.runtime.flows.host import ThreeFlows  # noqa: E402


async def live(directory: Path, config: Path | None) -> dict:
    demonstration = await demo(directory, "native", config)
    assert demonstration["passed"]
    keys = json.loads((directory / "demo-credentials.json").read_text(encoding="utf-8"))
    app = ThreeFlows(directory / "p3.db", directory / "cache", embedding_config=config)
    try:
        ctx = app.foundation.identity.context(keys["alice"], timeout_seconds=60)
        vectors = []
        for usage in ("query", "passage"):
            result = await app.embedding.embed(
                ctx,
                EmbeddingRequest(
                    operation_id="native_equal_input",
                    usage=usage,
                    texts=("如何保存智能体的长期记忆",),
                    model_space=app.model_space,
                    deadline_at=ctx.deadline_at,
                ),
            )
            vector = result.items[0].vector
            norm = math.sqrt(sum(v * v for v in vector))
            assert result.dimensions == 512 and abs(norm - 1) < 1e-4
            vectors.append(vector)
        assert vectors[0] != vectors[1]
        health = await app.health.report(ctx)
        assert health["dependencies"]["embedding"]["state"] == "available"
        trace = app.foundation.diagnostics.trace(ctx, ctx.trace_id, limit=500)
        assert any(r["node"] == "embedding.native.embed" for r in trace["records"])
        with app.foundation.uow.transaction() as tx:
            attempts = [row for _, row in tx.rows("native_embedding_attempts")]
            native_evidence = tx.raw.scan("semantic-backend-evidence")
            assert native_evidence and all(r["state"] == "succeeded" for r in attempts)
            assert tx.raw.scan("semantic-execution") == []
            binding = tx.read("settings", "p3_embedding_binding")
        return {
            "passed": True,
            "mode": "real_native_cpu_with_p3_foundation",
            "model_binding": binding,
            "query_passage_differ": True,
            "dimensions": 512,
            "native_attempts": len(attempts),
            "backend_evidence_count": len(native_evidence),
            "native_health": "available",
            "trace_verified": True,
            "restart_binding_verified": True,
            "flow_demo": demonstration["passed"],
            "not_verified_by_this_runner": [
                "live_milvus",
                "cross_encoder_reranking",
                "live_langmem_model",
                "production_storage_tiers",
                "downstream_llm_tokenizer_alignment",
                "http_host",
                "azure_deployment",
            ],
        }
    finally:
        app.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="p3-native-") as tmp:
        tests = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                "tests/runtime/p3/pytest.ini",
                "tests/runtime/native",
                "-q",
                "-o",
                f"cache_dir={tmp}/pytest",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        print(tests.stdout)
        if tests.returncode:
            print(tests.stderr)
            return tests.returncode
        result = asyncio.run(live(Path(tmp), args.config))
    result.update(
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "adapter_tests_passed": True,
        }
    )
    result["artifacts"] = [
        {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for folder in (
            "src/aether_agent_memory/recall/embedding",
            "src/aether_agent_memory/runtime/flows",
            "src/aether_agent_memory/recall/basic",
        )
        for path in (ROOT / folder).glob("*.py")
    ]
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
