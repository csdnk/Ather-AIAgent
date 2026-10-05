"""Distill must read its own task output, not claim every unseen catalog row."""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from aether_agent_memory.remember.contracts.models import ReflectionRequest
from aether_p4_simulator.demo.execution import StoryRun
from aether_p4_simulator.demo.stories import learning
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from aether_p4_simulator.validation.models import CapabilitiesData

from ..p4_validation.test_operations import Clock
from .support import NOW, Upstream, fixed_definition

pytestmark = pytest.mark.unit

OLD_TEXT = "长期化时就已存在的语义记忆"
NEW_TEXT = "本次任务返回的真实语义记忆"


class LearningUpstream(Upstream):
    """Keep P3 wire contracts complete; only replace the external HTTP boundary."""

    def __init__(self, variant):
        super().__init__()
        self.case = variant
        self.distilled = False
        self.policy = None

    def ref(self, memory_id):
        working = "m" + memory_id[1:] if memory_id.startswith("e") else memory_id
        base = self.memories.get(working, self.memories["m1"])[0]
        version = 1
        if memory_id == "semantic_old" and self.distilled:
            version = {"update": 2, "missing_supersedes": 2, "version_jump": 3}.get(self.case, 1)
        return {
            **deepcopy(base),
            "memory_id": memory_id,
            "version": version,
        }

    def snapshot(self, memory_id):
        ref = self.ref(memory_id)
        working = "m" + memory_id[1:] if memory_id.startswith("e") else memory_id
        kind = (
            "working"
            if memory_id.startswith("m")
            else "episodic"
            if memory_id.startswith("e")
            else "semantic"
        )
        source_key = working if working in self.sources else "m1"
        content = (
            self.memories[working][1]
            if working in self.memories
            else OLD_TEXT
            if memory_id == "semantic_old" and ref["version"] == 1
            else NEW_TEXT
        )
        return dict(
            ref=ref,
            revision=1,
            object_revision=ref["version"] + 1,
            kind=kind,
            status="active",
            content=content,
            content_hash="b" * 64,
            sources=[self.sources[source_key]],
            projection_state="ready",
            model_space="lexical",
            supersedes={**ref, "version": 1}
            if ref["version"] > 1 and self.case != "missing_supersedes"
            else None,
            created_at=NOW,
        )

    def __call__(self, request):
        path = request.url.path
        if path == "/p3/remember" or path == "/p3/recall":
            response = super().__call__(request)
            if path == "/p3/recall":
                value = response.json()
                value["groups"] = [
                    dict(
                        group_id="group_" + mid,
                        items=[
                            dict(
                                memory=ref,
                                content=content,
                                sources=[self.sources[mid]],
                                representation="original",
                            )
                        ],
                        conflict=None,
                    )
                    for mid, (ref, content) in self.memories.items()
                ]
                value["rendered_context"] = "\n".join(v[1] for v in self.memories.values())
                return httpx.Response(200, json=value)
            return response
        self.requests.append(request)
        if path == "/p3/remember/consolidate":
            value = {"task_ids": []}
        elif path == "/p3/remember/reflection":
            if request.method == "POST":
                self.policy = ReflectionRequest.model_validate_json(request.content).model_dump(
                    mode="json"
                )
            value = dict(
                scope=self.ref("m1")["scope"],
                revision=1 if self.policy else 0,
                status="enrolled" if self.policy else "not_enrolled",
                policy=self.policy,
            )
        elif path == "/p3/remember/distill":
            self.distilled = True
            value = {"task_id": "distill_task"}
        elif path == "/p3/operations/distill_task/result":
            if self.case == "unavailable":
                return httpx.Response(404, json={"code": "NOT_FOUND"})
            output_id = {
                "new": "semantic_new",
                "update": "semantic_old",
                "reuse": "semantic_old",
                "episodic": "e1",
                "foreign": "semantic_new",
                "duplicate": "semantic_new",
                "zero_mismatch": "semantic_new",
                "missing_supersedes": "semantic_old",
                "version_jump": "semantic_old",
            }.get(self.case)
            refs = [self.ref(output_id)] if output_id else []
            if self.case == "foreign":
                refs[0]["scope"]["task_id"] = "other_run"
            if self.case == "duplicate":
                refs *= 2
            value = dict(
                memories=refs,
                candidate_count=len(refs),
                zero_output=True if self.case == "zero_mismatch" else not refs,
                awaiting_extraction_provider=["e1"] if self.case == "deferred" else [],
            )
            if self.case == "malformed":
                value["zero_output"] = "true"
        elif path.startswith("/p3/tasks/"):
            task_id = path.split("/")[3]
            if path.endswith("/progress"):
                value = dict(wait=None, stages=[], checkpoints=[])
            else:
                memory_id = "e1" if task_id == "distill_task" else task_id.removeprefix("task_")
                subject = dict(
                    owner="remember",
                    object_type="memory",
                    object_id=memory_id,
                    scope=self.ref(memory_id)["scope"],
                )
                value = dict(
                    task_id=task_id,
                    owner_flow="remember",
                    kind="remember.distill" if task_id == "distill_task" else "remember.extract",
                    subject=subject,
                    input_ref=subject,
                    idempotency_key=task_id,
                    input_hash="a" * 64,
                    initiator_id="alice",
                    initiator_auth_epoch=1,
                    deadline_at=NOW,
                    state="succeeded",
                    revision=2,
                    attempt=1,
                    effect_status="confirmed",
                    result_ref={
                        **subject,
                        "object_type": "processing_result",
                        "object_id": task_id,
                    },
                )
                if task_id == "distill_task":
                    if self.case == "wrong_task":
                        value["kind"] = "remember.extract"
                    if self.case == "wrong_result":
                        value["result_ref"]["object_id"] = "other_task"
                    if self.case == "unknown_effect":
                        value["effect_status"] = "unknown"
        elif path == "/p3/memories":
            ids = [*self.memories, "e1", "e2", "e3", "semantic_old"]
            if self.distilled and self.case in {"new", "foreign", "unlinked"}:
                ids.append("semantic_new")
            value = {
                "items": [
                    {k: v for k, v in self.snapshot(mid).items() if k != "content"} for mid in ids
                ],
                "next_cursor": "more" if self.case == "incomplete_catalog" else None,
            }
        elif path.startswith("/p3/operate/memories/"):
            value = {
                "memory": self.ref(path.split("/")[-1]),
                "input": {},
                "actions": [],
                "heat": None,
            }
        elif path.endswith("/processing"):
            memory_id = path.split("/")[-2]
            value = dict(
                memory=self.ref(memory_id),
                state="completed",
                state_basis="latest_task_per_memory_and_kind",
                projection_state="ready",
                memory_status="active",
                derived_memory_ids=["e" + memory_id[1:], "semantic_old"]
                if memory_id.startswith("m")
                else [],
                tasks=[],
                historical_failed_tasks=0,
            )
        elif path.startswith("/p3/remember/"):
            value = self.snapshot(path.split("/")[-1])
        else:
            raise AssertionError(f"Unexpected route {path}")
        return httpx.Response(200, json=value)


@contextmanager
def story(variant):
    upstream = LearningUpstream(variant)
    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    run = StoryRun(
        client,
        "demo_learning",
        "learning-review",
        None,
        lambda _: None,
        definition=fixed_definition("learning-review"),
    )
    try:
        yield run, upstream
    finally:
        client.close()


@pytest.mark.parametrize("variant", ["empty", "unlinked"])
def test_zero_task_output_never_promotes_old_or_unlinked_catalog_content(variant):
    with story(variant) as (run, _):
        learning(run)
        assert run.current_step.state == "passed"
        assert run.evidence.memories == []
        assert OLD_TEXT not in run.current_step.response_text
        assert NEW_TEXT not in run.current_step.response_text
        assert "未产生输出" in run.current_step.response_text


@pytest.mark.parametrize(
    "variant,memory_id,version,label",
    [
        ("new", "semantic_new", 1, "新增 1"),
        ("update", "semantic_old", 2, "更新 1"),
        ("reuse", "semantic_old", 1, "复用 1"),
    ],
)
def test_only_task_linked_semantics_are_shown_with_honest_change_classification(
    variant, memory_id, version, label
):
    with story(variant) as (run, upstream):
        learning(run)
        assert [(ref.memory_id, ref.version) for ref in run.evidence.memories] == [
            (memory_id, version)
        ]
        assert label in run.current_step.response_text
        if variant != "new":
            assert "新增 0" in run.current_step.response_text
        if variant == "new":
            assert OLD_TEXT not in run.current_step.response_text
        assert sum(r.url.path == "/p3/remember/distill" for r in upstream.requests) == 1


@pytest.mark.parametrize(
    "variant",
    [
        "episodic",
        "foreign",
        "unavailable",
        "duplicate",
        "zero_mismatch",
        "deferred",
        "malformed",
        "wrong_task",
        "wrong_result",
        "unknown_effect",
        "missing_supersedes",
        "version_jump",
    ],
)
def test_unproved_or_wrong_kind_outputs_cannot_pass_as_distillation(variant):
    with story(variant) as (run, upstream):
        with pytest.raises(ValidationError):
            learning(run)
        assert run.current_step.state != "passed"
        assert OLD_TEXT not in (run.current_step.response_text or "")
        assert NEW_TEXT not in (run.current_step.response_text or "")
        assert sum(r.url.path == "/p3/remember/distill" for r in upstream.requests) == 1


def test_incomplete_catalog_cannot_be_used_as_a_pre_distill_baseline():
    with story("incomplete_catalog") as (run, upstream):
        with pytest.raises(ValidationError) as error:
            learning(run)
        assert error.value.code == "catalog_incomplete"
        assert not any(r.url.path == "/p3/remember/distill" for r in upstream.requests)


class DelayedReflectionUpstream(LearningUpstream):
    """Publish the periodic status independently of the failed distill task."""

    def __init__(self, clock, available_after, *, wrong_scope=False, stall=False):
        super().__init__("empty")
        self.clock = clock
        self.available_after = available_after
        self.wrong_scope = wrong_scope
        self.stall = stall

    def __call__(self, request):
        response = super().__call__(request)
        value = response.json()
        if request.url.path == "/p3/tasks/distill_task":
            value.update(state="failed", effect_status="no_effect", result_ref=None)
        elif (
            request.url.path == "/p3/remember/reflection"
            and request.method == "GET"
            and self.distilled
        ):
            if self.stall:
                self.clock.sleep(request.extensions["timeout"]["read"])
                raise httpx.ReadTimeout("reflection did not respond", request=request)
            if self.clock() >= self.available_after:
                value["status"] = "provider_unavailable"
            if self.wrong_scope:
                value["scope"]["task_id"] = "other_run"
        return httpx.Response(response.status_code, json=value)


@contextmanager
def failed_story(monkeypatch, *, wait_seconds, available_after, **kwargs):
    clock = Clock()
    monkeypatch.setattr(
        "aether_p4_simulator.demo.stories.time",
        SimpleNamespace(monotonic=clock, sleep=clock.sleep),
    )
    upstream = DelayedReflectionUpstream(clock, available_after, **kwargs)
    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    mode = CapabilitiesData(
        profile="local",
        embedding="lexical",
        semantic_processing="literal_baseline",
        object_storage="local_sqlite",
        scheduling="temporal_v1",
        executor="local_filesystem_cache",
        operations=("remember", "recall"),
    )
    run = StoryRun(
        client,
        "demo_learning",
        "learning-review",
        mode,
        lambda _: None,
        definition=fixed_definition("learning-review"),
        wait_seconds=wait_seconds,
    )
    try:
        yield run, upstream, clock
    finally:
        client.close()


def test_failed_distill_waits_for_delayed_provider_evidence_without_reposting(monkeypatch):
    # Break caught: a fixed five-second window mislabels a later explicit provider failure.
    with failed_story(monkeypatch, wait_seconds=10, available_after=8) as (run, upstream, clock):
        with pytest.raises(ValidationError) as error:
            learning(run)
        assert error.value.code == "provider_unavailable"
        assert 8 <= clock() < 10
        assert run.current_step.id == 7
        assert "不能展示提炼总结" in run.current_step.response_text
        assert sum(r.url.path == "/p3/remember/distill" for r in upstream.requests) == 1
        assert not any(r.url.path.endswith("/result") for r in upstream.requests)
        assert all(
            r.url.params["task_id"] == "demo_learning"
            and r.url.params["session_id"] == "demo_learning_session"
            for r in upstream.requests
            if r.url.path == "/p3/remember/reflection" and r.method == "GET"
        )


def test_failed_distill_without_provider_evidence_stops_at_observation_budget(monkeypatch):
    # Break caught: ignoring the run's deadline or inventing a missing-model reason.
    with failed_story(monkeypatch, wait_seconds=1, available_after=100) as (run, upstream, clock):
        with pytest.raises(ValidationError) as error:
            learning(run)
        assert error.value.code == "processing_failed"
        assert clock() <= 1
        assert "不能展示提炼总结" not in (run.current_step.response_text or "")
        assert sum(r.url.path == "/p3/remember/distill" for r in upstream.requests) == 1


def test_reflection_read_cannot_use_a_timeout_larger_than_remaining_budget(monkeypatch):
    # Break caught: one stalled GET using the default 60s timeout exceeds a shorter budget.
    with failed_story(monkeypatch, wait_seconds=2, available_after=100, stall=True) as (
        run,
        upstream,
        clock,
    ):
        with pytest.raises(ValidationError) as error:
            learning(run)
        assert error.value.code == "upstream_timeout"
        assert 0 < clock() <= 2
        assert sum(r.url.path == "/p3/remember/distill" for r in upstream.requests) == 1


def test_provider_evidence_from_another_run_is_rejected(monkeypatch):
    with failed_story(monkeypatch, wait_seconds=10, available_after=0, wrong_scope=True) as (
        run,
        upstream,
        _,
    ):
        with pytest.raises(ValidationError) as error:
            learning(run)
        assert error.value.code == "scope_mismatch"
        assert not any(r.url.path.endswith("/result") for r in upstream.requests)
