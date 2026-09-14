"""Strongly-typed, validated application settings for the P3 host.

This module centralizes configuration so the FastAPI application host
(``aether_agent_memory.app``) and the legacy ``scripts/p3_service.py`` entry
point share one source of truth.  Environment variable names stay compatible
with the existing ``AETHER_*`` conventions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RuntimeProfile = Literal["demo", "integration", "production"]


def _csv(value: str | None) -> list[str]:
    if not value or not value.strip():
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


class AppSettings(BaseSettings):
    """Application settings for the P3 Memory Service host."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # -- identity / runtime profile --
    profile: RuntimeProfile = Field(
        default="integration", validation_alias="AETHER_RUNTIME_PROFILE"
    )
    version: str = "0.1.0"
    data_dir: str = Field(default="./artifacts", validation_alias="AETHER_P3_DATA_DIR")

    # -- P2 downstream data plane --
    p2_endpoint: str = Field(default="localhost:50052", validation_alias="AETHER_P2_GRPC")
    p2_engine: str = Field(default="object/default", validation_alias="AETHER_P2_ENGINE")
    p2_bucket: str = Field(default="p3-memory", validation_alias="AETHER_P2_BUCKET")
    p2_collection: str = Field(default="p3", validation_alias="AETHER_P2_COLLECTION")
    p2_timeout_seconds: float = Field(default=10.0, validation_alias="AETHER_P2_TIMEOUT_SECONDS")

    # -- B2 primary memory store --
    memory_store: Literal["redis", "sqlite"] = Field(
        default="redis", validation_alias="AETHER_B2_MEMORY_STORE"
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0", validation_alias="AETHER_B2_REDIS_URL"
    )
    milvus_uri: str = Field(default="", validation_alias="AETHER_B2_MILVUS_URI")
    milvus_collection: str = Field(
        default="b2_memory_chunks_v3", validation_alias="AETHER_B2_MILVUS_COLLECTION"
    )
    vector_dimension: int = Field(default=512, validation_alias="AETHER_B2_VECTOR_DIMENSION")
    milvus_projection: bool = Field(default=False, validation_alias="AETHER_B2_MILVUS_PROJECTION")

    # -- B1 embedding sidecar --
    b1_sidecar_url: str = Field(default="", validation_alias="AETHER_B1_SIDECAR_URL")
    b1_embedding_url: str = Field(default="", validation_alias="AETHER_B1_EMBEDDING_URL")
    b1_model_name: str = Field(
        default="BAAI/bge-small-zh-v1.5", validation_alias="AETHER_B1_MODEL_NAME"
    )
    b1_tenant_id: str = Field(default="p3-runtime", validation_alias="AETHER_B1_TENANT_ID")
    b1_sidecar_timeout_seconds: float = Field(
        default=120.0, validation_alias="AETHER_B1_SIDECAR_TIMEOUT_SECONDS"
    )
    b1_max_batch_items: int = Field(default=32, validation_alias="AETHER_B1_MAX_BATCH_ITEMS")
    b1_chunk_max_chars: int = Field(default=400, validation_alias="AETHER_B1_CHUNK_MAX_CHARS")
    b1_chunk_overlap_chars: int = Field(
        default=40, validation_alias="AETHER_B1_CHUNK_OVERLAP_CHARS"
    )

    # -- B3 control plane --
    storage_control_url: str = Field(default="", validation_alias="AETHER_P3_STORAGE_CONTROL_URL")
    b3_shadow_mode: bool = Field(default=True, validation_alias="AETHER_B3_SHADOW_MODE")

    # -- B2 async task queue --
    broker_url: str = Field(default="", validation_alias="AETHER_B2_BROKER_URL")
    result_backend: str = Field(default="", validation_alias="AETHER_B2_RESULT_BACKEND")
    task_status_url: str = Field(default="", validation_alias="AETHER_B2_TASK_STATUS_URL")
    compression_target_ratio: float = Field(
        default=5.0, validation_alias="AETHER_B2_COMPRESSION_TARGET_RATIO"
    )
    compression_store: Literal["redis", "sqlite"] = Field(
        default="redis", validation_alias="AETHER_B2_COMPRESSION_STORE"
    )

    # -- HTTP host / CORS / timeout --
    host: str = Field(default="0.0.0.0", validation_alias="AETHER_P3_HOST")
    port: int = Field(default=8080, validation_alias="AETHER_P3_PORT")
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    request_timeout_seconds: float = Field(
        default=60.0, validation_alias="AETHER_P3_TIMEOUT_SECONDS"
    )

    # -- context retrieval observability --
    retrieval_trace_ttl_seconds: int = Field(
        default=3600,
        gt=0,
        validation_alias="AETHER_P3_RETRIEVAL_TRACE_TTL_SECONDS",
    )
    retrieval_trace_max_entries: int = Field(
        default=1000,
        gt=0,
        validation_alias="AETHER_P3_RETRIEVAL_TRACE_MAX_ENTRIES",
    )
    retrieval_trace_timeout_seconds: float = Field(
        default=0.25,
        gt=0,
        validation_alias="AETHER_P3_RETRIEVAL_TRACE_TIMEOUT_SECONDS",
    )
    projection_queue_ttl_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        gt=0,
        validation_alias="AETHER_P3_PROJECTION_QUEUE_TTL_SECONDS",
    )
    projection_queue_lease_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias="AETHER_P3_PROJECTION_QUEUE_LEASE_SECONDS",
    )
    projection_queue_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        validation_alias="AETHER_P3_PROJECTION_QUEUE_TIMEOUT_SECONDS",
    )
    context_reindex_max_items: int = Field(
        default=10_000,
        gt=0,
        le=1_000_000,
        validation_alias="AETHER_P3_CONTEXT_REINDEX_MAX_ITEMS",
    )
    context_reindex_max_children: int = Field(
        default=1_000,
        gt=0,
        le=100_000,
        validation_alias="AETHER_P3_CONTEXT_REINDEX_MAX_CHILDREN",
    )
    context_fact_ttl_seconds: int | None = Field(
        default=None,
        gt=0,
        validation_alias="AETHER_P3_CONTEXT_FACT_TTL_SECONDS",
    )

    # -- optional semantic Session-to-Memory extraction --
    memory_extraction_url: str = Field(
        default="", validation_alias="AETHER_P3_MEMORY_EXTRACTION_URL"
    )
    memory_extraction_model: str = Field(
        default="", validation_alias="AETHER_P3_MEMORY_EXTRACTION_MODEL"
    )
    memory_extraction_api_key: str = Field(
        default="", validation_alias="AETHER_P3_MEMORY_EXTRACTION_API_KEY"
    )
    memory_extraction_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias="AETHER_P3_MEMORY_EXTRACTION_TIMEOUT_SECONDS",
    )
    memory_extraction_max_candidates: int = Field(
        default=20,
        gt=0,
        le=100,
        validation_alias="AETHER_P3_MEMORY_EXTRACTION_MAX_CANDIDATES",
    )

    # -- demo gate --
    enable_demo: bool = Field(default=False, validation_alias="AETHER_ENABLE_DEMO")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors(cls, value: object) -> object:
        if isinstance(value, str):
            origins = _csv(value)
            if not origins:
                return ["*"]
            return origins
        return value

    @field_validator("profile", mode="before")
    @classmethod
    def _normalize_profile(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        raw = value.strip().lower()
        if raw in {"prod", "production"}:
            return "production"
        if raw in {"mock", "demo"}:
            return "demo"
        if raw in {"local", "dev", "development", "integration", "integ"}:
            return "integration"
        return raw

    def validate_for_profile(self) -> None:
        """Fail fast when a profile is misconfigured.

        A production profile must never silently fall back to mock embedding,
        demo state, or in-memory storage.  Missing required dependencies must
        surface at startup, not as a degraded request.
        """
        if self.profile == "production":
            if self.enable_demo:
                raise ValueError(
                    "production profile cannot enable demo (AETHER_ENABLE_DEMO must be false)"
                )
            if not self.b1_sidecar_url and not self.b1_embedding_url:
                raise ValueError(
                    "production profile requires a real B1 sidecar URL "
                    "(AETHER_B1_SIDECAR_URL or AETHER_B1_EMBEDDING_URL)"
                )
            if not self.p2_endpoint:
                raise ValueError("production profile requires a P2 endpoint")
            if not self.redis_url:
                raise ValueError(
                    "production profile requires a Redis URL for runtime state and traces"
                )
            if self.memory_store != "redis":
                raise ValueError("production profile requires Redis-backed memory_store")

    def safe_status(self) -> dict[str, object]:
        """Public-safe capability view; never leaks secrets."""
        return {
            "profile": self.profile,
            "version": self.version,
            "memory_store": self.memory_store,
            "vector_dimension": self.vector_dimension,
            "milvus_projection": self.milvus_projection,
            "demo_enabled": self.enable_demo,
            "b1_configured": bool(self.b1_sidecar_url or self.b1_embedding_url),
            "b3_shadow_mode": self.b3_shadow_mode,
            "storage_control_configured": bool(self.storage_control_url),
            "p2_endpoint": self.p2_endpoint,
            "retrieval_trace_ttl_seconds": self.retrieval_trace_ttl_seconds,
            "context_fact_ttl_seconds": self.context_fact_ttl_seconds,
            "memory_extraction_configured": bool(self.memory_extraction_url),
        }
