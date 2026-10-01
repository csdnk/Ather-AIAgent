"""Document publication must follow the owned summary task, not arbitrary versions."""

from copy import deepcopy

import httpx
import pytest

from aether_agent_memory.remember.contracts.models import DocumentInput
from aether_p4_simulator.demo.execution import StoryRun
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError

from .support import NOW

pytestmark = pytest.mark.unit


class SummaryUpstream:
    def __init__(self, variant="valid"):
        self.variant = variant
        self.requests = []
        self.scope = dict(
            tenant_id="t1",
            application_id="a1",
            user_id="u1",
            agent_id="a1",
            task_id="demo_summary",
            session_id="demo_summary_session",
        )
        self.old = dict(memory_id="m1", version=1, scope=self.scope)
        self.new = dict(memory_id="m1", version=2, scope=self.scope)
        self.source = dict(
            source_id="s1",
            source_version=1,
            content_hash="a" * 64,
            locator="test:source",
        )

    def __call__(self, request):
        self.requests.append((request.method, request.url.path))
        path = request.url.path
        if path == "/p3/remember":
            value = dict(
                operation_id=request.headers["X-Operation-ID"],
                saved=True,
                source=self.source,
                memories=[self.old],
                task_ids=["summary"],
                phase="processing",
            )
        elif path == "/p3/tasks/summary":
            subject = dict(owner="remember", object_type="memory", object_id="m1", scope=self.scope)
            value = dict(
                task_id="summary",
                owner_flow="remember",
                kind="remember.summarize",
                subject=subject,
                input_ref=subject,
                idempotency_key="summary",
                input_hash="a" * 64,
                initiator_id="alice",
                initiator_auth_epoch=1,
                deadline_at=NOW,
                state="succeeded",
                revision=2,
                attempt=1,
                effect_status="confirmed",
                result_ref=subject,
            )
            if self.variant == "wrong_task":
                value["kind"] = "remember.project"
            if self.variant == "inflight" and self.requests.count(("GET", path)) <= 2:
                value.update(
                    state="running",
                    effect_status="unknown",
                    result_ref=None,
                    execution=dict(
                        namespace="default",
                        workflow_id="wf",
                        run_id="run",
                        activity_id="publish",
                        delivery_attempt=1,
                        epoch=1,
                    ),
                )
        elif path.endswith("/progress"):
            value = dict(wait=None, stages=[], checkpoints=[])
        elif path == "/p3/remember/m1":
            value = dict(
                ref=deepcopy(self.new),
                revision=1,
                object_revision=3,
                kind="working",
                status="active",
                content="真实节选摘要：借期 30 天。",
                content_hash="b" * 64,
                sources=[self.source],
                projection_state="ready",
                model_space="lexical",
                supersedes=self.old,
                created_at=NOW,
            )
            if self.variant == "version_jump":
                value["ref"]["version"] = 3
            elif self.variant == "no_predecessor":
                value["supersedes"] = None
            elif self.variant == "foreign_source":
                value["sources"] = [{**self.source, "source_id": "foreign"}]
        elif path.endswith("/processing"):
            value = dict(
                memory=self.new,
                state="completed",
                state_basis="latest_task_per_memory_and_kind",
                projection_state="ready",
                memory_status="active",
                derived_memory_ids=[],
                tasks=[],
                historical_failed_tasks=0,
                working_summary=dict(
                    memory=self.new,
                    source=self.source,
                    state="ready",
                    representation="extractive_summary",
                    task_id="summary",
                    is_complete=False,
                ),
            )
            if self.variant == "foreign_summary":
                value["working_summary"]["task_id"] = "other"
        else:
            raise AssertionError(f"Unexpected route {path}")
        return httpx.Response(200, json=value)


def execute(upstream):
    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    run = StoryRun(client, "demo_summary", "library-full", None, lambda _: None)
    document = DocumentInput(
        kind="document",
        provider_id="local",
        document_id="doc",
        document_version="1",
        expected_hash="a" * 64,
    )
    try:
        with run.step(1, "/p3/remember"):
            run.remember("rules", "借期 30 天。", document=document)
            snapshot = run.current("rules")
        return run, snapshot
    finally:
        client.close()


@pytest.mark.parametrize("variant", ["valid", "inflight"])
def test_document_summary_uses_verified_new_version_and_preserves_original_receipt(variant):
    upstream = SummaryUpstream(variant)
    run, snapshot = execute(upstream)
    assert snapshot.ref.version == 2
    assert run.receipts["rules"].memories[0].version == 1
    assert run.refs["m1"].version == 2
    assert "真实节选摘要" in run.current_step.response_text
    assert ("GET", "/p3/tasks/summary") in upstream.requests
    assert sum(method == "POST" for method, _ in upstream.requests) == 1


@pytest.mark.parametrize(
    "variant",
    [
        "version_jump",
        "no_predecessor",
        "foreign_source",
        "wrong_task",
        "foreign_summary",
    ],
)
def test_summary_rejects_unproved_versions_or_provenance_without_rewriting(variant):
    upstream = SummaryUpstream(variant)
    with pytest.raises(ValidationError) as error:
        execute(upstream)
    assert error.value.code == "scope_mismatch"
    assert sum(method == "POST" for method, _ in upstream.requests) == 1
