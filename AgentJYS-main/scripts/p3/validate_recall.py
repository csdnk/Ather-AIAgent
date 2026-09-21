"""Opt-in real Embedding + CrossEncoder + tokenizer chain, with configurable Milvus.

Example: python scripts/p3/validate_recall.py --recall-config configs/recall.milvus.json
Use --report outside the source tree for raw machine evidence.
"""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from demo_flows import demo  # noqa: E402

from aether_agent_memory.recall.basic.config import RecallSettings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recall-config", required=True, type=Path)
    parser.add_argument("--embedding-config", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    settings = RecallSettings.model_validate_json(args.recall_config.read_text(encoding="utf-8"))
    if settings.rerank_policy != "required":
        parser.error("real-chain acceptance requires rerank_policy=required")
    with tempfile.TemporaryDirectory(prefix="p3-recall-live-") as directory:
        result = asyncio.run(
            demo(Path(directory), "native", args.embedding_config, args.recall_config)
        )
        evidence = json.loads((Path(directory) / "evidence.json").read_text(encoding="utf-8"))
    stages = evidence["trace"]["durable_facts"]["recall_stages"]
    assert result["passed"]
    assert any(s["stage"] == "rerank" and s["details"]["state"] == "succeeded" for s in stages)
    assert evidence["first_context"]["tokenizer_id"] != "utf8_bytes_v1"
    assert evidence["first_context"]["outcome"] == "available"
    if settings.milvus_uri:
        assert evidence["retrieval"] == "milvus"
    report = {
        "passed": True,
        "embedding": "real_native_cpu",
        "reranker": evidence["reranker"],
        "vector_backend": evidence["retrieval"],
        "milvus_verified": bool(settings.milvus_uri),
        "tokenizer": evidence["first_context"]["tokenizer_id"],
        "trace_verified": True,
        "scenario_evidence": evidence,
        "not_run": ["azure", "aks", "production_sla", "live_langmem"],
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "scenario_evidence"}, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
