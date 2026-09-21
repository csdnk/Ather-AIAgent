"""Health adapter probe tests."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from aether_agent_memory.adapters.milvus import MilvusHealthAdapter
from aether_agent_memory.runtime.status import ComponentStatus


@pytest.mark.asyncio
async def test_milvus_unconfigured_is_degraded_not_unavailable() -> None:
    health = await MilvusHealthAdapter("").health()
    assert health.status == ComponentStatus.DEGRADED
    assert health.critical is False


@pytest.mark.asyncio
async def test_milvus_probe_uses_utility_collection_api(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    connections = SimpleNamespace(
        connect=lambda **kwargs: calls.append(("connect", kwargs)),
        disconnect=lambda alias: calls.append(("disconnect", alias)),
    )
    utility = SimpleNamespace(
        has_collection=lambda name, *, using: calls.append(
            ("has_collection", (name, using))
        )
    )
    pymilvus = ModuleType("pymilvus")
    pymilvus.connections = connections  # type: ignore[attr-defined]
    pymilvus.utility = utility  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymilvus", pymilvus)

    health = await MilvusHealthAdapter("http://milvus.example:19530").health()

    assert health.status == ComponentStatus.HEALTHY
    assert calls == [
        (
            "connect",
            {
                "alias": "p3-health",
                "host": "milvus.example",
                "port": 19530,
                "timeout": 2.0,
            },
        ),
        ("has_collection", ("__p3_health_probe__", "p3-health")),
        ("disconnect", "p3-health"),
    ]
