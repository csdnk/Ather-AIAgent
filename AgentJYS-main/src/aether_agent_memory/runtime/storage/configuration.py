"""Complete Azure storage configuration; credential values remain outside files."""

import os
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import Field, StrictBool, model_validator

from aether_agent_memory.remember.basic.ceph_p2 import CephP2Config
from aether_agent_memory.runtime.contracts.models import ContractModel

EnvironmentVariable = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
StorageName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
SqlIdentifier = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")]


def secret(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"storage credential environment variable is missing: {name}")
    return value


def https_origin(value: str, *, allow_insecure_http: bool = False) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ({"https", "http"} if allow_insecure_http else {"https"})
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Azure storage requires an HTTPS origin without credentials")
    _ = parsed.port


class PostgresStorageConfiguration(ContractModel):
    dsn_env: EnvironmentVariable
    schema_name: SqlIdentifier

    def resolve_dsn(self) -> str:
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        try:
            values = {
                key: str(value)
                for key, value in conninfo_to_dict(secret(self.dsn_env)).items()
                if value is not None
            }
        except ValueError:
            raise
        except Exception:
            raise ValueError("invalid PostgreSQL connection configuration") from None
        if values.get("sslmode") != "verify-full" or not values.get("sslrootcert"):
            raise ValueError("Azure PostgreSQL requires verified TLS and an explicit trust root")
        if not all(values.get(name) for name in ("host", "dbname", "user", "password")):
            raise ValueError("Azure PostgreSQL requires explicit host, database and credentials")
        if values["dbname"] in {"postgres", "template0", "template1"}:
            raise ValueError("Azure PostgreSQL requires a dedicated application database")
        trust = values["sslrootcert"]
        if trust != "system" and not Path(trust).is_file():
            raise ValueError("PostgreSQL TLS trust root is missing")
        # This exact schema must be provisioned explicitly. An absent schema must
        # fail table creation, never fall through to the public schema.
        return make_conninfo("", **{**values, "options": "-csearch_path=" + self.schema_name})


class RedisStorageConfiguration(ContractModel):
    host: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
    port: int = Field(default=6380, ge=1, le=65535)
    password_env: EnvironmentVariable
    ca_file: Path
    timeout_seconds: float = Field(default=5, gt=0, le=30)
    max_connections: int = Field(default=16, ge=1, le=128)


class MilvusStorageConfiguration(ContractModel):
    uri: str
    database: SqlIdentifier
    collection: SqlIdentifier
    token_env: EnvironmentVariable
    ca_file: Path
    server_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]*$")

    @model_validator(mode="after")
    def transport(self) -> Self:
        https_origin(self.uri)
        return self


class CephStorageConfiguration(ContractModel):
    endpoint: str
    allow_insecure_http: StrictBool = False
    bucket: str = Field(min_length=1, max_length=128)
    access_key_env: EnvironmentVariable
    secret_key_env: EnvironmentVariable
    ca_file: Path | None = None
    region: str = "us-east-1"

    @model_validator(mode="after")
    def transport(self) -> Self:
        https_origin(self.endpoint, allow_insecure_http=self.allow_insecure_http)
        if "/" in self.bucket or self.bucket.strip() != self.bucket:
            raise ValueError("Ceph requires a bucket name")
        return self

    def transport_config(self) -> CephP2Config:
        return CephP2Config(
            endpoint=self.endpoint,
            bucket=self.bucket,
            access_key_env=self.access_key_env,
            secret_key_env=self.secret_key_env,
            ca_file=str(self.ca_file) if self.ca_file else None,
            region=self.region,
        )


class AzureStorageConfiguration(ContractModel):
    namespace: StorageName
    postgres: PostgresStorageConfiguration
    redis: RedisStorageConfiguration
    milvus: MilvusStorageConfiguration
    ceph: CephStorageConfiguration

    def require_credentials(self) -> None:
        for name in (
            self.postgres.dsn_env,
            self.redis.password_env,
            self.milvus.token_env,
            self.ceph.access_key_env,
            self.ceph.secret_key_env,
        ):
            secret(name)
        for path in (self.redis.ca_file, self.milvus.ca_file, self.ceph.ca_file):
            if path is not None and not path.is_file():
                raise ValueError("Azure storage TLS CA file is missing")
