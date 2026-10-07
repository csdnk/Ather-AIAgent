"""Deployment-scoped, operator-authenticated P3 read diagnostics."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import FastAPI, Query, Response

from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.models import MemoryKind, MemoryRef, MemoryStatus
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Flow,
    Identifier,
    PageRequest,
    RecordRef,
    TaskState,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, now
from aether_agent_memory.runtime.foundation.content_diagnostics import (
    authorize_content_read,
    authorize_operator,
    diagnostic_read,
)
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.temporal.models import WorkflowBinding


def fields(value: dict[str, Any] | None, names: str) -> dict[str, Any]:
    return {key: value[key] for key in names.split() if value is not None and key in value}


class AdminDiagnostics:
    def __init__(self, runtime: Any, execution: Any) -> None:
        self.runtime, self.execution = runtime, execution
        self.identity, self.uow = runtime.foundation.identity, runtime.foundation.uow

    @contextmanager
    def access(
        self, ctx: Any, tenant: str, user: str, resource: str, resource_id: str | None
    ) -> Iterator[None]:
        outcome = "read"
        try:
            with diagnostic_read(self.identity, ctx, tenant, user):
                yield
        except BaseException as exc:
            outcome = str(exc.code) if isinstance(exc, FoundationError) else "unavailable"
            raise
        finally:
            # A distinct access fact survives a rejected read. Never copy content,
            # credential, caller-supplied purpose or provider exception messages.
            with self.uow.transaction() as tx:
                tx.write(
                    "admin_content_access",
                    secrets.token_hex(16),
                    {
                        "operator_id": ctx.principal.principal_id,
                        "request_id": ctx.request_id,
                        "tenant_id": tenant,
                        "user_id": user,
                        "resource": resource,
                        "resource_id": resource_id,
                        "purpose": "operations_diagnosis",
                        "outcome": outcome,
                        "observed_at": now(),
                    },
                )

    def metadata(self, tx: Any, ctx: Any, memory_id: str) -> dict[str, Any]:
        pointer = tx.read("remember_current", memory_id)
        raw = tx.get(RecordRef.model_validate(pointer)) if pointer else None
        if raw is None:
            raise FoundationError(ErrorCode.NOT_FOUND, "memory not found")
        ref = MemoryRef.model_validate(raw["ref"])
        authorize_content_read(self.identity, tx, ctx, memory_ref(ref))
        if ref.memory_id != memory_id or memory_ref(
            ref, versioned=True
        ) != RecordRef.model_validate(pointer):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "memory pointer differs")
        return dict(raw)

    async def retained_body(
        self, ctx: Any, raw: dict[str, Any], max_chars: int | None = None
    ) -> dict[str, Any]:
        """Operator-only inspection, independent of business recall eligibility."""
        ref = MemoryRef.model_validate(raw["ref"])
        with self.uow.transaction() as tx:
            authorize_operator(self.identity, tx, ctx, ref.scope.tenant_id)
            if self.metadata(tx, ctx, ref.memory_id) != raw or raw["status"] not in {
                "archived",
                "deleted",
            }:
                raise FoundationError(ErrorCode.RESULT_INVALIDATED, "memory changed during read")
        content, reason = None, "retained_body"
        try:
            if "body_location" in raw:
                location = ResourceLocation.model_validate(raw["body_location"])
                bodies = self.runtime.remember.bodies
                content = (
                    await bodies.read_prefix(location, raw["body_chars"], max_chars)
                    if max_chars is not None
                    else await bodies.read_authority(location)
                )
            else:
                content = raw["content"]
                if text_hash(content) != raw["content_hash"]:
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "body hash differs")
                if max_chars is not None:
                    content = content[:max_chars]
        except FoundationError as exc:
            if exc.code not in {
                ErrorCode.NOT_FOUND,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                ErrorCode.CONTRACT_VIOLATION,
            }:
                raise
            reason = {
                ErrorCode.NOT_FOUND: "retained_body_missing",
                ErrorCode.CONTRACT_VIOLATION: "retained_body_invalid",
            }.get(exc.code, "retained_body_unavailable")
            content = None
        with self.uow.transaction() as tx:
            authorize_operator(self.identity, tx, ctx, ref.scope.tenant_id)
            if self.metadata(tx, ctx, ref.memory_id) != raw:
                raise FoundationError(ErrorCode.RESULT_INVALIDATED, "memory changed during read")
        return {
            "content": content,
            "outcome": "read" if content is not None else "unavailable",
            "reason_code": reason,
            "path": "authority" if content is not None else "none",
        }

    async def memories(
        self,
        ctx: Any,
        tenant_id: str,
        user_id: str,
        limit: int = 50,
        cursor: str | None = None,
        kind: MemoryKind | None = None,
        status: MemoryStatus | None = None,
        include_summary: bool = True,
        collapse_duplicates: bool = False,
    ) -> dict[str, Any]:
        with self.access(ctx, tenant_id, user_id, "memory_catalog", None):
            with self.uow.transaction() as tx:
                groups: dict[Any, list[str]] = {}
                scope = ctx.principal.home_scope
                for key, pointer in tx.rows("remember_current"):
                    ref = RecordRef.model_validate(pointer)
                    if (
                        ref.scope.tenant_id,
                        ref.scope.user_id,
                        ref.scope.application_id,
                        ref.scope.agent_id,
                    ) != (tenant_id, user_id, scope.application_id, scope.agent_id):
                        continue
                    raw = tx.get(ref)
                    if (
                        raw is None
                        or (kind is not None and raw["kind"] != kind)
                        or (status is not None and raw["status"] != status)
                    ):
                        continue
                    digest = raw.get("content_hash") or raw.get("body_location", {}).get(
                        "content_hash"
                    )
                    group = (
                        (raw["kind"], raw["status"], digest)
                        if collapse_duplicates and digest
                        else key
                    )
                    groups.setdefault(group, []).append(key)
                values = [(min(ids), sorted(ids)) for ids in groups.values()]
                items, following = tx.page(
                    values,
                    [
                        "admin_memory_catalog",
                        ctx.principal.model_dump(mode="json"),
                        tenant_id,
                        user_id,
                        kind,
                        status,
                        collapse_duplicates,
                    ],
                    PageRequest(limit=limit, cursor=cursor),
                )
            columns = (
                "ref kind status revision object_revision "
                "projection_state created_at expires_at importance"
            )
            selected = []
            for ids in items:
                with self.uow.transaction() as tx:
                    raw = self.metadata(tx, ctx, ids[0])
                    item = fields(raw, columns)
                    if collapse_duplicates:
                        item.update(
                            duplicate_count=len(ids),
                            duplicate_items=[
                                fields(self.metadata(tx, ctx, mid), columns) for mid in ids[:20]
                            ]
                            if len(ids) > 1
                            else [],
                            duplicates_truncated=len(ids) > 20,
                        )
                item.update(summary=None, summary_status="pending")
                selected.append(item)
            if include_summary:
                semaphore = asyncio.Semaphore(4)

                async def preview(item: dict[str, Any]) -> None:
                    async with semaphore:
                        try:
                            if item["status"] in {"archived", "deleted"}:
                                with self.uow.transaction() as tx:
                                    raw = self.metadata(tx, ctx, item["ref"]["memory_id"])
                                if (
                                    raw["ref"] != item["ref"]
                                    or raw["object_revision"] != item["object_revision"]
                                ):
                                    raise FoundationError(
                                        ErrorCode.RESULT_INVALIDATED, "memory changed"
                                    )
                                body = await asyncio.wait_for(
                                    self.retained_body(ctx, raw, 240), timeout=3
                                )
                                item.update(
                                    summary=body["content"], summary_status=body["reason_code"]
                                )
                                return
                            result = await asyncio.wait_for(
                                self.runtime.remember.preview(
                                    ctx, MemoryRef.model_validate(item["ref"]), 240
                                ),
                                timeout=3,
                            )
                            item.update(
                                summary=result.get("content"), summary_status=result["status"]
                            )
                        except TimeoutError:
                            item.update(
                                summary=None,
                                summary_status="unavailable",
                                summary_error_code="DEADLINE_EXCEEDED",
                            )
                        except FoundationError as exc:
                            if exc.code in {ErrorCode.FORBIDDEN, ErrorCode.DEADLINE_EXCEEDED}:
                                raise
                            item.update(
                                summary=None,
                                summary_status="unavailable",
                                summary_error_code=str(exc.code),
                            )

                workers = [asyncio.create_task(preview(item)) for item in selected]
                try:
                    await asyncio.wait_for(asyncio.gather(*workers), timeout=8)
                except TimeoutError:
                    for item in selected:
                        if item["summary_status"] == "pending":
                            item.update(
                                summary_status="unavailable",
                                summary_error_code="DEADLINE_EXCEEDED",
                            )
                finally:
                    for worker in workers:
                        if not worker.done():
                            worker.cancel()
                    await asyncio.gather(*workers, return_exceptions=True)
            return {
                "items": selected,
                "next_cursor": following,
                "status": "available",
                "observed_at": now(),
            }

    async def memory(
        self, ctx: Any, memory_id: str, tenant_id: str, user_id: str
    ) -> dict[str, Any]:
        with self.access(ctx, tenant_id, user_id, "memory", memory_id):
            with self.uow.transaction() as tx:
                raw = self.metadata(tx, ctx, memory_id)
            if raw["status"] in {"archived", "deleted"}:
                body = await self.retained_body(ctx, raw)
                return {
                    "memory": fields(
                        raw,
                        "ref kind status revision object_revision "
                        "projection_state created_at expires_at importance",
                    ),
                    "body": body,
                    "sources": [],
                    "processing": None,
                    "placement": None,
                    "read_mode": "retained",
                    "status": "available" if body["outcome"] == "read" else "unavailable",
                    "observed_at": now(),
                }
            ref = MemoryRef.model_validate(raw["ref"])
            body = await self.runtime.remember.read_body(ctx, ref)
            metadata = fields(
                raw,
                "ref kind status revision "
                "object_revision projection_state created_at expires_at importance",
            )
            if body.outcome != "read":
                return {
                    "memory": metadata,
                    "body": fields(
                        body.model_dump(mode="json"), "content outcome reason_code path"
                    ),
                    "sources": [],
                    "processing": None,
                    "placement": None,
                    "status": "excluded",
                    "observed_at": now(),
                }
            sources = []
            for source in body.sources[:8]:
                try:
                    value = await self.runtime.remember.read_source(ctx, source)
                    sources.append(
                        {
                            "source": source.model_dump(mode="json"),
                            "status": "available",
                            **fields(
                                value,
                                "content start_char end_char "
                                "total_chars is_complete representation",
                            ),
                        }
                    )
                except FoundationError as exc:
                    if exc.code in {
                        ErrorCode.FORBIDDEN,
                        ErrorCode.MEMORY_GONE,
                        ErrorCode.RESULT_INVALIDATED,
                    }:
                        raise
                    sources.append(
                        {
                            "source": source.model_dump(mode="json"),
                            "status": "unavailable",
                            "error_code": str(exc.code),
                        }
                    )
            processing = self.runtime.remember.processing(ctx, memory_id)
            # Processing has historical extractor evidence. Return only progress,
            # references and safe reason codes, never arbitrary provider payloads.
            processing = fields(
                processing,
                "state state_basis historical_failed_tasks memory_status projection_state "
                "derived_memory_ids rejected_candidate_count tasks "
                "physical_erasure source_retention",
            )
            with self.uow.transaction() as tx:
                latest = self.metadata(tx, ctx, memory_id)
                eligible = self.runtime.remember.final_guard(tx, ctx, (ref,), "recall").items[0]
                if (
                    eligible.decision != "allowed"
                    or latest["object_revision"] != raw["object_revision"]
                ):
                    raise FoundationError(
                        ErrorCode.RESULT_INVALIDATED, "memory changed during read"
                    )
                key = self.runtime.operate.key(ref)
                view, heat = tx.read("operate_views", key), tx.read("operate_heat", key)
                actions = []
                for _, action in tx.rows("operate_actions"):
                    if action.get("intent", {}).get("decision", {}).get("memory") == ref.model_dump(
                        mode="json"
                    ):
                        actions.append(
                            {
                                **fields(action, "state revision cleanup_state"),
                                **fields(
                                    action["intent"],
                                    "action_id created_at "
                                    "representation_id provider_id provider_mode",
                                ),
                                "decision": fields(
                                    action["intent"]["decision"],
                                    "decision_id outcome current_tier "
                                    "target_tier policy_version storage_watermark access_watermark",
                                ),
                                "feedback": fields(
                                    action.get("feedback"),
                                    "state observed_at provider_operation_id",
                                ),
                            }
                        )
                placement = {
                    "status": "available" if view or heat or actions else "no_records",
                    "input": fields(
                        view,
                        "memory storage_watermark access_watermark "
                        "successful_reads cleanup permanent",
                    ),
                    "heat": fields(
                        heat, "version heat desired cold_since updated_at last_access access_count"
                    ),
                    "actions": actions[-50:],
                }
            return {
                "memory": metadata,
                "body": fields(
                    body.model_dump(mode="json"), "content outcome reason_code path guard"
                ),
                "sources": sources,
                "sources_truncated": len(body.sources) > 8,
                "processing": processing,
                "placement": placement,
                "status": "available",
                "observed_at": now(),
            }

    def recalls(
        self,
        ctx: Any,
        tenant_id: str,
        user_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        with self.access(ctx, tenant_id, user_id, "recall_catalog", None):
            with self.uow.transaction() as tx:
                scope = ctx.principal.home_scope
                values = []
                for recall_id, row in tx.rows("recall_requests"):
                    record = row.get("record", {})
                    target = record.get("scope", {})
                    if tuple(
                        target.get(key)
                        for key in ("tenant_id", "user_id", "application_id", "agent_id")
                    ) != (tenant_id, user_id, scope.application_id, scope.agent_id):
                        continue
                    task = tx.read("tasks", recall_id) or {}
                    created_at = task.get("created_at")
                    item = fields(record, "recall_id state stage result_available")
                    query = row.get("request", {}).get("query")
                    item.update(
                        query=query[:12000] if isinstance(query, str) else None,
                        created_at=created_at,
                        statistics={},
                        candidate_count=None,
                    )
                    # Provider reason strings and cached packs are never list payloads.
                    item["reason_code"] = task.get("record", {}).get("error_code")
                    order = (
                        10**20 - int(datetime.fromisoformat(created_at).timestamp() * 1_000_000)
                        if created_at
                        else 10**20
                    )
                    values.append((f"{order:021d}:{recall_id}", item))
                items, following = tx.page(
                    values,
                    [
                        "admin_recall_catalog",
                        ctx.principal.model_dump(mode="json"),
                        tenant_id,
                        user_id,
                    ],
                    PageRequest(limit=limit, cursor=cursor),
                )
            return {
                "items": items,
                "next_cursor": following,
                "status": "available",
                "observed_at": now(),
            }

    async def recall(
        self, ctx: Any, recall_id: str, tenant_id: str, user_id: str
    ) -> dict[str, Any]:
        with self.access(ctx, tenant_id, user_id, "recall", recall_id):
            record = self.runtime.recall.status(ctx, recall_id)
            result, error, status = None, None, "not_available"
            if record.result_available:
                try:
                    result = self.runtime.recall.result(ctx, recall_id).model_dump(mode="json")
                    status = "available"
                except FoundationError as exc:
                    if exc.code in {ErrorCode.FORBIDDEN, ErrorCode.DEADLINE_EXCEEDED}:
                        raise
                    error = str(exc.code)
                    status = (
                        "invalidated"
                        if exc.code in {ErrorCode.MEMORY_GONE, ErrorCode.RESULT_INVALIDATED}
                        else "unavailable"
                    )
            return {
                "record": record.model_dump(mode="json"),
                "result": result,
                "result_status": status,
                "result_error_code": error,
                "status": "available",
                "observed_at": now(),
            }

    def platform(self, ctx: Any) -> None:
        with self.uow.transaction() as tx:
            authorize_operator(self.identity, tx, ctx)

    def in_deployment(self, row: dict[str, Any] | None) -> bool:
        config = getattr(getattr(self.execution, "ledger", None), "config", None)
        return bool(
            config
            and row
            and row.get("namespace") == config.namespace
            and row.get("job", {}).get("deployment_id") == config.deployment_id
        )

    async def workflow(self, binding: Any, *, periodic: bool = False) -> dict[str, Any]:
        stamp = now()
        gateway = getattr(self.execution, "gateway", None)
        if not binding:
            return {"status": "not_bound", "observed_at": stamp}
        if gateway is None:
            return {
                "status": "unavailable",
                "reason_code": "TEMPORAL_NOT_CONNECTED",
                "observed_at": stamp,
            }
        try:
            result = await asyncio.wait_for(
                gateway.describe_details(
                    WorkflowBinding.model_validate(binding), periodic=periodic
                ),
                timeout=3,
            )
            return {"status": "available", **result, "observed_at": stamp}
        except Exception as exc:
            return {
                "status": "unavailable",
                "reason_code": str(exc.code)
                if isinstance(exc, FoundationError)
                else "TEMPORAL_QUERY_FAILED",
                "observed_at": stamp,
            }

    async def task(self, ctx: Any, task_id: str) -> dict[str, Any]:
        self.platform(ctx)
        with self.uow.transaction() as tx:
            envelope = tx.read("tasks", task_id)
            binding = tx.read("temporal_bindings", task_id)
            if not envelope:
                raise FoundationError(ErrorCode.NOT_FOUND, "task not found")
            if not self.in_deployment(binding):
                raise FoundationError(ErrorCode.FORBIDDEN, "task outside deployment")
            task = fields(
                envelope["record"],
                "task_id revision kind owner_flow state attempt max_attempts "
                "query_attempt effect_status error_code subject next_run_at deadline_at result_ref",
            )
            progress = {
                "wait": fields(
                    tx.read("task_waits", task_id),
                    "dependency_id reason_code wait_started_at next_check_at deadline_at "
                    "original_operation_id effect_status resume_mode",
                ),
                "stages": [
                    fields(v, "stage completed_parts updated_at")
                    for _, v in tx.rows("task_stage_counts")
                    if v["task_id"] == task_id
                ][:100],
                "checkpoints": [
                    fields(v, "stage committed_at config_version output_refs")
                    for _, v in tx.rows("task_checkpoints")
                    if v["task_id"] == task_id
                ][:100],
            }
            trace_id = envelope.get("context", {}).get("trace_id")
            trace_rows = [
                fields(v, "record_id stage reason_code coverage occurred_at subject related")
                for _, v in tx.rows("diagnostics")
                if any(
                    r.get("object_type") == "task" and r.get("object_id") == task_id
                    for r in v.get("related", [])
                )
                and v.get("subject", {}).get("scope") == task.get("subject", {}).get("scope")
            ]
            safe_binding = fields(binding, "namespace workflow_id binding stage ordinal epoch")
        workflow = await self.workflow(binding.get("binding"))
        self.platform(ctx)
        return {
            "task": task,
            "progress": progress,
            "binding": safe_binding,
            "workflow": workflow,
            "traces": {
                "trace_id": trace_id,
                "items": trace_rows[:100],
                "truncated": len(trace_rows) > 100,
            },
            "status": "available",
            "observed_at": now(),
        }

    async def queues(self) -> dict[str, Any]:
        from temporalio.api.enums.v1 import TaskQueueType
        from temporalio.api.taskqueue.v1 import TaskQueue
        from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

        client = getattr(self.execution, "client", None)
        config = getattr(getattr(self.execution, "ledger", None), "config", None)
        if client is None or config is None:
            return {"status": "not_collected", "items": [], "observed_at": now()}
        classes = set(getattr(getattr(self.execution, "workers", None), "execution_classes", ()))
        if not classes:
            classes = set(getattr(self.runtime.foundation.tasks, "class_limits", {})) | {"periodic"}

        async def query(name: str, kind: TaskQueueType.ValueType) -> dict[str, Any]:
            queue = config.task_queue_prefix + "." + name
            row = {
                "task_queue": queue,
                "task_type": "workflow"
                if kind == TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW
                else "activity",
                "observed_at": now(),
            }
            try:
                result = await asyncio.wait_for(
                    client.workflow_service.describe_task_queue(
                        DescribeTaskQueueRequest(
                            namespace=config.namespace,
                            task_queue=TaskQueue(name=queue),
                            task_queue_type=kind,
                            include_task_queue_status=True,
                        ),
                        timeout=timedelta(seconds=5),
                    ),
                    timeout=5,
                )
                row.update(
                    status="available",
                    pollers=[
                        {
                            "identity": p.identity,
                            "last_access_time": p.last_access_time.ToJsonString(),
                        }
                        for p in result.pollers
                    ][:100],
                    pollers_truncated=len(result.pollers) > 100,
                    backlog_count_hint=result.task_queue_status.backlog_count_hint
                    if result.HasField("task_queue_status")
                    else None,
                    backlog_status="approximate"
                    if result.HasField("task_queue_status")
                    else "not_collected",
                )
            except Exception:
                row.update(
                    status="unavailable",
                    pollers=None,
                    backlog_count_hint=None,
                    backlog_status="unknown",
                    reason_code="TEMPORAL_QUERY_FAILED",
                )
            return row

        items = await asyncio.gather(
            *(
                query(name, kind)
                for name in sorted(classes)[:16]
                for kind in (
                    TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
                    TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
                )
            )
        )
        return {
            "status": "available" if all(i["status"] == "available" for i in items) else "partial",
            "namespace": config.namespace,
            "items": items,
            "observed_at": now(),
            "scope_note": "deployment queues; backlog is estimated; absent metrics are unknown",
        }

    async def placements(
        self, ctx: Any, limit: int = 50, cursor: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        from aether_agent_memory.runtime.flows.dashboard import placement_item

        self.platform(ctx)
        config = getattr(getattr(self.execution, "ledger", None), "config", None)
        with self.uow.transaction() as tx:
            bindings = dict(tx.rows("temporal_bindings"))
            actions, counts = {}, {}
            for task_id, intent in tx.rows("operate_task_actions"):
                action_id = intent.get("action_id")
                if not self.in_deployment(bindings.get(task_id)) or action_id in actions:
                    continue
                raw = tx.read("operate_actions", action_id)
                if not raw:
                    continue
                item = placement_item(task_id, raw, tx.read("operate_action_triggers", action_id))
                actions[action_id] = item
                counts[item["result"]] = counts.get(item["result"], 0) + 1
            values = []
            for action_id, item in actions.items():
                if status and item["result"] != status:
                    continue
                try:
                    stamp = datetime.fromisoformat(item["created_at"]).timestamp()
                except (ValueError, TypeError):
                    stamp = 0
                values.append((f"{10**20 - int(stamp * 1_000_000):021d}:{action_id}", item))
            items, following = tx.page(
                values,
                [
                    "admin_placements",
                    ctx.principal.model_dump(mode="json"),
                    config.deployment_id if config else None,
                    status,
                ],
                PageRequest(limit=limit, cursor=cursor),
            )
        self.platform(ctx)
        return {
            "items": items,
            "next_cursor": following,
            "total": len(actions),
            "matching": len(values),
            "by_result": counts,
            "placement_capability": "unsupported"
            if getattr(getattr(self.runtime, "executor", None), "supported_moves", None) == ()
            else "not_declared",
            "status": "available",
            "observed_at": now(),
        }

    def performance(self, ctx: Any) -> dict[str, Any]:
        """Read persisted observations without Temporal polling or business execution."""
        self.platform(ctx)
        from .dashboard import memory_observations
        from .performance_observations import performance_observations

        stamp = now()
        telemetry = self.runtime.foundation.telemetry
        if getattr(telemetry, "backend", None) == "postgresql":
            from .performance_metadata import performance_metadata

            config = getattr(getattr(self.execution, "ledger", None), "config", None)
            working_ids, metrics = performance_metadata(
                telemetry,
                namespace=config.namespace if config else "",
                deployment_id=config.deployment_id if config else "",
                observed_at=stamp,
            )
        else:
            # Engineering stores use a local transaction, with set reads rather than N+1 lookups.
            with self.uow.transaction() as tx:
                deployment_tasks = {
                    task_id
                    for task_id, binding in tx.rows("temporal_bindings")
                    if self.in_deployment(binding)
                }
                working_ids = {
                    row["value"]["ref"]["memory_id"]
                    for _, row in tx.rows("records")
                    if row.get("ref", {}).get("object_type") == "memory"
                    and (row.get("value") or {}).get("kind") == "working"
                }
                metrics = memory_observations(tx, deployment_tasks, stamp)
        # The log pool is independent; never hold the metadata transaction across log I/O.
        try:
            metrics.update(
                performance_observations(
                    self.runtime.foundation.telemetry, stamp, working_memory_ids=working_ids
                )
            )
        except Exception:
            metrics.update(
                embedding={"status": "unavailable", "rate": None},
                working_memory={"status": "unavailable", "p99_ms": None},
            )
        self.platform(ctx)
        return {"memory_observations": metrics, "observed_at": stamp, "status": "available"}

    async def diagnostics(
        self,
        ctx: Any,
        limit: int = 50,
        cursor: str | None = None,
        flow: Flow | None = None,
        state: TaskState | None = None,
    ) -> dict[str, Any]:
        self.platform(ctx)
        config = getattr(getattr(self.execution, "ledger", None), "config", None)
        with self.uow.transaction() as tx:
            values = []
            summary: dict[str, Any] = {
                "total": 0,
                "by_flow": {},
                "by_kind": {},
                "by_state": {},
                "by_effect_status": {},
                "created_last_24h": 0,
                "latest_task_at": None,
            }
            recent_start = datetime.fromisoformat(now()) - timedelta(hours=24)
            bindings = dict(tx.rows("temporal_bindings"))
            deployment_tasks = set()
            for task_id, envelope in tx.rows("tasks"):
                binding = bindings.get(task_id)
                if not self.in_deployment(binding):
                    continue
                deployment_tasks.add(task_id)
                record = envelope["record"]
                summary["total"] += 1
                created = envelope.get("created_at")
                if created:
                    stamp = datetime.fromisoformat(created)
                    summary["created_last_24h"] += int(stamp >= recent_start)
                    latest = summary["latest_task_at"]
                    if latest is None or stamp > datetime.fromisoformat(latest):
                        summary["latest_task_at"] = created
                for output, key in (
                    ("by_flow", "owner_flow"),
                    ("by_kind", "kind"),
                    ("by_state", "state"),
                    ("by_effect_status", "effect_status"),
                ):
                    value = record.get(key, "unknown")
                    summary[output][value] = summary[output].get(value, 0) + 1
                if (flow is not None and record.get("owner_flow") != flow) or (
                    state is not None and record["state"] != state
                ):
                    continue
                row = fields(
                    envelope["record"],
                    "task_id revision kind owner_flow state attempt "
                    "max_attempts effect_status error_code subject next_run_at deadline_at",
                )
                row.update(
                    created_at=envelope.get("created_at"),
                    binding=fields(binding, "namespace workflow_id binding stage ordinal"),
                )
                created = envelope.get("created_at")
                order = (
                    10**20 - int(datetime.fromisoformat(created).timestamp() * 1_000_000)
                    if created
                    else 10**20
                )
                values.append((f"{order:021d}:{task_id}", row))
            items, following = tx.page(
                values,
                [
                    "admin_tasks",
                    ctx.principal.model_dump(mode="json"),
                    config.deployment_id if config else None,
                    flow,
                    state,
                ],
                PageRequest(limit=limit, cursor=cursor),
            )
            periodic_row = (
                tx.read("temporal_periodic_binding", config.deployment_id) if config else None
            )
            periodic = fields(periodic_row, "binding revision")
            periodic.update(
                status="available" if periodic_row else "not_bound",
                next_run_at=None,
                next_run_status="not_observed",
            )
            from aether_agent_memory.runtime.flows.dashboard import memory_observations

            memory_metrics = memory_observations(tx, deployment_tasks, now())
        # Bound live describes to the requested page, not all persisted workflows.
        semaphore = asyncio.Semaphore(5)

        async def describe(item: dict[str, Any]) -> None:
            async with semaphore:
                item["workflow"] = await self.workflow(item["binding"].get("binding"))

        for item in items[10:]:
            item["workflow"] = {"status": "not_checked_on_list", "observed_at": None}

        async def live_page() -> None:
            await asyncio.gather(*(describe(item) for item in items[:10]))

        _, periodic_workflow, queues = await asyncio.gather(
            live_page(), self.workflow(periodic.get("binding"), periodic=True), self.queues()
        )
        periodic["workflow"] = periodic_workflow
        self.platform(ctx)
        summary.update(
            observed_at=now(),
            scope_note="all current deployment bound tasks; independent of list filters",
        )
        return {
            "task_summary": summary,
            "memory_observations": memory_metrics,
            "tasks": {
                "items": items,
                "next_cursor": following,
                "filters": {"flow": flow, "state": state},
                "scope_note": "current deployment bound tasks; page is not a global total",
            },
            "runtime": {
                **fields(
                    getattr(self.execution, "state", {}),
                    "worker identity lanes temporal reason_code worker_restarts",
                ),
                "scope_note": "local P3 process; remote pollers are shown separately",
                "observed_at": now(),
            },
            "periodic": periodic,
            "queue_metrics": queues,
            "status": "available",
            "observed_at": now(),
        }


def attach(app: FastAPI, runtime: Any, execution: Any) -> None:
    service = AdminDiagnostics(runtime, execution)
    dependency = app.state.trusted_dependency

    @app.get("/p3/admin/memories")
    async def memories(
        response: Response,
        tenant_id: Identifier,
        user_id: Identifier,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
        kind: MemoryKind | None = None,
        status: MemoryStatus | None = None,
        include_summary: bool = True,
        collapse_duplicates: bool = False,
        ctx: TrustedContext = dependency,
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return await service.memories(
            ctx,
            tenant_id,
            user_id,
            limit,
            cursor,
            kind,
            status,
            include_summary,
            collapse_duplicates,
        )

    @app.get("/p3/admin/memories/{memory_id}")
    async def memory(
        response: Response,
        memory_id: Identifier,
        tenant_id: Identifier,
        user_id: Identifier,
        ctx: TrustedContext = dependency,
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return await service.memory(ctx, memory_id, tenant_id, user_id)

    @app.get("/p3/admin/recalls")
    async def recalls(
        response: Response,
        tenant_id: Identifier,
        user_id: Identifier,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
        ctx: TrustedContext = dependency,
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(service.recalls, ctx, tenant_id, user_id, limit, cursor)

    @app.get("/p3/admin/recalls/{recall_id}")
    async def recall(
        response: Response,
        recall_id: Identifier,
        tenant_id: Identifier,
        user_id: Identifier,
        ctx: TrustedContext = dependency,
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return await service.recall(ctx, recall_id, tenant_id, user_id)

    @app.get("/p3/admin/diagnostics")
    async def diagnostics(
        response: Response,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
        flow: Flow | None = None,
        state: TaskState | None = None,
        kind: Literal["placement", "performance"] | None = None,
        status: Literal[
            "succeeded", "failed", "pending", "running", "unconfirmed", "simulated", "cancelled"
        ]
        | None = None,
        ctx: TrustedContext = dependency,
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        if kind == "performance":
            return await asyncio.to_thread(service.performance, ctx)
        if kind == "placement":
            return await service.placements(ctx, limit, cursor, status)
        return await service.diagnostics(ctx, limit, cursor, flow, state)

    @app.get("/p3/admin/tasks/{task_id}")
    async def task(
        response: Response, task_id: Identifier, ctx: TrustedContext = dependency
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return await service.task(ctx, task_id)
