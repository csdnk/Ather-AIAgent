"""Run the complete local P3 service against an explicitly configured Temporal server."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aether_agent_memory.remember.contracts.models import DeleteRequest  # noqa: E402
from aether_agent_memory.runtime.flows.application import Service  # noqa: E402
from aether_agent_memory.runtime.flows.cli import initialize  # noqa: E402
from aether_agent_memory.runtime.flows.config import ServiceConfiguration  # noqa: E402
from aether_agent_memory.runtime.foundation.common import now  # noqa: E402


async def demo(
    directory,
    embedding_profile="native",
    embedding_config=None,
    recall_config=None,
    temporal_endpoint="127.0.0.1:7233",
):
    config_path = initialize(
        directory,
        embedding_profile=embedding_profile,
        model=None,
        endpoint="http://127.0.0.1:11434/v1",
        temporal_endpoint=temporal_endpoint,
    )
    config = ServiceConfiguration.load(config_path)
    config = config.model_copy(
        update={
            "embedding_config": embedding_config or config.embedding_config,
            "recall_config": recall_config or config.recall_config,
        }
    )
    service = Service(config)
    token = (directory / "credential").read_text(encoding="utf-8")
    execution = service.execution
    contexts = {}

    def context(operation):
        return service.runtime.foundation.identity.context(
            token, timeout_seconds=120, operation_id=operation
        )

    async def command(operation, kind, payload):
        ctx = contexts[operation] = context(operation)
        job = execution.accept(ctx, kind, payload)
        return job, await execution.await_result(ctx, job.job_id, 120)

    source = {
        "kind": "conversation",
        "external_id": "demo",
        "external_version": "1",
        "occurred_at": now(),
    }
    try:
        await execution.start()
        execution.require_ready()
        saved_job, saved = await command(
            "save",
            "remember.save",
            {
                "selection": {"session_id": "demo-session"},
                "source": source,
                "content": {"kind": "text", "text": "我喜欢无糖咖啡。"},
            },
        )
        await execution.drain(120)
        recall_payload = {
            "query": "喜欢什么饮料",
            "selection": {"session_id": "demo-session"},
            "sources": "working",
            "token_budget": 1024,
        }
        recalled_job, recalled = await command("recall", "recall.execute", recall_payload)
        memory_id = saved["memories"][0]["memory_id"]
        current = service.runtime.remember.get(context("inspect"), memory_id)
        corrected_job, corrected = await command(
            "correct",
            "remember.correct",
            {
                "memory_id": memory_id,
                "request": {
                    "expected_version": current.ref.version,
                    "content": "我现在喜欢无糖红茶。",
                    "reason": "更新偏好",
                    "source": {**source, "external_id": "correction"},
                },
            },
        )
        await execution.drain(120)
        after_job, after = await command("recall-after", "recall.execute", recall_payload)
        current = service.runtime.remember.get(context("inspect-after"), memory_id)
        service.runtime.remember.delete(
            context("delete"),
            memory_id,
            DeleteRequest(expected_revision=current.object_revision, reason="演示删除"),
        )
        await execution.drain(120)
        deleted_job, deleted = await command(
            "recall-deleted",
            "recall.execute",
            {
                **recall_payload,
                "sources": "both",
            },
        )
        passed = (
            "咖啡" in recalled["rendered_context"]
            and "红茶" in after["rendered_context"]
            and deleted["outcome"] == "empty"
        )
        with service.runtime.foundation.uow.transaction() as tx:
            actions = [row for _, row in tx.rows("operate_actions")]
        evidence = {
            "passed": passed,
            "production_acceptance": False,
            "backend": "temporal",
            "profile": embedding_profile,
            "trace": service.runtime.foundation.diagnostics.trace(
                context("trace"), contexts["recall"].trace_id, limit=500
            ),
            "retrieval": "milvus"
            if service.runtime.owned_vectors
            else "sqlite_" + embedding_profile,
            "reranker": getattr(service.runtime.recall.reranker, "identifier", None),
            "model_space": service.runtime.model_space,
            "job_ids": [
                job.job_id
                for job in (saved_job, recalled_job, corrected_job, after_job, deleted_job)
            ],
            "saved": saved,
            "first_context": recalled,
            "correction": corrected,
            "corrected_context": after,
            "after_delete": deleted,
            "actions": actions,
        }
        path = directory / "evidence.json"
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "passed": passed,
            "production_acceptance": False,
            "backend": "temporal",
            "evidence": str(path),
            "database": str(config.data_dir / "p3.db"),
        }
    finally:
        await service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--embedding-profile", choices=["native", "lexical"], default="native")
    parser.add_argument("--embedding-config", type=Path)
    parser.add_argument("--recall-config", type=Path)
    parser.add_argument("--temporal-endpoint", default="127.0.0.1:7233")
    args = parser.parse_args()
    result = asyncio.run(
        demo(
            args.directory.resolve(),
            args.embedding_profile,
            args.embedding_config,
            args.recall_config,
            args.temporal_endpoint,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
