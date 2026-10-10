"""AET-43: real late Milvus reply cannot mutate an already committed Pack."""

from uuid import uuid4

import pytest
from tests.integration.test_recall_projection_states import diagnose
from tests.integration.test_recall_source_failure import (
    policy_evidence,
    positive,
    result,
    seed,
    submit,
)
from tests.integration.test_recall_working_native_search import working_target as _working_target

from aether_agent_memory.recall.contracts.models import ContextPack
from recall_authorization_support import F1_TEXTS, RecallHTTP
from recall_required_sources_support import AccessEventWitness
from recall_source_timing_support import LateSDK, pack_hash

pytestmark = [pytest.mark.integration, pytest.mark.sources, pytest.mark.p1]
working_target = _working_target


@pytest.mark.parametrize(
    "working_target", [{"case_id": "AET-43", "mixed_policy": "allow"}], indirect=True
)
@pytest.mark.parametrize("actor", ["U01", "U06"])
@pytest.mark.parametrize("late_source", ["working", "long_term"])
def test_rc_src_14_late_source_reply_cannot_rewrite_committed_pack(
    working_target, monkeypatch, actor, late_source
):
    client, native, probe, evidence = working_target
    runtime = probe.runtime
    policy = policy_evidence(runtime, evidence)
    assert runtime.recall.settings.rerank_policy == "disabled"
    maintainer, http = RecallHTTP(client, "fixture-maintainer"), RecallHTTP(client, actor)
    session = "late-source-" + uuid4().hex
    memories = seed(maintainer, session, (F1_TEXTS[0],), native)
    positive(http, session, memories, native, probe, evidence, actor)
    witness = AccessEventWitness(runtime, monkeypatch)
    late = LateSDK(runtime, session, late_source)
    evidence.data["source_timeline"] = late.timeline
    healthy = "long_term" if late_source == "working" else "working"
    originals = runtime.vectors.search, runtime.vectors.client.search
    with late.installed(monkeypatch):
        operation_id, job, response = submit(http, session, evidence)
        assert late.entered.wait(10), "real delayed SDK route never executed"
        task, reply = result(http, job, response, evidence)
        late.stamp("terminal_observed", task_state=task["state"], status=reply.status_code)
        evidence.data["executions"].append(
            {"operation_id": operation_id, "job_id": job, "task": task, "status": reply.status_code}
        )
        # This is the allow-partial lane. A total failure does not satisfy the
        # acceptance requirement to protect an already committed healthy Pack.
        assert task["state"] == "succeeded" and reply.status_code == 200
        pack = ContextPack.model_validate(reply.json())
        assert pack.outcome == "degraded" and pack.policy_version == runtime.recall.policy_version
        assert pack.selected_sources == ("working", "long_term")
        assert getattr(pack.coverage, healthy) == "complete"
        assert getattr(pack.coverage, late_source) == "unavailable"
        assert late_source + "_deadline" in pack.degradation_reasons
        items = [item for group in pack.groups for item in group.items]
        assert [item.memory for item in items] == [m.ref for m in memories[healthy]]
        assert all(item.content == memories[healthy][0].content for item in items)
        assert all(item.memory not in {m.ref for m in memories[late_source]} for item in items)
        frozen, digest = reply.json(), pack_hash(reply.json())
        events = witness.observe(operation_id)
        assert events["packed_committed_count"] == events["packed_attempt_count"] == len(items)
        before_record = http.get(f"/p3/recalls/{pack.recall_id}")
        before_result = http.get(f"/p3/recalls/{pack.recall_id}/result")
        assert before_record.status_code == before_result.status_code == 200
        assert before_result.json() == frozen and before_record.json()["result_available"]
        evidence.data["committed_before_release"] = {
            "pack": frozen,
            "hash": digest,
            "record": before_record.json(),
            "events": events,
            "committed_at": pack.committed_at,
        }
        assert not late.returned.is_set(), "late reply returned before commit observation"
        held = next(row for row in late.timeline if row["stage"] == "sdk_reply_held")
        expired = next(
            row for row in late.timeline if row["stage"] == "transport_deadline_exceeded"
        )
        assert held["hit_count"] > 0 and held["monotonic"] < expired["monotonic"]
        late.stamp("release_after_commit", pack_hash=digest)
        late.release.set()
        assert late.returned.wait(5), "late SDK did not return"
        assert late.tracked_future and late.settled.wait(5), "real SDK future did not settle"
        for path in (f"/p3/operations/{job}/result", f"/p3/recalls/{pack.recall_id}/result"):
            replay = http.get(path)
            evidence.response("GET", path, replay)
            assert replay.status_code == 200 and replay.json() == frozen
            assert pack_hash(replay.json()) == digest
        assert http.get(f"/p3/recalls/{pack.recall_id}").json() == before_record.json()
        assert witness.observe(operation_id) == events
        diagnose(http, maintainer, job, actor, evidence)
    assert (runtime.vectors.search, runtime.vectors.client.search) == originals
    positive(http, session, memories, native, probe, evidence, actor)
    replay = http.get(f"/p3/operations/{job}/result")
    assert replay.status_code == 200 and pack_hash(replay.json()) == digest
    assert witness.observe(operation_id) == events
    evidence.data.update(
        control_restored=True, acceptance="passed" if policy["confirmed"] else "limited"
    )
