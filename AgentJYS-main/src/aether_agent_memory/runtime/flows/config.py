"""Explicit, versioned deployment configuration for the complete P3 runtime."""

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, model_validator

from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.runtime.contracts.models import (
    AuthorizationGrant,
    ContractModel,
    Digest,
    Identifier,
    Principal,
)


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


class ServiceConfiguration(ContractModel):
    profile: Literal["local", "production"] = "local"
    data_dir: Path
    identity_file: Path
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    embedding_profile: Literal["native", "lexical"] = "native"
    embedding_config: Path | None = None
    recall_config: Path | None = None
    language_model: LanguageModel | None = None
    verifier_model: LanguageModel | None = None
    p2_endpoint: str | None = None
    p2_bucket: str = "p3-memory"
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
        if self.profile == "production" and (
            self.embedding_profile != "native"
            or self.language_model is None
            or self.p2_endpoint is None
        ):
            raise ValueError("production requires native embedding, a language model and real P2")
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
        return cls.model_validate(raw)
