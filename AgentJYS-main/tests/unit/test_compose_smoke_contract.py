"""用真实 HTTP 路由核对 smoke 消费者，防止响应结构漂移掩盖闭环失败。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from fastapi import FastAPI

from aether_agent_memory.api.routers import demo
from aether_agent_memory.config.app_settings import AppSettings

SPEC = importlib.util.spec_from_file_location(
    "compose_smoke", Path(__file__).parents[2] / "scripts" / "run_compose_smoke.py"
)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def test_compose_workers_share_the_api_memory_store() -> None:
    """消息队列连接成功不代表正文存储可用；所有消费者必须共用 API 的存储。"""
    compose = yaml.safe_load((Path(__file__).parents[2] / "compose.yaml").read_text("utf-8"))
    services = compose["services"]
    memory_store = services["p3"]["environment"]["AETHER_B2_REDIS_URL"]
    assert memory_store == "redis://redis:6379/0"
    for name in (
        "celery-worker", "p3-session-worker", "p3-projection-worker",
        "p3-context-projection-worker",
    ):
        assert services[name]["environment"].get("AETHER_B2_REDIS_URL") == memory_store, name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "vectors", "matches", "error"),
    [
        ("succeeded", 1, 1, None),
        ("failed", 1, 1, "B2 Celery task did not succeed"),
        ("succeeded", 0, 1, "P2 E1 did not receive"),
        ("succeeded", 1, 0, "P2 E1 search did not return"),
    ],
)
async def test_smoke_consumes_route_report_and_rejects_failed_flow(
    monkeypatch, capsys, state, vectors, matches, error
) -> None:
    # 只替换耗时业务依赖；报告是否包裹由生产路由决定，不在测试中复制。
    backend = AsyncMock(return_value={
        "object_key": "smoke-object",
        "b2_async": {
            "state": state,
            "p2_vector_count": vectors,
            "search_match_count": matches,
        },
        "action": {"action_type": "retain"},
        "context": {"memories": [{"memory_id": "memory-1"}]},
    })
    monkeypatch.setattr(demo, "run_full_test", backend)
    monkeypatch.setattr(demo, "record_demo_run", lambda *_: None)
    monkeypatch.setattr(demo, "update_last_success", lambda *_: None)
    app = FastAPI()
    app.state.settings = AppSettings(profile="demo", enable_demo=True)
    app.state.runtime = object()
    app.include_router(demo.router)
    app.get("/health")(lambda: {"status": "ok"})

    def client_factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

    monkeypatch.setattr(smoke, "httpx", SimpleNamespace(
        AsyncClient=client_factory, Timeout=httpx.Timeout, HTTPError=httpx.HTTPError
    ))
    if error is None:
        await smoke.main()
        assert "COMPOSE_SMOKE_PASSED" in capsys.readouterr().out
    else:
        with pytest.raises(RuntimeError, match=error):
            await smoke.main()
        assert "COMPOSE_SMOKE_PASSED" not in capsys.readouterr().out
    backend.assert_awaited_once()
