import json
import time
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

import httpx

from aether_agent_memory.runtime.contracts.client_definitions import (
    ClientDefinitionBinding,
    ClientDefinitionReceipt,
)
from aether_agent_memory.runtime.contracts.client_inputs import ClientInputReceipt
from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from aether_p4_simulator.demo.models import RunSnapshot

from .registry_support import UnitRegistry

NOW = "2026-09-30T00:00:00.000Z"


def fixed_definition(scenario="library-basic"):
    from aether_p4_simulator.demo.definition import RunDefinition

    return RunDefinition.capture(uuid4(), scenario)


class Upstream:
    """Transport boundary with full wire contracts and independently saved data."""

    def __init__(self, variant="success"):
        self.variant = variant
        self.requests = []
        self.memories = {}
        self.sources = {}
        self.processing_reads = 0
        self.recall_count = 0
        self.registry = UnitRegistry()

    def __call__(self, request):
        if request.url.path.startswith("/p3/client-runs/"):
            parts = request.url.path.split("/")
            if len(parts) == 6 and parts[4] == "states":
                record = self.registry.get(parts[3])
                if request.method == "GET":
                    payload = self.registry.states[parts[3], int(parts[5])]
                    return httpx.Response(
                        200,
                        content=payload,
                        headers={"X-P3-State-Hash": sha256(payload).hexdigest()},
                    )
                assert request.headers["X-P3-Run-Owner"] == record.owner_id
                assert request.headers["X-P3-Run-Revision"] == str(record.revision)
                updated = self.registry.save_state(record, request.content)
                return httpx.Response(200, json=updated.model_dump(mode="json"))
            if len(parts) == 5 and parts[4] == "definition":
                record = self.registry.get(parts[3])
                if request.method == "GET":
                    payload = self.registry.read_definition(record)
                    return httpx.Response(
                        200,
                        content=payload,
                        headers={"X-P3-Definition-Hash": sha256(payload).hexdigest()},
                    )
                self.registry.save_definition(record, request.content)
                receipt = ClientDefinitionReceipt(
                    run_id=record.run_id, **record.definition.model_dump()
                )
                return httpx.Response(200, json=receipt.model_dump(mode="json"))
            if len(parts) == 6 and parts[4] == "inputs":
                record = self.registry.get(parts[3])
                intent = next(
                    ClientOperation.model_validate(value)
                    for value in record.snapshot["operations"]
                    if value["operation_id"] == parts[5]
                )
                if request.method == "GET":
                    payload = self.registry.inputs[(parts[3], parts[5])]
                    return httpx.Response(
                        200,
                        content=payload,
                        headers={"X-P3-Request-Hash": sha256(payload).hexdigest()},
                    )
                assert request.headers["X-P3-Run-Owner"] == record.owner_id
                assert request.headers["X-P3-Run-Revision"] == str(record.revision)
                self.registry.save_input(record, intent, request.content)
                receipt = ClientInputReceipt(
                    run_id=record.run_id,
                    operation_id=intent.operation_id,
                    request_hash=intent.request_hash,
                    binding_digest=intent.binding.digest,
                    size_bytes=len(request.content),
                )
                return httpx.Response(200, json=receipt.model_dump(mode="json"))
            run_id = request.url.path.rsplit("/", 1)[1]
            if request.method == "GET":
                return httpx.Response(200, json=self.registry.get(run_id).model_dump(mode="json"))
            body = json.loads(request.content)
            snapshot = RunSnapshot.model_validate(body["snapshot"])
            if request.method == "POST":
                result = self.registry.register(
                    snapshot,
                    body["owner_id"],
                    ClientDefinitionBinding.model_validate(body["definition"])
                    if body.get("definition")
                    else None,
                )
                return httpx.Response(
                    201 if result.created else 200, json=result.model_dump(mode="json")
                )
            record = self.registry.get(run_id)
            result = self.registry.checkpoint(record, snapshot)
            return httpx.Response(200, json=result.model_dump(mode="json"))
        self.requests.append(request)
        path = request.url.path
        if path == "/p3/live":
            return httpx.Response(200, json={"liveness": "alive", "checked_at": NOW})
        if path in {"/p3/health", "/p3/ready"}:
            return httpx.Response(
                200,
                json={
                    "checked_at": NOW,
                    "config_version": "v1",
                    "liveness": "alive",
                    "readiness": "not_ready" if self.variant == "not_ready" else "ready",
                    "required_capabilities": ["recall"],
                    "observations": [
                        {
                            "name": "recall",
                            "category": "capability",
                            "state": "available",
                            "checked_at": NOW,
                            "fresh_until": "2026-10-01T00:00:00.000Z",
                            "elapsed_ms": 1,
                            "reason_code": "probe_ok",
                            "evidence_refs": [],
                        }
                    ],
                },
            )
        if path == "/p3/capabilities":
            return httpx.Response(
                200,
                json={
                    "profile": "local",
                    "embedding": "lexical",
                    "semantic_processing": "literal_baseline",
                    "object_storage": "local_sqlite",
                    "scheduling": "temporal_v1",
                    "executor": "local_filesystem_cache",
                    "operations": ["remember", "recall"],
                },
            )
        if path == "/p3/remember":
            body = json.loads(request.content)
            mid = f"m{len(self.memories) + 1}"
            scope = {
                "tenant_id": "t1",
                "application_id": "a1",
                "user_id": "u1",
                "agent_id": "a1",
                **body["selection"],
            }
            ref = {"memory_id": mid, "version": 1, "scope": scope}
            if self.variant == "bad_receipt_scope":
                ref["scope"]["task_id"] = "other"
            source = {
                "source_id": f"source_{mid}",
                "source_version": 1,
                "content_hash": "a" * 64,
                "locator": "conversation:test",
            }
            self.memories[mid] = (deepcopy(ref), body["content"]["text"])
            self.sources[mid] = source
            return httpx.Response(
                200,
                json={
                    "operation_id": request.headers["X-Operation-ID"],
                    "saved": self.variant != "not_saved",
                    "source": source,
                    "memories": [ref],
                    "task_ids": [f"task_{mid}"],
                    "phase": "processing",
                },
            )
        if path.endswith("/processing"):
            self.processing_reads += 1
            ref = deepcopy(self.memories[path.split("/")[-2]][0])
            if self.variant == "bad_processing_ref":
                ref["memory_id"] = "other"
            ready = self.variant not in {"projection_pending", "projection_failed"}
            projection = (
                "ready"
                if ready
                else ("failed" if self.variant == "projection_failed" else "pending")
            )
            return httpx.Response(
                200,
                json={
                    "memory": ref,
                    "state": "awaiting_consolidation",
                    "state_basis": "latest_task_per_memory_and_kind",
                    "projection_state": projection,
                    "memory_status": "active",
                    "derived_memory_ids": [],
                    "historical_failed_tasks": 1,
                    # Historical failure is not the current projection verdict.
                    "tasks": [
                        {
                            "task_id": "old",
                            "kind": "projection",
                            "state": "failed",
                            "error_code": None,
                            "result_ref": None,
                        }
                    ],
                },
            )
        if path == "/p3/recall":
            self.recall_count += 1
            selection = json.loads(request.content)["selection"]
            current = [
                mid
                for mid, (ref, _) in self.memories.items()
                if ref["scope"]["session_id"] == selection["session_id"]
            ]
            target = current[0] if self.recall_count % 3 == 1 else current[1]
            ref, content = deepcopy(self.memories[target])
            pack_scope = deepcopy(ref["scope"])
            if self.variant == "foreign_scope":
                ref["scope"]["task_id"] = "other"
            if self.variant == "foreign_memory":
                ref["memory_id"] = "another_round"
            if self.variant == "foreign_pack":
                pack_scope["session_id"] = "other"
            if self.variant == "wrong_content":
                content = "无关内容"
            item = {
                "memory": ref,
                "content": content,
                "sources": [self.sources[target]],
                "representation": "original",
            }
            groups = [{"group_id": "g1", "items": [item], "conflict": None}]
            if self.variant == "extra_foreign":
                leaked = deepcopy(item)
                leaked["memory"]["memory_id"] = "another"
                groups.append({"group_id": "g2", "items": [leaked], "conflict": None})
            empty = self.variant == "empty"
            degraded = self.variant == "degraded"
            return httpx.Response(
                200,
                json={
                    "recall_id": f"recall_{self.recall_count}",
                    "scope": pack_scope,
                    "outcome": "empty" if empty else "degraded" if degraded else "available",
                    "selected_sources": ["working"],
                    "coverage": {
                        "working": "partial" if degraded else "complete",
                        "long_term": "not_requested",
                    },
                    "groups": [] if empty else groups,
                    "rendered_context": "" if empty else f"真实P3上下文：{content}",
                    "token_budget": 2000,
                    "tokens_used": 0 if empty else 50,
                    "tokenizer_id": "lexical",
                    "policy_version": "v1",
                    "degradation_reasons": ["test_incomplete"] if degraded else [],
                    "committed_at": NOW,
                },
            )
        raise AssertionError(f"unexpected route: {request.method} {path}")


def finish(service, run_id):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        snapshot = service.get(run_id)
        if snapshot.state not in {"queued", "running"}:
            return snapshot
        time.sleep(0.005)
    raise AssertionError("service did not finish")
