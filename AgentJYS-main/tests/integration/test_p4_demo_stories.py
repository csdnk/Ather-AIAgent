"""Five fixed stories through actual P4 HTTP, P3 and test-owned Temporal."""

from uuid import uuid4

import pytest
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_p4_execution_state import restore_without_writes

from aether_agent_memory.remember.basic.extraction import LiteralExtraction
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.contracts.models import CandidateFact, ExtractionResult
from aether_p4_simulator.demo import stories
from aether_p4_simulator.validation.calls import ROUTES
from aether_p4_simulator.validation.client import P3ValidationClient
from azure_component_service import Service
from integration.test_p4_demo_temporal import (
    PREFIX,
    business_writes,
    real_chain,
    until,
)
from integration.test_p4_demo_temporal import (
    configuration as reference_configuration,
)

pytestmark = pytest.mark.integration


def test_frozen_retention_parameter_matches_real_policy_and_display(
    tmp_path, temporal_server, monkeypatch
):
    from aether_p4_simulator.demo import scenarios

    monkeypatch.setitem(scenarios.STORY_INPUTS, "retention_hours", 72)
    service = Service(reference_configuration(tmp_path, temporal_server.endpoint))
    with real_chain(service) as (browser, bridge):
        run_id = str(uuid4())
        response = browser.post(
            PREFIX + "/runs", json={"scenario_id": "preference-update", "request_id": run_id}
        )
        assert response.status_code == 202, response.text
        value = until(
            lambda: browser.get(PREFIX + "/runs/" + run_id).json(),
            lambda x: x["state"] not in {"queued", "running"},
            seconds=150,
        )
        assert value["state"] == "passed", value["error"]
        memory_id = value["steps"][3]["evidence"]["memories"][0]["memory_id"]
        policy = bridge.client.get(
            f"/p3/remember/{memory_id}/retention", headers={"Authorization": "Bearer alice"}
        )
        assert policy.status_code == 200 and policy.json()["policy"]["idle_hours"] == 72
        assert "72 小时" in value["steps"][6]["response_text"]
        assert "168 小时" not in value["steps"][6]["response_text"]


@pytest.mark.parametrize(
    ("scenario", "terminal", "required_calls"),
    [
        (
            "library-full",
            "passed",
            {
                ("PUT", "/p3/documents/{document_id}"),
                ("POST", "/p3/remember/body"),
                ("POST", "/p3/remember/body/range"),
                ("POST", "/p3/sources/read-range"),
                ("GET", "/p3/memories"),
            },
        ),
        (
            "weather-weekend",
            "passed",
            {
                ("POST", "/p3/remember/consolidate"),
                ("GET", "/p3/tasks/{task_id}/progress"),
                ("GET", "/p3/operate/memories/{memory_id}"),
            },
        ),
        (
            "preference-update",
            "passed",
            {
                ("POST", "/p3/remember/{memory_id}/correct"),
                ("POST", "/p3/remember/{memory_id}/lifecycle"),
                ("GET", "/p3/remember/{memory_id}/retention"),
                ("POST", "/p3/remember/{memory_id}/retention"),
                ("POST", "/p3/remember/{memory_id}/reprocess"),
            },
        ),
        (
            "learning-review",
            "blocked",
            {
                ("GET", "/p3/remember/reflection"),
                ("POST", "/p3/remember/reflection"),
                ("POST", "/p3/remember/distill"),
            },
        ),
        (
            "forget-sources",
            "passed",
            {
                ("POST", "/p3/remember/{memory_id}/delete"),
                ("POST", "/p3/sources/{source_id}/delete"),
                ("POST", "/p3/sources/{source_id}/revoke"),
                ("GET", "/p3/recalls/{recall_id}/result"),
            },
        ),
    ],
)
def test_fixed_story_real_results(
    configuration,
    monkeypatch,
    scenario,
    terminal,
    required_calls,
):
    # Preserve the real executor; keep the Python traceback for unexpected failures.
    errors = []
    original = stories.run_story

    def observed(run):
        try:
            return original(run)
        except Exception as error:
            errors.append(error)
            raise

    monkeypatch.setattr(stories, "run_story", observed)
    # The single-message current-P2 smoke fixture caps distillation at one input.
    # Keep the fixed stories' original default policy while using its real P2 endpoint.
    service = Service(configuration.model_copy(update={"remember": RememberPolicy()}))
    with real_chain(service) as (browser, bridge):
        run_id = str(uuid4())
        response = browser.post(
            PREFIX + "/runs", json={"scenario_id": scenario, "request_id": run_id}
        )
        assert response.status_code == 202, response.text
        value = until(
            lambda: browser.get(PREFIX + "/runs/" + run_id).json(),
            lambda x: x["state"] not in {"queued", "running"},
            seconds=150,
        )
        if value["state"] != terminal and errors:
            raise errors[0]
        assert value["state"] == terminal, (value["current_step"], value["error"], value["steps"])
        assert value["scenario_id"] == scenario
        assert value["total_steps"] == len(value["steps"]) >= 7
        assert value["mode"]["embedding"] == "injected"
        assert value["mode"]["scheduling"] == "temporal_v1"
        called = {
            (c["method"], c["path"]) for c in value["coverage"] if c["calls"] and c["http_statuses"]
        }
        assert required_calls <= called
        assert all(c["http_statuses"] for c in value["coverage"] if c["state"] == "passed")
        # System-mutating operations never ride on a scene-start button.
        assert not any(
            path
            in {
                "/p3/recovery",
                "/p3/configuration",
                "/p3/backups",
                "/p3/restore-drills",
                "/p3/maintenance/cycle",
            }
            for method, path, _ in bridge.calls
            if method in {"PUT", "POST"}
        )
        assert len(value["coverage"]) == len(ROUTES)
        assert {(row["method"], row["path"]) for row in value["coverage"]} == set(ROUTES)
        for row in value["coverage"]:
            if row["path"].startswith("/p3/auth/"):
                assert row["state"] == "unexecuted" and row["calls"] == 0
        if terminal == "passed":
            assert all(s["state"] == "passed" for s in value["steps"])
        else:
            assert value["error"]["code"] == "provider_unavailable"
            assert any(s["state"] == "blocked" for s in value["steps"])
            assert value["mode"]["semantic_processing"] == "literal_baseline"
        writes_before = business_writes(bridge)
        repeat = browser.post(
            PREFIX + "/runs", json={"scenario_id": scenario, "request_id": run_id}
        )
        assert repeat.status_code == 200
        assert repeat.json()["operations"] == value["operations"]
        assert repeat.json()["steps"] == value["steps"]
        assert business_writes(bridge) == writes_before
        restored = restore_without_writes(bridge.client, service, run_id)
        assert restored.journal_matches
        data = restored.data
        assert data.completed_steps == [
            step["id"] for step in value["steps"] if step["state"] == "passed"
        ]
        if terminal == "passed":
            assert all(result.consumed for result in data.resolved.values())
        else:
            pending = [key for key, result in data.resolved.items() if not result.consumed]
            assert pending == [restored.record.scope_id + "_7_distill"]
        capabilities = bridge.client.get(
            "/p3/capabilities", headers={"Authorization": "Bearer alice"}
        )
        assert capabilities.json()["object_storage"] == "ceph"
        if scenario == "library-full":
            assert data.documents["rules"].document_id == restored.record.scope_id + "_rules"
            assert data.recalls["last"].recall_id == value["steps"][-1]["evidence"]["recall_id"]
        elif scenario == "weather-weekend":
            assert set(data.episodes) == {"weather", "park", "backup"}
            assert data.task_groups
        elif scenario == "preference-update":
            assert set(data.task_slots) == {"reprocess", "reindex"}
            assert "before_correction" in data.recalls
        elif scenario == "learning-review":
            assert len(data.episodes["distill"]) >= 3
            assert "before_distill" in data.baselines and "distill" in data.task_slots
        else:
            assert data.task_groups["cleanup"] and "before_delete" in data.recalls


@pytest.mark.parametrize("reflection_first", [False, True])
def test_distill_displays_original_task_result_over_real_http(
    configuration, monkeypatch, reflection_first
):
    class DeterministicReview(LiteralExtraction):
        """Test-only candidate provider; not proof of real model quality."""

        async def review_episodes(self, ctx, episodes, items, policy_version):
            return ExtractionResult(
                candidates=(
                    CandidateFact(
                        text=items[0].content,
                        kind="semantic",
                        sources=items[0].sources,
                        evidence_status="supported",
                    ),
                ),
                model_id="test_review",
                policy_version=policy_version,
            )

    if reflection_first:
        configure = P3ValidationClient.configure_reflection

        def await_original_reflection(client, body, operation_id):
            receipt = configure(client, body, operation_id)

            def completed():
                status = client.reflection(body.selection).root
                task_id = status.get("last_task_id")
                return task_id and client.task(task_id).state == "succeeded"

            # Force the real periodic task to finish before the story reads its
            # pre-distill catalog. This sends only GETs after the original POST.
            until(completed, bool, seconds=60)
            return receipt

        monkeypatch.setattr(P3ValidationClient, "configure_reflection", await_original_reflection)

    service = Service(configuration.model_copy(update={"remember": RememberPolicy()}))
    service.runtime.remember.extraction = DeterministicReview()
    with real_chain(service) as (browser, bridge):
        run_id = str(uuid4())
        response = browser.post(
            PREFIX + "/runs", json={"scenario_id": "learning-review", "request_id": run_id}
        )
        assert response.status_code == 202
        value = until(
            lambda: browser.get(PREFIX + "/runs/" + run_id).json(),
            lambda x: x["state"] not in {"queued", "running"},
            seconds=150,
        )
        assert value["state"] == "passed", (value["error"], value["steps"])
        final = value["steps"][-1]
        task_id = final["evidence"]["job_id"]
        path = f"/p3/operations/{task_id}/result"
        assert any(method == "GET" and route == path for method, route, _ in bridge.calls)
        result = bridge.client.get(path, headers={"Authorization": "Bearer alice"})
        assert result.status_code == 200, result.text
        assert final["evidence"]["memories"] == result.json()["memories"]
        assert len(final["evidence"]["memories"]) == 1
        assert sum(route == "/p3/remember/distill" for _, route, _ in bridge.calls) == 1
        restored = restore_without_writes(bridge.client, service, run_id)
        assert restored.journal_matches and restored.data.completed_steps == list(range(1, 9))
        assert restored.data.task_slots["distill"] == task_id
        assert len(restored.data.episodes["distill"]) >= 3
        generated_id = final["evidence"]["memories"][0]["memory_id"]
        assert generated_id in restored.data.refs
        baseline = restored.data.baselines["before_distill"]
        if generated_id in baseline:
            assert baseline[generated_id].model_dump(mode="json") == result.json()["memories"][0]
            assert "新增 0、更新 0、复用 1" in final["response_text"]
            assert "复用项不是新增总结" in final["response_text"]
        else:
            assert "新增 1、更新 0、复用 0" in final["response_text"]
        if reflection_first:
            assert generated_id in baseline
