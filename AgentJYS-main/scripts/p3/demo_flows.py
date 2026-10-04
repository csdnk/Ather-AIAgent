"""Run save, recall, correction and deletion against configured P2 and Temporal."""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from demo_support import arguments, run_configured  # noqa: E402

from aether_agent_memory.remember.contracts.models import DeleteRequest  # noqa: E402


async def demo(directory, *, config_path, credential_file):
    return await run_configured(directory, config_path, credential_file, scenario)


async def scenario(run):
    service = run.service
    execution = service.execution
    context, command = run.context, run.command
    source = {
        "kind": "conversation",
        "external_id": run.run_id,
        "external_version": "1",
        "occurred_at": run.started_at,
    }
    await run.start()
    saved_job, saved = await command(
        "save",
        "remember.save",
        {
            "selection": {"session_id": run.run_id, "task_id": run.run_id},
            "source": source,
            "content": {"kind": "text", "text": "我喜欢无糖咖啡。"},
        },
    )
    await execution.drain(120)
    recall_payload = {
        "query": "喜欢什么饮料",
        "selection": {"session_id": run.run_id, "task_id": run.run_id},
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
                "source": {**source, "external_id": run.run_id + "-correction"},
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
        "profile": service.config.embedding_profile,
        "trace": service.runtime.foundation.diagnostics.trace(
            context("trace"), run.contexts["recall"].trace_id, limit=500
        ),
        "retrieval": run.providers()["vectors"],
        "reranker": getattr(service.runtime.recall.reranker, "identifier", None),
        "model_space": service.runtime.model_space,
        "job_ids": [
            job.job_id for job in (saved_job, recalled_job, corrected_job, after_job, deleted_job)
        ],
        "saved": saved,
        "first_context": recalled,
        "correction": corrected,
        "corrected_context": after,
        "after_delete": deleted,
        "actions": actions,
    }
    return run.finish(evidence)


def main():
    args = arguments(__doc__)
    result = asyncio.run(
        demo(
            args.directory.resolve(), config_path=args.config, credential_file=args.credential_file
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
