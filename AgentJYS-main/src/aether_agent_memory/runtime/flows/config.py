"""Explicit, versioned deployment configuration for the complete P3 runtime."""

import json
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, model_validator

from aether_agent_memory.remember.basic.ceph_p2 import CephP2Config
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.runtime.contracts.models import (
    AuthorizationGrant,
    ContractModel,
    Digest,
    Identifier,
    Principal,
)
from aether_agent_memory.runtime.temporal.config import TemporalConfiguration


class Tenant(ContractModel):
    tenant_id: Identifier
    enabled: bool = True


class IdentityEntry(ContractModel):
    credential_sha256: Digest
    principal: Principal


class IdentityConfiguration(ContractModel):
    revision: int = Field(ge=1)
    tenants: tuple[Tenant, ...]
    identities: tuple[IdentityEntry, ...]
    grants: tuple[AuthorizationGrant, ...] = ()

    @model_validator(mode="after")
    def bound_tenants(self) -> Self:
        ids = [t.tenant_id for t in self.tenants]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate business tenant")
        if any(i.principal.home_scope.tenant_id not in ids for i in self.identities):
            raise ValueError("every principal requires an explicitly registered tenant")
        return self


class LanguageModel(ContractModel):
    endpoint: str = "http://127.0.0.1:11434/v1"
    model: str = Field(min_length=1)
    api_key_env: str = "AETHER_LLM_API_KEY"
    timeout_seconds: float = Field(default=60, gt=0, le=300)
    max_output_tokens: int = Field(default=4096, ge=256, le=16384)
    max_response_bytes: int = Field(default=1048576, ge=1024, le=8388608)
    concurrency: int = Field(default=2, ge=1, le=32)
    response_format: Literal["json_object", "json_schema"] = "json_object"
    token_limit_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_completion_tokens"
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    health_cache_seconds: float = Field(default=60, ge=1, le=300, allow_inf_nan=False)
    health_max_output_tokens: int = Field(default=1024, ge=256, le=4096)
    prompt_version: Identifier = "remember_structured_v2"


class ServiceConfiguration(ContractModel):
    temporal: TemporalConfiguration
    http_wait_seconds: float = Field(default=30, gt=0, le=300)
    profile: Literal["local", "production"] = "local"
    data_dir: Path
    identity_file: Path
    metadata_backend: Literal["sqlite", "postgresql"] = "sqlite"
    postgres_dsn_env: str = Field(
        default="AETHER_POSTGRES_DSN", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    embedding_profile: Literal["native", "lexical"] = "native"
    embedding_config: Path | None = None
    recall_config: Path | None = None
    language_model: LanguageModel | None = None
    verifier_model: LanguageModel | None = None
    p2_endpoint: str | None = None
    p2_bucket: str = "p3-memory"
    ceph: CephP2Config | None = None
    redis_url_env: str | None = None
    maintenance_principals: tuple[str, ...] = ()
    automatic_cache_repair: bool = True
    remember: RememberPolicy = Field(default_factory=RememberPolicy)
    poll_seconds: float = Field(default=0.25, ge=0.01, le=30)
    periodic_seconds: float = Field(default=1, ge=0.01, le=60)
    shutdown_seconds: float = Field(default=15, ge=0.1, le=300)
    identity_reload_seconds: float = Field(default=2, ge=0.01, le=300)
    log_retention_days: int = Field(default=14, ge=1)
    log_max_records: int = Field(default=200000, ge=100)
    cache_capacity_bytes: int = Field(default=268435456, ge=1024)
    operate_decay_seconds: float = Field(default=3600, gt=0)
    operate_audit_seconds: float = Field(default=300, gt=0)
    operate_retry_seconds: float = Field(default=5, gt=0)
    operate_stats_retention_seconds: float = Field(default=3600, gt=0)

    @model_validator(mode="after")
    def production_dependencies(self) -> Self:
        if self.p2_endpoint is not None and self.ceph is not None:
            raise ValueError("configure exactly one object storage authority: Ceph or P2 gRPC")
        if self.profile == "production" and (
            self.embedding_profile != "native"
            or self.language_model is None
            or (self.p2_endpoint is None and self.ceph is None)
        ):
            raise ValueError("production requires native embedding, a language model and real P2")
        if self.profile == "production":
            if self.recall_config is None:
                raise ValueError("production requires an explicit Milvus configuration")
            vectors = json.loads(self.recall_config.read_text(encoding="utf-8"))
            if not isinstance(vectors, dict) or not vectors.get("milvus_uri"):
                raise ValueError("production requires Milvus; SQLite vectors are not allowed")
            if self.metadata_backend != "postgresql":
                raise ValueError("production Remember requires PostgreSQL transaction metadata")
        return self

    @classmethod
    def load(cls, path: Path) -> "ServiceConfiguration":
        resolved = path.resolve()
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("service configuration must be an object")
        for key in ("data_dir", "identity_file", "embedding_config", "recall_config"):
            if raw.get(key):
                value = Path(raw[key])
                raw[key] = value if value.is_absolute() else resolved.parent / value
        if isinstance(raw.get("temporal"), dict):
            for key in ("tls_ca_file", "tls_cert_file", "tls_key_file"):
                if raw["temporal"].get(key):
                    value = Path(raw["temporal"][key])
                    raw["temporal"][key] = value if value.is_absolute() else resolved.parent / value
        return cls.model_validate(raw)
