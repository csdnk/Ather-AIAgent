"""Shared vector connections and resource lifecycle; no projection or search API."""

import asyncio
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import copy_context
from datetime import datetime
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any, TypeVar

from aether_agent_memory.remember.contracts.models import ProjectionTarget
from aether_agent_memory.runtime.contracts.models import ErrorCode, Flow, RecordRef, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.storage.ports import MetadataUnitOfWork

T = TypeVar("T")


def projection_ref(target: ProjectionTarget) -> RecordRef:
    return RecordRef(
        owner=Flow.REMEMBER,
        object_type="memory",
        object_id=target.memory.memory_id,
        scope=target.memory.scope,
    )


class MilvusConnection:
    def __init__(
        self,
        uow: MetadataUnitOfWork,
        identity: Identity,
        model_space: str,
        dimensions: int,
        *,
        uri: str,
        collection: str = "p3_memories",
        token: str = "",
        client: Any = None,
        serialize_writes: bool = False,
        secure: bool = False,
        ca_file: str | None = None,
        server_name: str | None = None,
        database: str = "default",
    ) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", collection):
            raise ValueError("invalid Milvus collection")
        if (ca_file or server_name) and not secure:
            raise ValueError("Milvus certificate settings require a secure connection")
        if ca_file and not Path(ca_file).is_file():
            raise ValueError("Milvus trusted CA file is missing")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", database):
            raise ValueError("invalid Milvus database name")
        self.uow, self.identity = uow, identity
        self.model_space, self.dimensions, self.collection = model_space, dimensions, collection
        self.projection_namespace = "milvus_projections"
        if client is None:
            from pymilvus import MilvusClient

            options: dict[str, Any] = {"secure": secure, "db_name": database}
            if ca_file:
                options["server_pem_path"] = ca_file
            if server_name:
                options["server_name"] = server_name
            client = MilvusClient(uri=uri, token=token, timeout=10, **options)
        self.client: Any = client
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="p3-milvus")
        self.metadata_executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="p3-vector-meta"
        )
        self.slots = BoundedSemaphore(4)
        self.serialize_writes = serialize_writes
        self.write_lock = Lock()
        self.prepare_lock = asyncio.Lock()
        self.closed = False
        self.prepared = False

    def timeout(self, ctx: TrustedContext) -> float:
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
        seconds = (
            datetime.fromisoformat(ctx.deadline_at.replace("Z", "+00:00"))
            - datetime.fromisoformat(self.identity.clock().replace("Z", "+00:00"))
        ).total_seconds()
        return max(0.001, min(seconds, 10))

    async def metadata(self, operation: Callable[[], T]) -> T:
        """Keep PG waits off the transport loop while carrying execution fences."""
        if self.closed:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Milvus adapter closed")
        return await asyncio.get_running_loop().run_in_executor(
            self.metadata_executor, copy_context().run, operation
        )

    async def call(
        self,
        ctx: TrustedContext,
        method: str,
        *,
        before_call: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> Any:
        timeout = await self.metadata(lambda: self.timeout(ctx))
        if self.closed or not self.slots.acquire(blocking=False):
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Milvus adapter closed or busy")

        def invoke() -> Any:
            mutation = method in {"upsert", "delete", "create_collection", "load_collection"}
            # The SDK may continue after wait_for times out. Keep the lock inside its
            # worker thread until the real call ends, including while the caller cancels.
            with self.write_lock if self.serialize_writes and mutation else nullcontext():
                if self.closed:
                    raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Milvus adapter closed")
                if before_call is not None:
                    before_call()  # Object authorization/intents must survive the queue wait too.
                remaining = self.timeout(ctx)  # Revalidate after waiting for a previous write.
                return getattr(self.client, method)(
                    collection_name=self.collection, timeout=remaining, **kwargs
                )

        try:
            future = self.executor.submit(copy_context().run, invoke)
        except BaseException:
            self.slots.release()
            raise
        future.add_done_callback(lambda _: self.slots.release())
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        try:
            result = await asyncio.wait_for(asyncio.shield(wrapped), timeout)
        except Exception as exc:
            if isinstance(exc, FoundationError):
                raise
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Milvus call failed") from exc
        await self.metadata(lambda: self.timeout(ctx))
        return result

    async def prepare(self, ctx: TrustedContext) -> None:
        if self.prepared:
            return
        async with self.prepare_lock:
            if not self.prepared:
                await self.prepare_collection(ctx)

    async def prepare_collection(self, ctx: TrustedContext) -> None:
        from pymilvus import DataType

        if not await self.call(ctx, "has_collection"):
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("vector_id", DataType.VARCHAR, is_primary=True, max_length=64)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dimensions)
            schema.add_field("target", DataType.JSON)
            schema.add_field("model_space", DataType.VARCHAR, max_length=512)
            for field in (
                "tenant_id",
                "application_id",
                "user_id",
                "agent_id",
                "session_id",
                "task_id",
            ):
                schema.add_field(field, DataType.VARCHAR, max_length=512)
            index = self.client.prepare_index_params()
            index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="IP")
            await self.call(
                ctx,
                "create_collection",
                schema=schema,
                index_params=index,
                consistency_level="Strong",
            )
        description = await self.call(ctx, "describe_collection")
        fields = {f["name"]: f for f in description["fields"]}
        scalar_fields = {
            "model_space",
            "tenant_id",
            "application_id",
            "user_id",
            "agent_id",
            "session_id",
            "task_id",
        }
        expected_types = {
            "vector_id": DataType.VARCHAR,
            "vector": DataType.FLOAT_VECTOR,
            "target": DataType.JSON,
            **dict.fromkeys(scalar_fields, DataType.VARCHAR),
        }
        try:
            dimensions = int(fields.get("vector", {}).get("params", {}).get("dim", 0))
        except (ValueError, TypeError):
            dimensions = 0
        if (
            dimensions != self.dimensions
            or any(
                fields.get(name, {}).get("type") != dtype for name, dtype in expected_types.items()
            )
            or {name for name, field in fields.items() if field.get("is_primary")} != {"vector_id"}
            or description.get("auto_id", False)
            or fields.get("vector_id", {}).get("auto_id", False)
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "Milvus collection schema mismatch")
        vector_indexes = []
        for name in await self.call(ctx, "list_indexes"):
            index = await self.call(ctx, "describe_index", index_name=name)
            if index and index.get("field_name") == "vector":
                vector_indexes.append(index)
        if not vector_indexes or any(index.get("metric_type") != "IP" for index in vector_indexes):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "Milvus vector index requires IP")
        await self.call(ctx, "load_collection")
        self.prepared = True

    async def health(self, ctx: TrustedContext) -> dict[str, object]:
        exists = await self.call(ctx, "has_collection")
        return {"state": "available" if exists else "unknown", "provider_id": "milvus"}

    def close(self) -> None:
        self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.metadata_executor.shutdown(wait=True, cancel_futures=True)
        self.client.close()
