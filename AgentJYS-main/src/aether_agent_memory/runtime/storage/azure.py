"""Ownership-aware assembly of our real PG, Redis, Milvus and Ceph providers."""

from contextlib import suppress
from pathlib import Path
from typing import Any

from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.contracts.ports import MemoryReadPort
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.postgres import PostgresUnitOfWork
from aether_agent_memory.runtime.foundation.postgres_telemetry import PostgresTelemetry

from .configuration import AzureStorageConfiguration, secret
from .objects import CephObjects
from .ports import MetadataUnitOfWork
from .redis_cache import RedisCache
from .redis_executor import RedisExecutor
from .vectors import AzureVectors


class StorageProviders:
    def __init__(
        self,
        config: AzureStorageConfiguration,
        anchor: Path,
        policy: RememberPolicy,
        *,
        log_retention_days: int = 14,
        log_max_records: int = 200_000,
    ) -> None:
        # Check every required secret before acquiring any connection or local path.
        config.require_credentials()
        dsn = config.postgres.resolve_dsn()
        self.config, self.transferred, self.closed = config, False, False
        self.owned: list[Any] = []
        self.external: list[Any] = []
        self.acquired: list[tuple[Any, bool]] = []
        try:
            self.metadata = PostgresUnitOfWork(dsn, anchor / "metadata-anchor")
            self.owned.append(self.metadata)
            self.acquired.append((self.metadata, True))
            self.telemetry = PostgresTelemetry(
                dsn,
                anchor / "logs-anchor",
                retention_days=log_retention_days,
                max_records=log_max_records,
            )
            self.owned.append(self.telemetry)
            self.acquired.append((self.telemetry, True))
            self.objects = CephObjects(
                config.ceph.transport_config(),
                namespace=config.namespace,
                allow_insecure_http=config.ceph.allow_insecure_http,
            )
            self.external.append(self.objects)
            self.acquired.append((self.objects, False))
            from redis import Redis

            redis = config.redis
            self.redis_client = Redis(
                host=redis.host,
                port=redis.port,
                password=secret(redis.password_env),
                ssl=True,
                ssl_cert_reqs="required",
                ssl_check_hostname=True,
                ssl_ca_certs=str(redis.ca_file),
                socket_timeout=redis.timeout_seconds,
                socket_connect_timeout=redis.timeout_seconds,
                max_connections=redis.max_connections,
                decode_responses=False,
            )
            self.external.append(self.redis_client)
            self.acquired.append((self.redis_client, False))
            self.cache = RedisCache(self.redis_client, policy, namespace=config.namespace)
        except BaseException:
            # Preserve the acquisition failure after attempting every close.
            with suppress(BaseException):
                self.close()
            raise

    def vectors_factory(
        self,
        uow: MetadataUnitOfWork,
        identity: Identity,
        model_space: str,
        dimensions: int,
    ) -> AzureVectors:
        config = self.config.milvus
        vectors = AzureVectors(
            uow,
            identity,
            model_space,
            dimensions,
            namespace=self.config.namespace,
            collection=config.collection,
            uri=config.uri,
            database=config.database,
            token=secret(config.token_env),
            ca_file=str(config.ca_file),
            server_name=config.server_name,
        )
        self.owned.append(vectors)
        self.acquired.append((vectors, True))
        return vectors

    def cache_factory(
        self,
        uow: MetadataUnitOfWork,
        identity: Identity,
        memories: MemoryReadPort,
    ) -> RedisExecutor:
        reader = getattr(memories, "read_authority", None)
        if not callable(reader):
            raise ValueError("Ceph/Redis scheduling requires an authoritative memory reader")
        return RedisExecutor(uow, identity, memories, self.cache, authority_reader=reader)

    def runtime_options(self) -> dict[str, Any]:
        return {
            "uow": self.metadata,
            "telemetry": self.telemetry,
            "p2": self.objects,
            "body_cache": self.cache,
            "vectors_factory": self.vectors_factory,
            "cache_factory": self.cache_factory,
        }

    def transfer_runtime_ownership(self) -> None:
        self.transferred = True

    def close(self) -> None:
        if self.closed:
            return
        failure: BaseException | None = None
        for resource, owned in reversed(self.acquired):
            if owned and self.transferred:
                continue
            try:
                resource.close()
            except BaseException as exc:
                failure = failure or exc
        self.closed = True
        if failure is not None:
            raise failure
