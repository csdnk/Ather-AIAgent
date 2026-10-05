"""Recover original runtime references without storing bodies or replaying effects."""

from uuid import uuid4

import httpx
import pytest

from aether_agent_memory.remember.contracts.models import MemoryRef, RememberReceipt, SourceRef
from aether_agent_memory.runtime.contracts.client_runs import ClientOperation, ClientRunRecord
from aether_agent_memory.runtime.contracts.models import Scope
from aether_p4_simulator.demo.execution import StoryRun
from aether_p4_simulator.demo.runner import run_library
from aether_p4_simulator.validation.client import P3ValidationClient

from .support import NOW, Upstream, fixed_definition


def owned():
    scope = Scope(
        tenant_id="t",
        application_id="app",
        user_id="u",
        agent_id="a",
        task_id="p4r_" + "a" * 32,
        session_id="p4r_" + "a" * 32 + "_session",
    )
    ref = MemoryRef(memory_id="m1", version=1, scope=scope)
    source = SourceRef(source_id="s1", source_version=1, content_hash="b" * 64, locator="p2:source")
    receipt = RememberReceipt(
        operation_id="save",
        saved=True,
        memories=(ref,),
        source=source,
        task_ids=("project",),
        phase="processing",
    )
    intent = ClientOperation.prepare(
        method="POST",
        path="/p3/remember",
        operation_id="save",
        request_hash="c" * 64,
        target="/p3/remember",
        content_type="application/json",
    )
    record = ClientRunRecord(
        run_id=uuid4(),
        scenario_id="library-full",
        scope_id=scope.task_id,
        owner_id="owner",
        auth_epoch=1,
        revision=4,
        updated_at=NOW,
        definition={"format_id": "p4_fixed_story_v1", "content_hash": "d" * 64, "size_bytes": 50},
        scope_policy="p4_task_v1",
        state_policy="p4_state_v1",
        snapshot={
            "state": "running",
            "operations": [
                intent.model_copy(update={"phase": "observed", "status_code": 200}).model_dump(
                    mode="json"
                )
            ],
        },
    )
    return scope, ref, source, receipt, record


def test_state_preserves_original_receipt_and_advanced_ref_for_real_consumer():
    from aether_p4_simulator.demo.state import ExecutionData, RecallHandle

    scope, old, source, receipt, record = owned()
    new = old.model_copy(update={"version": 2})
    data = ExecutionData(
        scope=scope,
        refs={"m1": new},
        sources={"s1": source},
        receipts={"rules": receipt},
        episodes={"rules": (old,)},
        recalls={"old": RecallHandle(recall_id="recall-old", scope=scope)},
        baselines={"before_distill": {"m1": old}},
        task_ids={"project", "distill"},
        task_slots={"distill": "distill"},
        task_groups={"cleanup": ["project"]},
        step=7,
    )
    state = data.envelope(record)
    restored = ExecutionData.from_envelope(state, record)
    assert restored.refs[restored.receipts["rules"].memories[0].memory_id].version == 2
    assert restored.receipts["rules"].memories[0].version == 1
    assert restored.baselines["before_distill"]["m1"].version == 1
    assert restored.task_slots["distill"] == "distill"
    assert restored.task_groups["cleanup"] == ["project"]
    calls = []

    def reply(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(410, json={"code": "RESULT_INVALIDATED"})

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(reply))
    run = StoryRun(
        client,
        record.scope_id,
        "library-full",
        None,
        lambda _: None,
        definition=fixed_definition("library-full"),
        execution=restored,
    )
    try:
        with run.step(8, "/p3/recalls/{recall_id}/result", method="GET"):
            run.old_result_invalid(restored.recalls["old"])
        assert calls == [("GET", "/p3/recalls/recall-old/result")]
    finally:
        client.close()


@pytest.mark.parametrize(
    "changed", ["scope", "ref_key", "source", "receipt_version", "task", "body", "unknown_result"]
)
def test_invalid_runtime_state_is_rejected_before_use(changed):
    from aether_p4_simulator.demo.state import ExecutionData

    scope, ref, source, receipt, record = owned()
    raw = ExecutionData(
        scope=scope,
        refs={"m1": ref},
        sources={"s1": source},
        receipts={"rules": receipt},
        task_ids={"project"},
    ).model_dump(mode="json")
    if changed == "scope":
        raw["refs"]["m1"]["scope"]["task_id"] = "foreign"
    elif changed == "ref_key":
        raw["refs"] = {"other": raw["refs"]["m1"]}
    elif changed == "source":
        raw["sources"] = {}
    elif changed == "receipt_version":
        raw["receipts"]["rules"]["memories"][0]["version"] = 3
    elif changed == "task":
        raw["task_slots"] = {"distill": "unknown"}
    elif changed == "body":
        raw["recalls"] = {
            "old": {
                "recall_id": "old",
                "scope": scope.model_dump(mode="json"),
                "rendered_context": "old secret body",
            }
        }
    else:
        raw["resolved"] = {
            "unregistered": {
                "operation_id": "unregistered",
                "result_type": "TaskData",
                "parsed_hash": "a" * 64,
                "basis": "typed_response",
                "consumed": True,
            }
        }
    with pytest.raises(ValueError):
        ExecutionData.model_validate(raw).envelope(record)


def test_basic_runner_retains_receipts_and_lightweight_recalls_after_steps():
    from aether_p4_simulator.demo.state import ExecutionData

    upstream = Upstream()
    data = ExecutionData()
    snapshots = []
    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    try:
        run_library(
            client,
            "demo_unit",
            lambda _: None,
            definition=fixed_definition(),
            execution=data,
            save_state=lambda: snapshots.append(data.snapshot()),
        )
    finally:
        client.close()
    assert set(data.receipts) == {"rules", "loan", "noise"}
    assert len(data.refs) == 3 and data.completed_steps == [1, 2, 3, 4, 5, 6]
    assert data.recalls["last"].recall_id == "recall_3"
    assert all(value.consumed for value in data.resolved.values())
    assert any(s.phase == "parsed" and not s.resolved[s.operation_id].consumed for s in snapshots)
    assert all("真实P3上下文" not in s.model_dump_json() for s in snapshots)


def test_learning_retains_baseline_episodes_and_original_task_across_steps():
    from aether_p4_simulator.demo.stories import learning

    from .test_story_distill import story

    with story("new") as (run, _):
        checkpoints = []
        run.save_state = lambda: checkpoints.append(run.execution.snapshot())
        learning(run)
        assert run.execution.task_slots["distill"] == "distill_task"
        assert len(run.execution.episodes["distill"]) == 3
        assert run.execution.baselines["before_distill"]["semantic_old"].version == 1
        assert "semantic_new" not in run.execution.baselines["before_distill"]
        before_result = next(
            row for row in checkpoints if row.step == 7 and row.phase == "step_complete"
        )
        assert before_result.task_slots["distill"] == "distill_task"
        assert "semantic_new" not in before_result.refs
        assert all(value.consumed for value in run.execution.resolved.values())
