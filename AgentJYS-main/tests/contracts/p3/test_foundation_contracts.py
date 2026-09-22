"""Cross-flow failure examples, without claiming provider execution or atomic commits."""

import copy
import json

import pytest
from catalog import ROOT, models
from pydantic import ValidationError

MODELS = models()
EXAMPLES = {
    case["model"]: case["payload"]
    for case in json.loads((ROOT / "contracts/p3/fixtures/cases.json").read_text("utf-8"))
    if case["valid"]
}


def example(key):
    return copy.deepcopy(EXAMPLES[key])


def parse(key, value):
    return MODELS[key].model_validate_json(json.dumps(value, ensure_ascii=False))


@pytest.mark.parametrize(
    ("key", "path", "value"),
    [
        ("runtime.NodeLogRecord", ("elapsed_ms",), None),
        ("runtime.RuntimeHealthSnapshot", ("observations", 0, "state"), "unknown"),
        (
            "runtime.RuntimeHealthSnapshot",
            ("checked_at",),
            "2026-09-21T02:02:00.000Z",
        ),
        ("runtime.SignalObservation", ("state",), "stale"),
        ("runtime.SignalObservation", ("labels",), {"tenant_id": "unbounded"}),
        ("runtime.WorkerHeartbeat", ("lease_token",), None),
        ("runtime.OperationDefinition", ("recovery",), "resume"),
        ("runtime.IncidentRecord", ("verification_refs",), []),
        ("runtime.TaskWaitRecord", ("resume_mode",), "resume"),
        ("runtime.BackupManifest", ("restore_evidence",), []),
        ("remember.SourceRecord", ("original_location", "content_hash"), "0" * 64),
        ("remember.ProcessingRecord", ("stage",), "extract"),
        ("remember.ExtractionDecision", ("candidate", "evidence_status"), "insufficient"),
        ("remember.ProjectionManifest", ("chunks", 0, "verified"), False),
        ("remember.ProjectionManifest", ("expected_chunk_count",), 2),
        ("remember.MemoryRecord", ("projection", "memory", "version"), 9),
        ("remember.FullBodyReadResult", ("content",), "only a fragment"),
        ("remember.FullBodyReadResult", ("outcome",), "excluded"),
        ("remember.ReferenceHandoff", ("new_location", "kind"), "vector"),
        ("remember.ReferenceHandoffReceipt", ("state",), "unknown"),
        ("remember.CleanupReceipt", ("released_protection_ids",), []),
        ("remember.CleanupReceipt", ("results", 0, "outcome"), "unknown"),
        ("recall.MemoryCandidate", ("hits", 0, "generation"), "old_generation"),
        ("recall.MemoryCandidate", ("best_score",), 1.8),
        ("recall.MemorySearchResult", ("examined_chunk_hits",), 999),
        ("recall.MemorySearchResult", ("stop_reason",), "deadline"),
        ("remember.ChunkProjectionRequest", ("vector",), [1.0]),
        ("remember.ChunkProjectionResult", ("searchable",), False),
        ("recall.ChunkSearchResult", ("hits", 0, "model_space"), "other_space"),
        ("recall.RecallPlanRequest", ("sources",), ["working"]),
        ("recall.RankingEvidence", ("rrf_contribution",), 0.9),
        ("recall.ContextAssemblyPlan", ("tokens_used",), 1025),
        ("recall.ContextAssemblyPlan", ("rendered_context",), "summary"),
        ("recall.ContextCommitRequest", ("final_guards",), []),
        ("recall.ContextCommitRequest", ("final_guards", 0, "relations_revision"), 2),
        ("recall.ContextCommitRequest", ("final_guards", 0, "authorization_epoch"), 2),
        ("operate.SchedulingRound", ("coverage",), "unknown"),
        ("operate.BufferEntry", ("last_read_at",), None),
        ("operate.MigrationPlan", ("handoff",), None),
        ("operate.MigrationPlan", ("protection", "owner_action_id"), "other_action"),
    ],
)
def test_rejects_broken_handoffs(key, path, value):
    payload = example(key)
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        parse(key, payload)


def test_many_hit_chunks_still_occupy_one_memory_slot():
    result = example("recall.MemorySearchResult")
    result["request"]["memory_top_k"] = 1
    result["stop_reason"] = "top_k"
    candidate = result["candidates"][0]
    chunk = copy.deepcopy(candidate["manifest"]["chunks"][0])
    chunk.update(chunk_index=1, vector_id="b" * 64)
    candidate["manifest"]["chunks"].append(chunk)
    candidate["manifest"]["expected_chunk_count"] = 2
    hit = copy.deepcopy(candidate["hits"][0])
    hit.update(chunk_index=1, vector_id="b" * 64, score=0.8, rank=2)
    candidate["hits"].append(hit)
    result["examined_chunk_hits"] = 2
    parsed = parse("recall.MemorySearchResult", result)
    assert len(parsed.candidates) == 1
    assert parsed.candidates[0].best_score == 0.9
    result["candidates"].append(copy.deepcopy(candidate))
    result["candidates"][1]["rank"] = 2
    result["request"]["memory_top_k"] = 2
    result["examined_chunk_hits"] = 4
    with pytest.raises(ValidationError, match="Top K slots"):
        parse("recall.MemorySearchResult", result)


def test_required_conflict_members_are_atomic_but_do_not_consume_primary_k():
    plan = example("recall.ContextAssemblyPlan")
    plan["request"]["long_term_search"]["memory_top_k"] = 1
    unit = plan["units"][0]
    second = copy.deepcopy(unit["bodies"][0])
    second["memory"]["memory_id"] = "related_memory"
    second["guard"]["memory"]["memory_id"] = "related_memory"
    unit["bodies"].append(second)
    unit["conflict"] = dict(
        group_id=unit["group_id"],
        members=[b["memory"] for b in unit["bodies"]],
        explanation="Both facts are necessary",
        state="unresolved",
    )
    parsed = parse("recall.ContextAssemblyPlan", plan)
    assert len(parsed.units[0].bodies) == 2
    assert len(parsed.units[0].primary_memories) == 1
    unit["bodies"].pop()
    with pytest.raises(ValidationError, match="conflict group must be complete"):
        parse("recall.ContextAssemblyPlan", plan)


def test_working_only_and_empty_degraded_plan_are_representable():
    plan = example("recall.ContextAssemblyPlan")
    plan["request"].update(sources=["working"], long_term_search=None)
    plan["rank_evidence"] = []
    plan["units"][0]["primary_memories"] = []
    parse("recall.ContextAssemblyPlan", plan)
    plan.update(units=[], rendered_context="", tokens_used=0, degradation_reasons=["read_timeout"])
    parse("recall.ContextAssemblyPlan", plan)


def test_unknown_cleanup_remains_queryable_and_cannot_claim_completion():
    receipt = example("remember.CleanupReceipt")
    receipt.update(state="unknown", released_protection_ids=[])
    receipt["results"][0].update(outcome="unknown", evidence_ref=None)
    parse("remember.CleanupReceipt", receipt)
    receipt["state"] = "completed"
    with pytest.raises(ValidationError):
        parse("remember.CleanupReceipt", receipt)


def test_unknown_handoff_keeps_old_copy_protected():
    plan = example("operate.MigrationPlan")
    plan["handoff"].update(state="unknown", resulting_revision=None)
    plan["protection"].update(state="active", released_at=None)
    plan["old_copy_cleanup"] = "blocked"
    parse("operate.MigrationPlan", plan)
    plan["old_copy_cleanup"] = "eligible"
    with pytest.raises(ValidationError, match="cleanup waits"):
        parse("operate.MigrationPlan", plan)


def test_state_signal_and_rule_can_exchange_the_same_value_type():
    observation = example("runtime.SignalObservation")
    observation["signal"]["unit"] = "state"
    observation["value"] = "unavailable"
    parse("runtime.SignalObservation", observation)
    rule = example("runtime.DispositionRule")
    rule.update(comparator="eq", threshold="unavailable")
    parse("runtime.DispositionRule", rule)
    rule["comparator"] = "gt"
    with pytest.raises(ValidationError, match="equality"):
        parse("runtime.DispositionRule", rule)
    observation["value"] = 1
    with pytest.raises(ValidationError, match="state signals"):
        parse("runtime.SignalObservation", observation)
