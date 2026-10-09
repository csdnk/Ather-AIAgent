"""Real stores and Temporal prove >100 result admission with a controlled model."""

import asyncio
import json
from types import SimpleNamespace

from remember_candidate_support import ControlledManager
from remember_helpers import app as app
from remember_helpers import context, facts, source

from aether_agent_memory.remember.basic.official_langmem import OfficialLangMemConsolidation
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.models import RememberRequest, TextInput
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from temporal_test_support import http_execution


def test_105_final_memories_commit_then_finish_deferred_projection(app):
    class EveryFact(ControlledManager):
        async def ainvoke(self, payload, **kwargs):
            body = json.loads(payload["messages"][0]["content"])
            if "sources" not in body:
                return await super().ainvoke(payload, **kwargs)
            self.extraction_inputs.append(body)
            return [
                SimpleNamespace(
                    id="candidate-" + str(index),
                    content={
                        "text": fragment["text"].strip(),
                        "kind": "episodic",
                        "evidence_ids": [fragment["evidence_id"]],
                    },
                )
                for index, fragment in enumerate(
                    f for source in body["sources"] for f in source["fragments"]
                )
                if fragment["text"].strip()
            ]

    model = EveryFact()
    app.remember.extraction = OfficialLangMemConsolidation(
        model, "controlled-fanout", extraction_manager=model, decision_manager=model
    )
    # Distinct measurements; not repeated padding and no compression-quality claim.
    statements = [f"Station {i:03d} measured {100 + i * 7} milliseconds." for i in range(105)]
    original = "\n".join(statements)
    assert len(original.encode()) < 8000
    assert len(set(statements)) == 105

    async def exercise():
        # Exercise the deployed public timer, not Remember's convenience sweep.
        service = http_execution(app, app.execution.endpoint)
        try:
            await service.start()
            receipt = await app.remember.save(
                context(app),
                RememberRequest(
                    source=source(),
                    selection=ScopeSelector(session_id="session_1"),
                    content=TextInput(kind="text", text=original),
                    trigger="remember",
                ),
            )
            # Hundreds of real, serializable Azure transactions and consumer
            # workflows need a convergence budget, not a production latency SLO.
            deadline = asyncio.get_running_loop().time() + 1800
            while True:
                status = await asyncio.to_thread(
                    app.remember.processing, context(app), receipt.memories[0].memory_id
                )
                if status["state"] in {"completed", "failed"}:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(1)
            if status["state"] == "completed":
                await service.drain(timeout_seconds=300)
            return receipt, status
        finally:
            if service.client is not None:
                await service.client.get_workflow_handle(
                    f"p3/{service.ledger.config.deployment_id}/periodic"
                ).terminate(reason="isolated fanout regression complete")
            await service.stop()

    receipt, status = asyncio.run(exercise())
    if status["state"] != "completed":
        task_ids = {task["task_id"] for task in status["tasks"]}
        with app.foundation.uow.transaction() as tx:
            details = {
                "status": status,
                "task_envelopes": [
                    {key: row.get(key) for key in ("record", "terminal_reason")}
                    for key, row in tx.rows("tasks")
                    if key in task_ids and row["record"]["state"] not in {"succeeded", "cancelled"}
                ],
                "stage_failures": [
                    row
                    for _, row in tx.rows("remember_stage_failures")
                    if row["task_id"] in task_ids
                ],
                "steps": {
                    key: row
                    for key, row in tx.rows("temporal_steps")
                    if key.split(":")[0] in task_ids
                },
                "capacity_splits": tx.rows("remember_candidate_extraction_splits"),
            }
        print("FANOUT_DIAGNOSTICS=" + json.dumps(details, ensure_ascii=False, default=str))
    assert status["state"] == "completed", json.dumps(status, ensure_ascii=False)
    final_refs = facts(app, receipt)
    assert len(final_refs) == 105
    with app.foundation.uow.transaction() as tx:
        final = [app.remember.current(tx, ref.memory_id) for ref in final_refs]
        assert {item.content for item in final} == set(statements)
        assert all(item.projection_state == "ready" for item in final)
        intents = [
            row
            for _, row in tx.rows("remember_projection_dispatch")
            if row["memory"]["memory_id"] in {ref.memory_id for ref in final_refs}
        ]
        assert len(intents) == 105
        assert all(row["state"] == "admitted" for row in intents)
        assert len({row["task_id"] for row in intents}) == 105
        assert all(tx.read("remember_outbox", row["task_id"]) for row in intents)
        assert tx.read("remember_pending", receipt.memories[0].memory_id)["state"] == "processed"
        assert tx.rows("temporal_ticks"), "public periodic workflow never executed"
        assert any(row.get("dispatch_task_id") for _, row in tx.rows("remember_pending"))
        deferred_events = [row for _, row in tx.rows("remember_event_dispatch")]
        assert deferred_events and all(row["state"] == "admitted" for row in deferred_events)
        deliveries = [row for _, row in tx.rows("deliveries")]
        assert deliveries and all(row["state"] == "acknowledged" for row in deliveries)
        unsuccessful = [
            {"task_id": key, "state": row["record"]["state"], "reason": row.get("terminal_reason")}
            for key, row in tx.rows("tasks")
            if row["record"]["state"] not in {"succeeded", "cancelled"}
        ]
        assert not unsuccessful, json.dumps(unsuccessful, ensure_ascii=False)
        assert tx.get(memory_ref(receipt.memories[0], versioned=True))["ref"] == receipt.memories[
            0
        ].model_dump(mode="json")
    assert app.foundation.tasks.pending_limit == 100
    assert app.foundation.tasks.pending_limit_per_tenant == 300
