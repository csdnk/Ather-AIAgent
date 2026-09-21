from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src" / "aether_agent_memory"


@pytest.mark.unit
def test_api_layer_does_not_import_concrete_infrastructure_clients() -> None:
    files = [SRC / "app.py"]
    files.extend((SRC / "api").rglob("*.py"))
    combined = "\n".join(path.read_text("utf-8") for path in files)

    forbidden = [
        "RedisTaskStatusStore",
        "RedisIdempotencyStore",
        "RedisActionLogStore",
        "B1EmbeddingServiceClient",
        "P2GrpcClient",
        "P2StorageClient",
        "P2VectorSink",
        "submit_long_text",
        "p2_collection_for_scope",
        "runtime.client",
    ]
    for token in forbidden:
        assert token not in combined


@pytest.mark.unit
def test_application_layer_does_not_import_concrete_adapters_or_clients() -> None:
    files = list((SRC / "application").rglob("*.py"))
    combined = "\n".join(path.read_text("utf-8") for path in files)

    forbidden = [
        "aether_agent_memory.adapters",
        "RedisTaskStatusStore",
        "RedisIdempotencyStore",
        "RedisActionLogStore",
        "P2GrpcClient",
        "P2StorageClient",
        "P2VectorSink",
        "B1EmbeddingServiceClient",
        "from aether_agent_memory.runtime.legacy import P3Runtime",
    ]
    for token in forbidden:
        assert token not in combined


@pytest.mark.unit
def test_core_memory_does_not_import_provider_implementations() -> None:
    files = [SRC / "core" / "memory.py"]
    files.extend((SRC / "memory").rglob("*.py"))
    combined = "\n".join(path.read_text("utf-8") for path in files)

    forbidden = [
        "aether_agent_memory.adapters",
        "P2GrpcClient",
        "RedisTaskStatusStore",
        "RedisIdempotencyStore",
        "RedisActionLogStore",
        "B1EmbeddingServiceClient",
        "from celery",
        "import celery",
        "aether_agent_memory.b2.celery_app",
        "pymilvus",
        "MilvusMemoryStore",
        "HeuristicScheduler",
        "P2MigrationExecutor",
    ]
    for token in forbidden:
        assert token not in combined


@pytest.mark.unit
def test_production_adapters_do_not_read_environment_directly() -> None:
    files = [
        SRC / "adapters" / "p2.py",
        SRC / "adapters" / "celery.py",
        SRC / "adapters" / "redis.py",
        SRC / "adapters" / "milvus.py",
        SRC / "adapters" / "b1_client.py",
        SRC / "adapters" / "b3.py",
    ]
    combined = "\n".join(path.read_text("utf-8") for path in files)

    assert "os.getenv" not in combined
    assert "os.environ" not in combined


@pytest.mark.unit
def test_b2_memory_service_depends_on_context_builder_protocol() -> None:
    source = (SRC / "b2" / "service.py").read_text("utf-8")

    assert "MockContextPackBuilder" not in source
    assert "ContextPackBuilder" in source


@pytest.mark.unit
def test_retrieval_context_builder_depends_on_port_not_mock_builder() -> None:
    source = (SRC / "memory" / "context" / "builder.py").read_text("utf-8")

    assert "MockContextPackBuilder" not in source
    assert "from aether_agent_memory.runtime.ports import ContextPackBuilder" in source


@pytest.mark.unit
def test_runtime_dependencies_use_explicit_store_ports() -> None:
    source = (SRC / "runtime" / "dependencies.py").read_text("utf-8")

    assert "idempotency_store: IdempotencyPort | None" in source
    assert "action_log_store: ActionLogPort | None" in source
    assert "idempotency_store: Any" not in source
    assert "action_log_store: Any" not in source


@pytest.mark.unit
def test_runtime_ports_use_typed_boundary_dtos() -> None:
    source = (SRC / "runtime" / "ports.py").read_text("utf-8")

    expected = [
        "LongMemorySubmission",
        "TaskStatusRecord",
        "ObjectReference",
        "MemorySearchResult",
        "IdempotencyPort",
        "ActionLogPort",
    ]
    for token in expected:
        assert token in source


@pytest.mark.unit
def test_b3_candidate_logic_lives_outside_http_api_layer() -> None:
    api_source = (SRC / "api" / "routers" / "context.py").read_text("utf-8")
    b3_source = (SRC / "b3" / "application.py").read_text("utf-8")

    assert "from aether_agent_memory.b3.application import b3_candidates_from_context" in api_source
    assert "def b3_candidates_from_context" in b3_source
    assert "recency_score" not in api_source
    assert "business_priority" not in api_source
