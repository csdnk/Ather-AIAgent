"""Reproducible three-flow local demo; credentials and evidence stay in its run directory."""

import argparse
import asyncio
import json
import secrets
import sys
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aether_agent_memory.recall.contracts.models import RecallRequest  # noqa: E402
from aether_agent_memory.remember.contracts.models import (  # noqa: E402
    CorrectionRequest,
    DeleteRequest,
    RememberRequest,
    SourceInput,
    TextInput,
)
from aether_agent_memory.runtime.contracts.models import (  # noqa: E402
    Permission,
    Principal,
    Scope,
    ScopeSelector,
)
from aether_agent_memory.runtime.flows.host import ThreeFlows  # noqa: E402
from aether_agent_memory.runtime.foundation.common import FoundationError, now  # noqa: E402


async def demo(
    directory: Path,
    embedding_profile: str = "native",
    embedding_config: Path | None = None,
    recall_config: Path | None = None,
) -> dict:
    credentials = {name: secrets.token_urlsafe(32) for name in ("alice", "bob", "carol", "david")}
    path = directory / "demo-credentials.json"
    with path.open("x", encoding="utf-8") as output:
        json.dump(credentials, output)
    path.chmod(0o600)
    app = ThreeFlows(
        directory / "p3.db",
        directory / "cache",
        embedding_profile=embedding_profile,
        embedding_config=embedding_config,
        recall_config=recall_config,
    )
    try:
        people = [
            Principal(
                principal_id=name,
                home_scope=Scope(
                    tenant_id="tenant_a" if name in {"alice", "bob"} else "tenant_b",
                    application_id="demo",
                    user_id=name,
                    agent_id="agent",
                ),
                permissions=tuple(Permission),
                auth_epoch=1,
            )
            for name in credentials
        ]
        app.foundation.identity.provision(
            [(sha256(credentials[p.principal_id].encode()).hexdigest(), p) for p in people]
        )

        def ctx(user="alice"):
            return app.foundation.identity.context(credentials[user], timeout_seconds=300)

        if app.owned_reranker:
            await app.owned_reranker.rerank(ctx(), "健康检测", ("模型服务健康检测",))
        if app.owned_vectors:
            await app.owned_vectors.prepare(ctx())

        receipt = await app.remember.save(
            ctx(),
            RememberRequest(
                source=SourceInput(
                    kind="conversation",
                    external_id="session_1",
                    external_version="1",
                    occurred_at=now(),
                ),
                selection=ScopeSelector(session_id="session_1"),
                content=TextInput(kind="text", text="我喜欢无糖咖啡。"),
            ),
        )
        await app.drain()
        processing = app.foundation.diagnostics.task(ctx(), receipt.task_ids[0])
        with app.foundation.uow.transaction() as tx:
            fact = tx.get(processing.result_ref)["memories"][0]
        recall_ctx = ctx()
        pack = await app.recall.recall(
            recall_ctx, RecallRequest(query="饮品偏好 无糖咖啡", selection=ScopeSelector())
        )
        await app.drain()
        memory = app.remember.get(ctx(), fact["memory_id"])
        observed = await app.executor.observe(ctx(), memory.ref, "original")
        other = await app.recall.recall(
            ctx("bob"), RecallRequest(query="无糖咖啡", selection=ScopeSelector())
        )
        correction = app.remember.correct(
            ctx(),
            memory.ref.memory_id,
            CorrectionRequest(
                expected_version=memory.ref.version,
                content="我现在喜欢红茶。",
                source=SourceInput(
                    kind="conversation",
                    external_id="correction",
                    external_version="1",
                    occurred_at=now(),
                ),
                reason="显式纠错",
            ),
        )
        await app.drain()
        corrected = await app.recall.recall(
            ctx(), RecallRequest(query="红茶", selection=ScopeSelector())
        )
        invalidated = False
        try:
            app.recall.result(ctx(), pack.recall_id)
        except FoundationError as exc:
            invalidated = exc.code == "RESULT_INVALIDATED"
        current = app.remember.get(ctx(), memory.ref.memory_id)
        deleted = app.remember.delete(
            ctx(),
            memory.ref.memory_id,
            DeleteRequest(expected_revision=current.object_revision, reason="演示删除"),
        )
        await app.drain()
        after_delete = await app.recall.recall(
            ctx(), RecallRequest(query="红茶 咖啡", selection=ScopeSelector(), sources="both")
        )
        with app.foundation.uow.transaction() as tx:
            actions = [value for _, value in tx.rows("operate_actions")]
        passed = (
            pack.outcome == "available"
            and corrected.groups[0].items[0].memory.version == 2
            and other.outcome == "empty"
            and invalidated
            and after_delete.outcome == "empty"
            and observed.readable
        )
        evidence = {
            "profile": "local_" + app.embedding_profile,
            "passed": passed,
            "production_acceptance": False,
            "extraction": "literal_episode_baseline",
            "retrieval": "milvus" if app.owned_vectors else "sqlite_" + app.embedding_profile,
            "rerank_policy": app.recall_settings.rerank_policy,
            "reranker": getattr(app.recall.reranker, "identifier", None),
            "trace": app.foundation.diagnostics.trace(ctx(), recall_ctx.trace_id, limit=500),
            "model_space": app.model_space,
            "execution": "real_local_cache_folders",
            "saved": receipt.model_dump(mode="json"),
            "first_context": pack.model_dump(mode="json"),
            "cache_observation": observed.model_dump(mode="json"),
            "isolated_user_outcome": other.outcome,
            "correction": correction.model_dump(mode="json"),
            "corrected_context": corrected.model_dump(mode="json"),
            "old_context_invalidated": invalidated,
            "delete": deleted.model_dump(mode="json"),
            "after_delete": after_delete.model_dump(mode="json"),
            "actions": actions,
        }
        (directory / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return {
            "passed": passed,
            "profile": "local_" + app.embedding_profile,
            "database": str(directory / "p3.db"),
            "evidence": str(directory / "evidence.json"),
            "memory_id": memory.ref.memory_id,
            "recall_id": pack.recall_id,
        }
    finally:
        app.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--embedding-profile", choices=["native", "lexical"], default="native")
    parser.add_argument("--embedding-config", type=Path)
    parser.add_argument("--recall-config", type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "p3.db").exists() or (directory / "demo-credentials.json").exists():
        parser.error("choose a fresh directory; existing state will not be overwritten")
    result = asyncio.run(
        demo(directory, args.embedding_profile, args.embedding_config, args.recall_config)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
