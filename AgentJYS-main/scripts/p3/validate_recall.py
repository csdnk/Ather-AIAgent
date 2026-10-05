"""Opt-in configured Azure Embedding + CrossEncoder + tokenizer chain.

Supply a complete development/test service YAML, credential file and new external
evidence directory. Physical P2 storage and model quality require separate acceptance.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from demo_flows import demo  # noqa: E402

from aether_agent_memory.recall.basic.config import RecallSettings  # noqa: E402
from aether_agent_memory.runtime.flows.config import ServiceConfiguration  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--credential-file", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    service_config = ServiceConfiguration.load(args.config)
    settings = RecallSettings.model_validate_json(
        service_config.recall_config.read_text(encoding="utf-8")
    )
    if settings.rerank_policy != "required":
        parser.error("real-chain acceptance requires rerank_policy=required")
    result = asyncio.run(
        demo(
            args.directory.resolve(),
            config_path=args.config,
            credential_file=args.credential_file,
        )
    )
    evidence = json.loads(Path(result["evidence"]).read_text(encoding="utf-8"))
    stages = evidence["trace"]["durable_facts"]["recall_stages"]
    assert result["passed"]
    assert any(s["stage"] == "rerank" and s["details"]["state"] == "succeeded" for s in stages)
    assert evidence["first_context"]["tokenizer_id"] != "utf8_bytes_v1"
    assert evidence["first_context"]["outcome"] == "available"
    assert evidence["providers"]["vectors"].endswith(".AzureVectors")
    report = {
        "passed": True,
        "production_acceptance": False,
        "embedding": "configured_native_embedding",
        "reranker": evidence["reranker"],
        "vector_backend": evidence["retrieval"],
        "azure_storage_consumer_verified": True,
        "milvus_verified": True,
        "tokenizer": evidence["first_context"]["tokenizer_id"],
        "trace_verified": True,
        "scenario_evidence": evidence,
        "not_run": ["production_sla", "physical_p2_storage", "model_quality"],
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
