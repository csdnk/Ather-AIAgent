"""Validate document summary, source retention and revocation with configured providers.

Use a dedicated development/test deployment. This scenario reports exact model
output checks as failures when the configured model does not produce them.
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from demo_support import arguments, run_configured  # noqa: E402

from aether_agent_memory.recall.contracts.models import ContextPack, RecallRequest  # noqa: E402
from aether_agent_memory.remember.contracts.models import (  # noqa: E402
    DeleteRequest,
    LifecycleRequest,
    RememberReceipt,
    RememberRequest,
    SourceInput,
)
from aether_agent_memory.runtime.contracts.models import ScopeSelector  # noqa: E402

EVENT = "The deployment failed on Tuesday."
RULE = "Rollback requires an approval."


async def run(directory, *, config_path, credential_file):
    return await run_configured(directory, config_path, credential_file, scenario)


async def scenario(run):
    host, execution = run.service.runtime, run.service.execution
    context = run.context
    line = "Request tracing and rollback design.\n"
    repeats = max(40, host.remember.policy.working_summary_min_bytes // len(line.encode()) + 1)
    text = line * repeats + EVENT + "\n" + RULE
    raw_file = text.encode("utf-8")
    if len(raw_file) > host.remember.policy.max_input_bytes:
        raise ValueError("summary scenario exceeds the configured document limit")
    await run.start()
    document = await run.service.documents.upload(
        context("upload"), run.run_id, "1", raw_file, "text/plain"
    )
    request = RememberRequest(
        source=SourceInput(
            kind="document",
            external_id=run.run_id,
            external_version="1",
            occurred_at=run.started_at,
        ),
        selection=ScopeSelector(session_id=run.run_id, task_id=run.run_id),
        content=document,
        task_context="Review the deployment failure and rollback requirements.",
    )
    _, saved = await run.command("save", "remember.save", request.model_dump(mode="json"))
    receipt = RememberReceipt.model_validate(saved)
    memory_id = receipt.memories[0].memory_id
    working = host.remember.get(context(), memory_id)
    immediate = await host.remember.read_source(context(), receipt.source, len(text) - len(RULE))
    stages = [
        {
            "stage": "source_read_after_save",
            "working_version": working.ref.version,
            "working_bytes": len(working.content.encode()),
            "source_bytes": len(text.encode()),
            "original_immediately_readable": immediate["content"] == RULE,
        }
    ]
    await execution.drain(120)
    working = host.remember.get(context(), memory_id)
    _, recalled = await run.command(
        "recall-episode",
        "recall.execute",
        RecallRequest(
            query=EVENT, selection=ScopeSelector(task_id=run.run_id), sources="long_term"
        ).model_dump(mode="json"),
    )
    pack = ContextPack.model_validate(recalled)
    if not pack.groups or not pack.groups[0].items:
        return run.finish(
            {
                "passed": False,
                "reason": "episodic_recall_empty",
                "stages": stages,
                "recalled": recalled,
            }
        )
    episode = pack.groups[0].items[0].memory
    cached = await host.remember.bodies.cache.get(working.ref.scope, working.content_hash)
    summary = host.remember.processing(context(), memory_id)["working_summary"]
    stages.append(
        {
            "stage": "summary_and_episodic_ready",
            "working_version": working.ref.version,
            "working_summary_cached": cached == working.content,
            "summary_state": summary["state"] if summary else None,
            "episode_recalled": bool(pack.groups),
        }
    )
    host.remember.distill(context("distill"), (episode,))
    await execution.drain(120)
    _, recalled_semantic = await run.command(
        "recall-semantic",
        "recall.execute",
        RecallRequest(
            query=RULE, selection=ScopeSelector(task_id=run.run_id), sources="long_term"
        ).model_dump(mode="json"),
    )
    semantic_pack = ContextPack.model_validate(recalled_semantic)
    stages.append(
        {
            "stage": "reflection",
            "semantic_recalled": any(
                i.content == RULE for g in semantic_pack.groups for i in g.items
            ),
        }
    )
    await host.remember.bodies.cache.delete(working.ref.scope, working.content_hash)
    loaded = await host.remember.load_async(context(), (working.ref,))
    host.remember.lifecycle(
        context("archive"),
        memory_id,
        LifecycleRequest(
            expected_version=working.ref.version,
            target="archived",
            reason="session task complete",
        ),
    )
    original_parts, start = [], 0
    while True:
        page = await host.remember.read_source(context(), receipt.source, start)
        original_parts.append(page["content"])
        if page["next_start"] is None:
            break
        start = page["next_start"]
    stages.append(
        {
            "stage": "scoped_cache_delete_and_archive",
            "summary_loadable": bool(loaded.items),
            "source_retained": "".join(original_parts) == text,
            "episode_retained": host.remember.get(context(), episode.memory_id).status == "active",
        }
    )
    host.remember.revoke_source(
        context("revoke"),
        receipt.source.source_id,
        DeleteRequest(expected_revision=1, reason="withdraw source"),
    )
    guard_context = context()
    with host.foundation.uow.transaction() as tx:
        guarded = host.remember.final_guard(tx, guard_context, (episode,), "recall")
    stages.append(
        {
            "stage": "source_revocation",
            "episode_blocked": guarded.items[0].decision == "excluded",
        }
    )
    passed = (
        stages[0]["original_immediately_readable"]
        and stages[1]["working_summary_cached"]
        and stages[1]["summary_state"] == "ready"
        and stages[1]["episode_recalled"]
        and stages[2]["semantic_recalled"]
        and stages[3]["summary_loadable"]
        and stages[3]["source_retained"]
        and stages[3]["episode_retained"]
        and stages[4]["episode_blocked"]
    )
    return run.finish({"passed": passed, "stages": stages})


if __name__ == "__main__":
    args = arguments(__doc__)
    result = asyncio.run(
        run(args.directory.resolve(), config_path=args.config, credential_file=args.credential_file)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
