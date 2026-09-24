"""Run real Remember with SQLite P2, a Redis double and deterministic model providers.

Example: python scripts/p3/demo_remember_summary.py --directory /outside/repo/demo
Each execution creates a new run directory. No real model, parser or Redis service is claimed.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.basic.content import RedisBodyCache
from aether_agent_memory.remember.basic.sources import PreparedDocument
from aether_agent_memory.remember.contracts.models import (
    CandidateFact,
    DeleteRequest,
    DocumentInput,
    ExtractionResult,
    LifecycleRequest,
    RememberRequest,
    SourceInput,
)
from aether_agent_memory.remember.local import create_runtime
from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope, ScopeSelector
from aether_agent_memory.runtime.foundation.common import now
from aether_agent_memory.runtime.foundation.requests import text_hash

EVENT = "The deployment failed on Tuesday."
RULE = "Rollback requires an approval."


class ModelDouble:
    async def extract(self, ctx, request):
        return ExtractionResult(
            candidates=(
                CandidateFact(
                    text=EVENT,
                    kind="episodic",
                    sources=(request.source,),
                    evidence_status="supported",
                    event_key="deployment_tuesday",
                ),
            )
            if EVENT in request.text
            else (),
            model_id="deterministic-demo",
            policy_version=request.policy_version,
        )

    async def review_episodes(self, ctx, episodes, originals, policy_version):
        source = next(i.sources[0] for i in originals if RULE in i.content)
        return ExtractionResult(
            candidates=(
                CandidateFact(
                    text=RULE, kind="semantic", sources=(source,), evidence_status="supported"
                ),
            ),
            model_id="deterministic-demo-review",
            policy_version=policy_version,
        )


async def run(directory: Path) -> dict:
    import fakeredis.aioredis

    directory.mkdir(parents=True, exist_ok=True)
    host = create_runtime(
        directory / "metadata.db", directory / "cache", embedding_profile="lexical"
    )
    redis = fakeredis.aioredis.FakeRedis()
    host.remember.bodies.cache = RedisBodyCache(redis, host.remember.policy)
    host.remember.extraction = ModelDouble()
    principal = Principal(
        principal_id="demo-user",
        home_scope=Scope(
            tenant_id="tenant-a", application_id="demo", user_id="demo-user", agent_id="agent"
        ),
        permissions=tuple(Permission),
        auth_epoch=1,
    )
    host.foundation.identity.provision([(sha256(b"demo-token").hexdigest(), principal)])

    def context():
        return host.foundation.identity.context("demo-token", timeout_seconds=300)

    text = "Request tracing and rollback design.\n" * 40 + EVENT + "\n" + RULE
    raw_file = ("# Uploaded engineering review\n" + text).encode("utf-8")
    original_key = "demo/upload/design-v1"
    await host.remember.bodies.p2.put_object(original_key, raw_file)
    document = DocumentInput(
        kind="document",
        provider_id="demo-files",
        document_id="design",
        document_version="1",
        expected_hash=sha256(raw_file).hexdigest(),
    )

    class P2Files:
        async def acquire_text(self, ctx, request):
            raw = await host.remember.bodies.p2.get_object(original_key)
            assert request == document and sha256(raw).hexdigest() == request.expected_hash
            return PreparedDocument(
                document=request,
                original_storage_ref=original_key,
                original_bytes=len(raw),
                media_type="text/markdown",
                parsed_text=text,
                parsed_hash=text_hash(text),
                parser_version="demo-prepared-text-v1",
            )

    host.remember.documents["demo-files"] = P2Files()
    try:
        receipt = await host.remember.save(
            context(),
            RememberRequest(
                source=SourceInput(
                    kind="document", external_id="design", external_version="1", occurred_at=now()
                ),
                selection=ScopeSelector(session_id="engineering"),
                content=document,
                task_context="Review the deployment failure and rollback requirements.",
            ),
        )
        memory_id = receipt.memories[0].memory_id
        working = host.remember.get(context(), memory_id)
        immediate = await host.remember.read_source(
            context(), receipt.source, len(text) - len(RULE)
        )
        stages = [
            {
                "stage": "saved_before_summary",
                "working_version": working.ref.version,
                "working_bytes": len(working.content.encode()),
                "source_bytes": len(text.encode()),
                "original_immediately_readable": immediate["content"] == RULE,
            }
        ]
        await host.drain()
        working = host.remember.get(context(), memory_id)
        pack = await host.recall.recall(
            context(), RecallRequest(query=EVENT, selection=ScopeSelector(), sources="long_term")
        )
        episode = pack.groups[0].items[0].memory
        cached = await host.remember.bodies.cache.get(working.ref.scope, working.content_hash)
        stages.append(
            {
                "stage": "summary_and_episodic_ready",
                "working_version": working.ref.version,
                "working_summary_cached": cached == working.content,
                "summary_state": host.remember.processing(context(), memory_id)["working_summary"][
                    "state"
                ],
                "episode_recalled": bool(pack.groups),
            }
        )
        host.remember.distill(context(), (episode,))
        await host.drain()
        semantic_pack = await host.recall.recall(
            context(), RecallRequest(query=RULE, selection=ScopeSelector(), sources="long_term")
        )
        stages.append(
            {
                "stage": "reflection",
                "semantic_recalled": any(
                    i.content == RULE for g in semantic_pack.groups for i in g.items
                ),
            }
        )
        await redis.flushdb()
        loaded = await host.remember.load_async(context(), (working.ref,))
        host.remember.lifecycle(
            context(),
            memory_id,
            LifecycleRequest(
                expected_version=working.ref.version,
                target="archived",
                reason="session task complete",
            ),
        )
        original = await host.remember.read_source(context(), receipt.source)
        stages.append(
            {
                "stage": "cache_eviction_and_archive",
                "summary_reloaded": bool(loaded.items),
                "source_retained": original["content"] == text,
                "episode_retained": host.remember.get(context(), episode.memory_id).status
                == "active",
            }
        )
        host.remember.revoke_source(
            context(),
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
            and stages[1]["episode_recalled"]
            and stages[2]["semantic_recalled"]
            and stages[3]["summary_reloaded"]
            and stages[3]["source_retained"]
            and stages[3]["episode_retained"]
            and stages[4]["episode_blocked"]
        )
        result = {
            "passed": passed,
            "providers": {
                "p2": "SQLite",
                "redis": "fakeredis",
                "models": "deterministic doubles",
                "file_parsing": "prepared P2 result",
            },
            "stages": stages,
        }
        (directory / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result
    finally:
        await redis.aclose()
        host.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    target = args.directory / datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S-%f")
    result = asyncio.run(run(target))
    print(json.dumps({**result, "directory": str(target)}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
