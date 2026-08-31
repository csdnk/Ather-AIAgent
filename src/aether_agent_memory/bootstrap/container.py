"""Composition root for the P3 application host."""

from __future__ import annotations

from typing import Any

from aether_agent_memory.config.app_settings import AppSettings
from aether_agent_memory.context_store.ports import (
    ContextIndexTombstonePort,
    ContextProjectionQueuePort,
    ContextReindexPort,
    RetrievalTraceStorePort,
    SemanticIndexPort,
)
from aether_agent_memory.memory.projection import ProjectionQueuePort
from aether_agent_memory.runtime.dependencies import RuntimeDependencies, RuntimeProfile
from aether_agent_memory.runtime.legacy import P3Runtime, P3RuntimeConfig
from aether_agent_memory.runtime.ports import HealthCheckPort, IdempotencyPort
from aether_agent_memory.runtime.service import MemoryRuntime
from aether_agent_memory.session.ports import (
    SessionExtractionQueuePort,
    SessionStorePort,
)


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
    from aether_agent_memory.adapters.context_catalog import CompositeContextReader
    from aether_agent_memory.adapters.context_index_tombstone import (
        RedisContextIndexTombstones,
    )
    from aether_agent_memory.adapters.context_projection_executor import (
        ProviderContextProjectionExecutor,
    )
    from aether_agent_memory.adapters.context_projection_queue import (
        InMemoryContextProjectionQueue,
    )
    from aether_agent_memory.adapters.context_semantic_index import (
        P2ContextSemanticIndexAdapter,
    )
    from aether_agent_memory.adapters.context_store import MemoryStoreContextReader
    from aether_agent_memory.adapters.memory_extraction import (
        HttpMemoryExtractionAdapter,
    )
    from aether_agent_memory.adapters.memory_service import (
        LegacyContextAdapter,
        LegacyMemoryEventAdapter,
    )
    from aether_agent_memory.adapters.milvus import MilvusHealthAdapter
    from aether_agent_memory.adapters.p2 import (
        P2ObjectStoreAdapter,
        P2VectorIndexAdapter,
        P2VectorSearchAdapter,
    )
    from aether_agent_memory.adapters.projection_executor import ProviderProjectionExecutor
    from aether_agent_memory.adapters.projection_queue import InMemoryProjectionQueue
    from aether_agent_memory.adapters.redis import RedisHealthAdapter
    from aether_agent_memory.adapters.redis_context_projection_queue import (
        RedisContextProjectionQueue,
    )
    from aether_agent_memory.adapters.redis_projection_queue import RedisProjectionQueue
    from aether_agent_memory.adapters.redis_session_extraction_queue import (
        RedisSessionExtractionQueue,
    )
    from aether_agent_memory.adapters.resource_context import ResourceContextReader
    from aether_agent_memory.adapters.resource_store import (
        InMemoryResourceStore,
        RedisResourceStore,
    )
    from aether_agent_memory.adapters.retrieval_trace import RedisRetrievalTraceStore
    from aether_agent_memory.adapters.session_context import SessionContextReader
    from aether_agent_memory.adapters.session_extraction_queue import (
        InMemorySessionExtractionQueue,
    )
    from aether_agent_memory.adapters.session_store import (
        InMemorySessionStore,
        RedisSessionStore,
    )
    from aether_agent_memory.adapters.skill_context import SkillContextReader
    from aether_agent_memory.adapters.skill_store import (
        InMemorySkillStore,
        RedisSkillStore,
    )
    from aether_agent_memory.context_store.hierarchical import (
        HierarchicalContextSearchService,
    )
    from aether_agent_memory.context_store.in_memory import (
        InMemoryContextIndexTombstones,
        InMemoryRetrievalTraceStore,
        InMemorySemanticIndex,
    )
    from aether_agent_memory.context_store.reindex import ReindexService
    from aether_agent_memory.memory.formation import MemoryExtractionPort
    from aether_agent_memory.memory.projection import MemoryProjectionReconciler
    from aether_agent_memory.memory.retrieval import (
        ContextCatalogRecallSource,
        ContextRetrievalService,
        EpisodicRecallSource,
        P2E1RecallSource,
        RecallSource,
        SemanticRecallSource,
        WorkingRecallSource,
    )
    from aether_agent_memory.persistence.idempotency import InMemoryIdempotencyStore
    from aether_agent_memory.resource.ports import ResourceStorePort
    from aether_agent_memory.skill.ports import SkillStorePort

    idempotency_store: IdempotencyPort = InMemoryIdempotencyStore()
    action_log_store = None
    access_trace: Any = InMemoryAccessTraceAdapter()
    retrieval_trace_store: RetrievalTraceStorePort = InMemoryRetrievalTraceStore(
        max_entries=config.retrieval_trace_max_entries
    )
    session_store: SessionStorePort = InMemorySessionStore()
    session_extraction_queue: SessionExtractionQueuePort = InMemorySessionExtractionQueue(
        lease_seconds=config.projection_queue_lease_seconds
    )
    if config.memory_store == "redis" or profile == RuntimeProfile.PRODUCTION:
        from aether_agent_memory.b3.action_log import RedisActionLogStore
        from aether_agent_memory.persistence.idempotency import RedisIdempotencyStore

        idempotency_store = RedisIdempotencyStore(config.redis_url)
        access_trace = RedisAccessTraceAdapter(config.redis_url)
        retrieval_trace_store = RedisRetrievalTraceStore(
            config.redis_url,
            ttl_seconds=config.retrieval_trace_ttl_seconds,
            operation_timeout_seconds=config.retrieval_trace_timeout_seconds,
        )
        action_log_store = RedisActionLogStore(config.redis_url)
        session_store = RedisSessionStore(config.redis_url)
        session_extraction_queue = RedisSessionExtractionQueue(
            config.redis_url,
            ttl_seconds=config.projection_queue_ttl_seconds,
            lease_seconds=config.projection_queue_lease_seconds,
            operation_timeout_seconds=config.projection_queue_timeout_seconds,
        )

    embedding = LegacyEmbeddingAdapter(legacy_runtime)
    scheduler = LegacySchedulerAdapter(legacy_runtime)
    object_store = P2ObjectStoreAdapter(legacy_runtime)
    vector_index = P2VectorIndexAdapter(legacy_runtime)
    query_embedding_endpoint = _query_embedding_endpoint(config)
    vector_search = P2VectorSearchAdapter(
        legacy_runtime,
        b1_endpoint=query_embedding_endpoint,
    )
    broker_url = config.broker_url or config.redis_url
    task_status_url = config.task_status_url or config.redis_url
    task_queue = CeleryLongMemoryTaskAdapter(broker_url=broker_url)
    task_status = RedisTaskStatusAdapter(redis_url=task_status_url)
    memory_extraction: MemoryExtractionPort | None = None
    if config.memory_extraction_url:
        memory_extraction = HttpMemoryExtractionAdapter(
            config.memory_extraction_url,
            model=config.memory_extraction_model,
            api_key=config.memory_extraction_api_key,
            timeout_seconds=config.memory_extraction_timeout_seconds,
            max_candidates=config.memory_extraction_max_candidates,
        )

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
    memory_store = legacy_runtime.compatibility_memory_store
    projection_reconciler = MemoryProjectionReconciler(
        memory_store
    )
    if config.memory_store == "redis" or profile == RuntimeProfile.PRODUCTION:
        projection_queue: ProjectionQueuePort = RedisProjectionQueue(
            config.redis_url,
            ttl_seconds=config.projection_queue_ttl_seconds,
            lease_seconds=config.projection_queue_lease_seconds,
            operation_timeout_seconds=config.projection_queue_timeout_seconds,
        )
    else:
        projection_queue = InMemoryProjectionQueue(
            lease_seconds=config.projection_queue_lease_seconds
        )
    if config.memory_store == "redis" or profile == RuntimeProfile.PRODUCTION:
        context_projection_queue: ContextProjectionQueuePort = RedisContextProjectionQueue(
            config.redis_url,
            ttl_seconds=config.projection_queue_ttl_seconds,
            lease_seconds=config.projection_queue_lease_seconds,
            operation_timeout_seconds=config.projection_queue_timeout_seconds,
        )
    else:
        context_projection_queue = InMemoryContextProjectionQueue(
            lease_seconds=config.projection_queue_lease_seconds
        )
    memory_context_reader = MemoryStoreContextReader(
        memory_store
    )
    session_context_reader = SessionContextReader(session_store)
    if profile == RuntimeProfile.PRODUCTION:
        resource_store: ResourceStorePort = RedisResourceStore(
            config.redis_url,
            ttl_seconds=config.context_fact_ttl_seconds,
        )
        skill_store: SkillStorePort = RedisSkillStore(
            config.redis_url,
            ttl_seconds=config.context_fact_ttl_seconds,
        )
    else:
        resource_store = InMemoryResourceStore()
        skill_store = InMemorySkillStore()
    skill_context_reader = SkillContextReader(skill_store)
    resource_context_reader = ResourceContextReader(
        resource_store,
        content_reader=object_store,
    )
    context_reader = CompositeContextReader(
        [
            memory_context_reader,
            resource_context_reader,
            session_context_reader,
            skill_context_reader,
        ],
        [
            memory_context_reader,
            resource_context_reader,
            session_context_reader,
            skill_context_reader,
        ],
    )
    # Integration/demo profiles use a deterministic reference index. Production
    # uses the provider-neutral adapter backed by the existing B1/P2 ports.
    semantic_index: SemanticIndexPort | None = None
    context_reindex: ContextReindexPort | None = None
    context_index_tombstones: ContextIndexTombstonePort = (
        InMemoryContextIndexTombstones()
    )
    if profile != RuntimeProfile.PRODUCTION:
        semantic_index = InMemorySemanticIndex()
        context_reindex = ReindexService(
            context_reader,
            context_reader,
            semantic_index,
            max_items=config.context_reindex_max_items,
            max_children_per_directory=config.context_reindex_max_children,
        )
    else:
        context_index_tombstones = RedisContextIndexTombstones(
            config.redis_url,
            operation_timeout_seconds=config.projection_queue_timeout_seconds,
        )
        semantic_index = P2ContextSemanticIndexAdapter(
            embedding=embedding,
            vector_index=vector_index,
            vector_search=vector_search,
            tombstones=context_index_tombstones,
        )
        context_reindex = ReindexService(
            context_reader,
            context_reader,
            semantic_index,
            max_items=config.context_reindex_max_items,
            max_children_per_directory=config.context_reindex_max_children,
        )
    context_search = HierarchicalContextSearchService(
        context_reader,
        context_reader,
        semantic_index=semantic_index,
        trace_store=retrieval_trace_store,
    )
    retrieval_sources: list[RecallSource] = [
        WorkingRecallSource(memory_service._working),
        EpisodicRecallSource(memory_service._episodic),
        SemanticRecallSource(memory_service._semantic),
        P2E1RecallSource(vector_search),
        ContextCatalogRecallSource(context_search),
    ]

    return RuntimeDependencies(
        embedding=embedding,
        memory_events=LegacyMemoryEventAdapter(legacy_runtime),
        context_builder=LegacyContextAdapter(legacy_runtime),
        task_queue=task_queue,
        task_status=task_status,
        object_store=object_store,
        vector_search=vector_search,
        vector_index=vector_index,
        memory_store=memory_store,
        retrieval=ContextRetrievalService(
            retrieval_sources,
            trace_store=retrieval_trace_store,
        ),
        retrieval_trace_store=retrieval_trace_store,
        context_catalog=context_reader,
        context_content=context_reader,
        context_search=context_search,
        context_semantic_index=semantic_index,
        context_reindex=context_reindex,
        context_index_tombstones=context_index_tombstones,
        context_projection_queue=context_projection_queue,
        context_projection_executor=ProviderContextProjectionExecutor(
            catalog=context_reader,
            content=context_reader,
            semantic_index=semantic_index,
        ),
        resource_store=resource_store,
        skill_store=skill_store,
        projection_queue=projection_queue,
        projection_reconciler=projection_reconciler,
        projection_executor=ProviderProjectionExecutor(
            memory_store=memory_store,
            embedding=embedding,
            vector_index=vector_index,
            context_semantic_index=semantic_index,
        ),
        session_store=session_store,
        session_extraction_queue=session_extraction_queue,
        memory_extraction=memory_extraction,
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
