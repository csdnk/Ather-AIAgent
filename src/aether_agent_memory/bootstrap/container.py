"""Composition root for the P3 application host."""

from __future__ import annotations

from typing import Any

from aether_agent_memory.config.app_settings import AppSettings
from aether_agent_memory.runtime.dependencies import RuntimeDependencies, RuntimeProfile
from aether_agent_memory.runtime.legacy import P3Runtime, P3RuntimeConfig
from aether_agent_memory.runtime.ports import HealthCheckPort
from aether_agent_memory.runtime.service import MemoryRuntime


def build_runtime(settings: AppSettings) -> MemoryRuntime:
    """Build the long-lived P3 runtime from resolved application settings."""
    settings.validate_for_profile()
    config = P3RuntimeConfig.from_settings(settings)
    profile = RuntimeProfile(settings.profile)
    legacy_runtime = P3Runtime(config)
    return MemoryRuntime.from_config(
        config,
        profile=profile,
        legacy_runtime=legacy_runtime,
    )


def build_dependencies_from_legacy(
    legacy_runtime: P3Runtime,
    *,
    config: P3RuntimeConfig,
    profile: RuntimeProfile,
) -> RuntimeDependencies:
    """Build production P3 ports from one resolved runtime config.

    ``legacy_runtime`` is a migration bridge that owns the existing managers and
    clients. New application code receives only the explicit ports assembled here.
    """
    from aether_agent_memory.adapters.access_trace import (
        InMemoryAccessTraceAdapter,
        RedisAccessTraceAdapter,
    )
    from aether_agent_memory.adapters.b1_client import LegacyEmbeddingAdapter
    from aether_agent_memory.adapters.b3 import LegacySchedulerAdapter
    from aether_agent_memory.adapters.celery import (
        CeleryLongMemoryTaskAdapter,
        RedisTaskStatusAdapter,
    )
    from aether_agent_memory.adapters.memory_service import (
        LegacyContextAdapter,
        LegacyMemoryEventAdapter,
    )
    from aether_agent_memory.adapters.milvus import MilvusHealthAdapter
    from aether_agent_memory.adapters.p2 import P2ObjectStoreAdapter, P2VectorSearchAdapter
    from aether_agent_memory.adapters.redis import RedisHealthAdapter
    from aether_agent_memory.memory.retrieval import (
        EpisodicRecallSource,
        MemoryRetrievalService,
        P2E1RecallSource,
        RecallSource,
        SemanticRecallSource,
        WorkingRecallSource,
    )

    idempotency_store = None
    action_log_store = None
    access_trace: Any = InMemoryAccessTraceAdapter()
    if config.memory_store == "redis":
        from aether_agent_memory.b3.action_log import RedisActionLogStore
        from aether_agent_memory.persistence.idempotency import RedisIdempotencyStore

        idempotency_store = RedisIdempotencyStore(config.redis_url)
        access_trace = RedisAccessTraceAdapter(config.redis_url)
        action_log_store = RedisActionLogStore(config.redis_url)

    embedding = LegacyEmbeddingAdapter(legacy_runtime)
    scheduler = LegacySchedulerAdapter(legacy_runtime)
    object_store = P2ObjectStoreAdapter(legacy_runtime)
    query_embedding_endpoint = _query_embedding_endpoint(config)
    vector_search = P2VectorSearchAdapter(
        legacy_runtime,
        b1_endpoint=query_embedding_endpoint,
    )
    broker_url = config.broker_url or config.redis_url
    task_status_url = config.task_status_url or config.redis_url
    task_queue = CeleryLongMemoryTaskAdapter(broker_url=broker_url)
    task_status = RedisTaskStatusAdapter(redis_url=task_status_url)

    health_checks: list[HealthCheckPort] = [embedding, object_store, scheduler]
    if config.memory_store == "redis":
        health_checks.append(
            RedisHealthAdapter(
                config.redis_url,
                critical=profile == RuntimeProfile.PRODUCTION,
            )
        )
    health_checks.extend(
        [
            MilvusHealthAdapter(config.milvus_uri),
            CeleryLongMemoryTaskAdapter(broker_url=broker_url),
        ]
    )

    memory_service = legacy_runtime.memory
    retrieval_sources: list[RecallSource] = [
        WorkingRecallSource(memory_service._working),
        EpisodicRecallSource(memory_service._episodic),
        SemanticRecallSource(memory_service._semantic),
        P2E1RecallSource(vector_search),
    ]

    return RuntimeDependencies(
        embedding=embedding,
        memory_events=LegacyMemoryEventAdapter(legacy_runtime),
        context_builder=LegacyContextAdapter(legacy_runtime),
        task_queue=task_queue,
        task_status=task_status,
        object_store=object_store,
        vector_search=vector_search,
        retrieval=MemoryRetrievalService(retrieval_sources),
        scheduler=scheduler,
        access_trace=access_trace,
        idempotency_store=idempotency_store,
        action_log_store=action_log_store,
        health_checks=health_checks,
        legacy_runtime=legacy_runtime,
    )


def _query_embedding_endpoint(config: P3RuntimeConfig) -> str:
    if config.b1_embedding_url.strip():
        return config.b1_embedding_url.strip()
    if config.b1_sidecar_url.strip():
        return f"{config.b1_sidecar_url.rstrip('/')}/v1/intercept"
    return "http://localhost:18081/v1/intercept"
