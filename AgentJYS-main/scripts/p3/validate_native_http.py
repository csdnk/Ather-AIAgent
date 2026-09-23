"""Real BGE + SQLite + loopback HTTP acceptance. Writes evidence outside checkout."""
# 真实运行验收：本机 TCP HTTP + SQLite + 原生 BGE，覆盖现有 B 保存/投影及 Recall。
# 此脚本不证明新 B 多块接口已接入；耗时仅是本次单机功能观测，不代表生产容量。

import argparse
import json
import platform
import secrets
import socket
import sys
import time
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from threading import Thread

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope  # noqa: E402
from aether_agent_memory.runtime.flows.host import ThreeFlows  # noqa: E402
from aether_agent_memory.runtime.flows.http import create_app  # noqa: E402
from aether_agent_memory.runtime.foundation.common import now  # noqa: E402


@contextmanager
def serve(runtime):
    # 使用实际回环 TCP 端口启动 uvicorn，并在退出时等待服务关闭；不是 TestClient 替身。
    with socket.socket() as listener:
        # 让操作系统分配可用端口，并保留 socket 交给服务，避免先查端口再启动的竞争。
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(create_app(runtime), log_level="error", access_log=False)
        )
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 20
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("HTTP host did not start")
                time.sleep(0.05)
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False
            ) as client:
                yield client
        finally:
            server.should_exit = True
            thread.join(20)
            if thread.is_alive():
                raise RuntimeError("HTTP host did not stop cleanly")


def verify(directory: Path, config: Path) -> dict:
    # 在全新外部目录创建数据库，完成保存、发布、检索、重取、幂等与重启绑定检查。
    directory.mkdir(parents=True, exist_ok=False)
    credential = secrets.token_urlsafe(32)
    headers = {"Authorization": "Bearer " + credential}
    started = time.monotonic()
    runtime = ThreeFlows(directory / "p3.db", directory / "cache", embedding_config=config)
    load_seconds = time.monotonic() - started
    try:
        runtime.foundation.identity.provision(
            [
                (
                    sha256(credential.encode()).hexdigest(),
                    Principal(
                        principal_id="http_acceptance",
                        home_scope=Scope(
                            tenant_id="acceptance",
                            application_id="app",
                            user_id="user",
                            agent_id="agent",
                        ),
                        permissions=tuple(Permission),
                        auth_epoch=1,
                    ),
                )
            ]
        )
        with serve(runtime) as client:
            assert client.get("/p3/live").status_code == 200
            assert (
                client.post("/p3/recall", json={"query": "probe", "selection": {}}).status_code
                == 401
            )
            saved = client.post(
                "/p3/remember",
                headers={**headers, "X-Operation-ID": "native_http_save"},
                json={
                    "source": {
                        "kind": "text",
                        "external_id": "native_http",
                        "external_version": "1",
                        "occurred_at": now(),
                    },
                    "selection": {},
                    "content": {"kind": "text", "text": "我喜欢无糖咖啡，每天下午喝一杯。"},
                },
            )
            assert saved.status_code == 200, saved.text
            # 等待真实后台投影完成后再召回，不能仅凭 Remember 接收成功认定索引已可用。
            deadline = time.monotonic() + 45
            while True:
                with runtime.foundation.uow.transaction() as tx:
                    vectors = tx.rows("recall_vectors")
                    tasks = tx.rows("tasks")
                if vectors and all(row["record"]["state"] == "succeeded" for _, row in tasks):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("real Remember projection did not become ready")
                time.sleep(0.1)
            task_id = saved.json()["task_ids"][0]
            assert (
                client.get(f"/p3/tasks/{task_id}", headers=headers).json()["state"] == "succeeded"
            )
            query = {
                "query": "我喜欢喝什么",
                "selection": {},
                "sources": "long_term",
                "token_budget": 256,
            }
            query_headers = {**headers, "X-Operation-ID": "native_http_recall"}
            started = time.monotonic()
            recalled = client.post("/p3/recall", headers=query_headers, json=query)
            recall_seconds = time.monotonic() - started
            assert recalled.status_code == 200, recalled.text
            pack = recalled.json()
            assert pack["outcome"] == "available" and "无糖咖啡" in pack["rendered_context"]
            assert pack["tokens_used"] <= 256
            recall_id = pack["recall_id"]
            assert (
                client.get(f"/p3/recalls/{recall_id}", headers=headers).json()["state"]
                == "completed"
            )
            assert client.get(f"/p3/recalls/{recall_id}/result", headers=headers).json() == pack
            # 相同幂等键重试返回原结果；同键改问题必须冲突，不能覆盖原业务操作。
            assert client.post("/p3/recall", headers=query_headers, json=query).json() == pack
            assert (
                client.post(
                    "/p3/recall", headers=query_headers, json={**query, "query": "另一问题"}
                ).status_code
                == 409
            )
            with runtime.foundation.uow.transaction() as tx:
                attempts = tx.rows("native_embedding_attempts")
                spaces = tx.rows("embedding_spaces")
                binding = tx.read("settings", "p3_embedding_binding")
                logs = tx.raw.scan("semantic-backend-evidence")
                events = [
                    r["event"]["payload"]
                    for _, r in tx.rows("outbox")
                    if r["event"]["event_type"] == "recall.access"
                ]
            assert {r["usage"] for _, r in attempts} == {"query", "passage"}
            assert all(r["state"] == "succeeded" for _, r in attempts)
            assert logs and spaces and len([e for e in events if e["stage"] == "packed"]) == 1
            ctx = runtime.foundation.identity.context(credential)
            trace = runtime.foundation.diagnostics.trace(
                ctx, attempts[-1][1]["trace_id"], limit=500
            )
            assert trace["records"]
            # Logs must not contain plaintext credentials or content.
            trace_text = json.dumps(trace, ensure_ascii=False)
            assert credential not in trace_text and "无糖咖啡" not in trace_text
    finally:
        runtime.close()
    # 重新装配服务，核对持久化模型绑定及历史结果仍可通过受控接口读取。
    restarted = ThreeFlows(directory / "p3.db", directory / "cache", embedding_config=config)
    try:
        with serve(restarted) as client:
            assert client.get(f"/p3/recalls/{recall_id}/result", headers=headers).json() == pack
        assert restarted.model_space == binding["retrieval_space_ref"]
    finally:
        restarted.close()
    return {
        "passed": True,
        "generated_at": now(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "transport": "real_loopback_tcp_http",
        "mode": "existing_B_Recall_with_real_native_embedding_and_SQLite",
        "model_binding": binding,
        "native_attempts": len(attempts),
        "evidence_count": len(logs),
        "model_load_seconds": round(load_seconds, 3),
        "recall_seconds": round(recall_seconds, 3),
        "tokens_used": pack["tokens_used"],
        "task_status_verified": True,
        "recall_status_result_verified": True,
        "idempotency_verified": True,
        "restart_result_and_space_verified": True,
        "trace_redaction_verified": True,
        "not_verified": [
            "real_B_generation_contracts",
            "Milvus",
            "production_capacity",
            "external_identity",
            "real_extraction_LLM",
            "human_consumer_approval",
        ],
    }


def main() -> int:
    # 显式指定配置与外部证据路径，避免覆盖既有验收数据或把运行产物写入源码目录。
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", type=Path, required=True, help="New external evidence directory"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.directory, args.config)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
