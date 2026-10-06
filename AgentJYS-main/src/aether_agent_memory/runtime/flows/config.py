"""Explicit, versioned deployment configuration for the complete P3 runtime."""

from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import AnyHttpUrl, Field, model_validator

from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.runtime.contracts.models import (
    AuthorizationGrant,
    ContractModel,
    Digest,
    Identifier,
    Permission,
    Principal,
)
from aether_agent_memory.runtime.storage.configuration import AzureStorageConfiguration
from aether_agent_memory.runtime.temporal.config import TemporalConfiguration


class Tenant(ContractModel):
    tenant_id: Identifier
    enabled: bool = True


class IdentityEntry(ContractModel):
    credential_sha256: Digest | None = None
    principal: Principal


class JWTSubjectMapping(ContractModel):
    subject: str = Field(min_length=1, max_length=512)
    principal_id: Identifier


class OrganizationBinding(ContractModel):
    organization_id: Identifier
    tenant_id: Identifier
    application_id: Identifier = "p3"
    agent_id: Identifier = "p3-agent"


class KeycloakDirectoryConfiguration(ContractModel):
    """Read-only native organization directory; no locally maintained member list."""

    client_id: Identifier
    client_secret_file: Path
    roles_client_id: Identifier
    organizations: tuple[OrganizationBinding, ...]
    role_permissions: dict[str, tuple[Permission, ...]]
    refresh_seconds: float = Field(default=5, ge=1, le=300)
    stale_after_seconds: float = Field(default=20, ge=5, le=600)
    timeout_seconds: float = Field(default=3, gt=0, le=10)

    @model_validator(mode="after")
    def validate_directory(self) -> Self:
        if not self.client_secret_file.is_absolute():
            raise ValueError("directory client secret requires an absolute external path")
        if not self.organizations or not self.role_permissions:
            raise ValueError("directory requires explicit organization and role allowlists")
        if self.stale_after_seconds <= self.refresh_seconds:
            raise ValueError("directory freshness must exceed refresh interval")
        for field in ("organization_id", "tenant_id"):
            values = [getattr(item, field) for item in self.organizations]
            if len(values) != len(set(values)):
                raise ValueError("directory organization/tenant bindings must be unique")
        return self


class JWTIssuerConfiguration(ContractModel):
    issuer: str = Field(min_length=1, max_length=2048)
    jwks_url: AnyHttpUrl
    audience: str = Field(min_length=1, max_length=512)
    algorithms: tuple[Literal["RS256", "ES256"], ...] = ("RS256",)
    subject_mappings: tuple[JWTSubjectMapping, ...] = ()
    directory: KeycloakDirectoryConfiguration | None = None
    jwks_cache_seconds: int = Field(default=300, ge=1, le=3600)
    jwks_timeout_seconds: float = Field(default=5, gt=0, le=30)
    leeway_seconds: int = Field(default=30, ge=0, le=120)

    @model_validator(mode="after")
    def validate_jwks_and_subjects(self) -> Self:
        if not self.algorithms or len(self.algorithms) != len(set(self.algorithms)):
            raise ValueError("JWT algorithms must be a nonempty, unique allowlist")
        url = self.jwks_url
        if url.scheme != "https" and url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("JWKS must use HTTPS except for a loopback development endpoint")
        subjects = [mapping.subject for mapping in self.subject_mappings]
        if (not subjects and self.directory is None) or len(subjects) != len(set(subjects)):
            raise ValueError("each JWT issuer requires unique subject mappings")
        if self.directory:
            from urllib.parse import urlsplit

            parsed = urlsplit(self.issuer)
            if (
                parsed.scheme not in {"http", "https"}
                or (
                    parsed.scheme != "https"
                    and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                )
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or not parsed.path.startswith("/realms/")
                or len(parsed.path.split("/")) != 3
                or not parsed.path.split("/")[-1]
                or subjects
            ):
                raise ValueError(
                    "directory requires an exact Keycloak issuer and no static subjects"
                )
        return self


class IdentityConfiguration(ContractModel):
    revision: int = Field(ge=1)
    tenants: tuple[Tenant, ...]
    identities: tuple[IdentityEntry, ...]
    grants: tuple[AuthorizationGrant, ...] = ()
    jwt_issuers: tuple[JWTIssuerConfiguration, ...] = ()
    ruoyi: dict[str, Any] | None = None

    @model_validator(mode="after")
    def bound_tenants(self) -> Self:
        if self.ruoyi is not None:
            from aether_platform.auth.ruoyi import validate_ruoyi_config

            validate_ruoyi_config(self.ruoyi)
            if self.jwt_issuers or not self.ruoyi.get("role_permissions"):
                raise ValueError("Ruoyi mode requires role permission ceilings and no JWT issuers")
            for permissions in self.ruoyi["role_permissions"].values():
                for permission in permissions:
                    Permission(permission)
            mapped = {item.get("principal_id") for item in self.ruoyi.get("mappings", [])}
            if mapped.intersection(entry.principal.principal_id for entry in self.identities):
                raise ValueError(
                    "Ruoyi mapped principals must not have independent static identities"
                )
        tenant_ids = [tenant.tenant_id for tenant in self.tenants]
        if len(tenant_ids) != len(set(tenant_ids)):
            raise ValueError("duplicate business tenant")
        principal_ids = [entry.principal.principal_id for entry in self.identities]
        if len(principal_ids) != len(set(principal_ids)):
            raise ValueError("duplicate principal")
        if any(entry.principal.home_scope.tenant_id not in tenant_ids for entry in self.identities):
            raise ValueError("every principal requires an explicitly registered tenant")
        if any(
            binding.tenant_id not in tenant_ids
            for issuer in self.jwt_issuers
            if issuer.directory
            for binding in issuer.directory.organizations
        ):
            raise ValueError("directory organization requires a registered P3 tenant")
        issuers = [issuer.issuer for issuer in self.jwt_issuers]
        if len(issuers) != len(set(issuers)):
            raise ValueError("duplicate JWT issuer")
        bindings = [
            (issuer.issuer, mapping.subject)
            for issuer in self.jwt_issuers
            for mapping in issuer.subject_mappings
        ]
        if len(bindings) != len(set(bindings)):
            raise ValueError("duplicate JWT subject mapping")
        mapped_principals = {
            mapping.principal_id
            for issuer in self.jwt_issuers
            for mapping in issuer.subject_mappings
        }
        if not mapped_principals.issubset(set(principal_ids)):
            raise ValueError("JWT subject mapping references an unknown principal")
        if any(
            entry.credential_sha256 is None
            and entry.principal.principal_id not in mapped_principals
            for entry in self.identities
        ):
            raise ValueError("each principal requires a static credential or JWT subject mapping")
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


class BrowserIdentityConfiguration(ContractModel):
    """Public Keycloak browser client; never contains a client secret."""

    url: AnyHttpUrl
    realm: str = Field(pattern=r"^[A-Za-z0-9_.-]+$", max_length=128)
    client_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def secure_endpoint(self) -> Self:
        if self.url.scheme != "https" and self.url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("browser identity must use HTTPS except on loopback")
        if self.url.username or self.url.password or self.url.query or self.url.fragment:
            raise ValueError("browser identity URL cannot contain credentials, query or fragment")
        return self

    @property
    def issuer(self) -> str:
        return f"{str(self.url).rstrip('/')}/realms/{self.realm}"


class ServiceConfiguration(ContractModel):
    temporal: TemporalConfiguration
    http_wait_seconds: float = Field(default=30, gt=0, le=300)
    request_timeout_seconds: float = Field(default=60, gt=0, le=3600, allow_inf_nan=False)
    health_probe_timeout_seconds: float = Field(default=2, gt=0, le=60, allow_inf_nan=False)
    health_snapshot_ttl_seconds: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)
    profile: Literal["development", "test", "staging", "production"] = "development"
    data_dir: Path
    identity_file: Path
    storage_mode: Literal["current_p2", "production_p2", "azure"] = "azure"
    metadata_backend: Literal["p2", "postgresql"] = "postgresql"
    azure_storage: AzureStorageConfiguration | None = None
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    embedding_profile: Literal["native"] = "native"
    embedding_config: Path | None = None
    recall_config: Path | None = None
    language_model: LanguageModel | None = None
    verifier_model: LanguageModel | None = None
    p2_endpoint: str | None = None
    p2_bucket: str = Field(default="p3-memory", min_length=1, max_length=128)
    p2_collection: str = Field(default="p3", min_length=1, max_length=128)
    p2_secure: bool = False
    p2_ca_file: Path | None = None
    p2_cert_file: Path | None = None
    p2_key_file: Path | None = None
    p2_token_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    maintenance_principals: tuple[str, ...] = ()
    automatic_cache_repair: bool = True
    remember: RememberPolicy = Field(default_factory=RememberPolicy)
    poll_seconds: float = Field(default=0.25, ge=0.01, le=30)
    periodic_seconds: float = Field(default=1, ge=0.01, le=60)
    shutdown_seconds: float = Field(default=15, ge=0.1, le=300)
    identity_reload_seconds: float = Field(default=2, ge=0.01, le=300)
    browser_identity: BrowserIdentityConfiguration | None = None
    otlp_traces_endpoint: AnyHttpUrl | None = None
    log_retention_days: int = Field(default=14, ge=1)
    log_max_records: int = Field(default=200000, ge=100)
    cache_capacity_bytes: int = Field(default=268435456, ge=1024)
    operate_decay_seconds: float = Field(default=3600, gt=0)
    operate_audit_seconds: float = Field(default=300, gt=0)
    operate_retry_seconds: float = Field(default=5, gt=0)
    operate_stats_retention_seconds: float = Field(default=3600, gt=0)

    @model_validator(mode="after")
    def production_dependencies(self) -> Self:
        if self.storage_mode == "azure":
            if self.azure_storage is None or self.metadata_backend != "postgresql":
                raise ValueError(
                    "Azure requires the complete storage bundle and PostgreSQL metadata"
                )
            if (
                self.p2_endpoint
                or self.p2_secure
                or self.p2_token_env
                or any((self.p2_ca_file, self.p2_cert_file, self.p2_key_file))
            ):
                raise ValueError("Azure storage cannot be mixed with P2 connection settings")
            return self.model_dependencies(azure=True)
        if self.azure_storage is not None:
            raise ValueError("Azure storage configuration requires storage_mode=azure")
        if not self.p2_endpoint or self.p2_endpoint != self.p2_endpoint.strip():
            raise ValueError("an explicit P2 host:port endpoint is required")
        endpoint = urlsplit("//" + self.p2_endpoint)
        if (
            not endpoint.hostname
            or not endpoint.port
            or endpoint.path
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("P2 endpoint must be host:port without credentials or URI paths")
        if self.storage_mode == "current_p2":
            raise ValueError(
                "current P2 reference metadata is retired; use Azure storage "
                "or explicit P2 protocol tests"
            )
        else:
            if self.metadata_backend != "p2":
                raise ValueError("production P2 requires P2 transaction metadata")
            if not self.p2_secure:
                raise ValueError("production P2 requires authenticated TLS transport")
        if (self.p2_ca_file or self.p2_cert_file or self.p2_key_file) and not self.p2_secure:
            raise ValueError("P2 certificate settings require secure transport")
        if bool(self.p2_cert_file) != bool(self.p2_key_file):
            raise ValueError("P2 client certificate and key must be configured together")
        if self.p2_token_env and not self.p2_secure:
            raise ValueError("P2 tokens require secure transport")
        return self.model_dependencies()

    def model_dependencies(self, *, azure: bool = False) -> Self:
        if self.embedding_profile != "native" or self.embedding_config is None:
            raise ValueError("configured native embedding is required")
        if self.language_model is None or self.recall_config is None:
            raise ValueError("an explicit language model and Recall policy are required")
        from aether_agent_memory.recall.basic.config import RecallSettings

        recall = RecallSettings.model_validate_json(self.recall_config.read_text(encoding="utf-8"))
        if recall.milvus_uri:
            if azure:
                raise ValueError("Azure vectors must use the single azure_storage Milvus binding")
            raise ValueError(
                "vector access must go through P2; direct Milvus configuration is retired"
            )
        return self

    @classmethod
    def load(cls, path: Path) -> "ServiceConfiguration":
        resolved = path.resolve()
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("service configuration must be an object")
        for key in (
            "data_dir",
            "identity_file",
            "embedding_config",
            "recall_config",
            "p2_ca_file",
            "p2_cert_file",
            "p2_key_file",
        ):
            if raw.get(key):
                value = Path(raw[key])
                raw[key] = value if value.is_absolute() else resolved.parent / value
        if isinstance(raw.get("temporal"), dict):
            for key in ("tls_ca_file", "tls_cert_file", "tls_key_file"):
                if raw["temporal"].get(key):
                    value = Path(raw["temporal"][key])
                    raw["temporal"][key] = value if value.is_absolute() else resolved.parent / value
        if isinstance(raw.get("azure_storage"), dict):
            for component in ("redis", "milvus", "ceph"):
                section = raw["azure_storage"].get(component)
                if isinstance(section, dict) and section.get("ca_file"):
                    value = Path(section["ca_file"])
                    section["ca_file"] = value if value.is_absolute() else resolved.parent / value
        return cls.model_validate(raw)
