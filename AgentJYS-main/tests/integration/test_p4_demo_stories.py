"""Five fixed stories through actual P4 HTTP, P3 and test-owned Temporal."""

from uuid import uuid4

import pytest

from aether_agent_memory.remember.basic.extraction import LiteralExtraction
from aether_agent_memory.remember.contracts.models import CandidateFact, ExtractionResult
from aether_agent_memory.runtime.flows.application import Service
from aether_p4_simulator.demo import stories
from aether_p4_simulator.validation.calls import ROUTES
from integration.test_p4_demo_temporal import PREFIX, configuration, real_chain, until

pytestmark = pytest.mark.integration


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
    tmp_path,
    temporal_server,
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
    service = Service(configuration(tmp_path, temporal_server.endpoint))
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
        assert value["mode"]["embedding"] == "lexical"
        assert value["mode"]["scheduling"] == "temporal_v1"
        called = {
            (c["method"], c["path"])
            for c in value["coverage"]
            if c["calls"] and c["http_statuses"]
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
        post_count = sum(m in {"POST", "PUT"} for m, _, _ in bridge.calls)
        repeat = browser.post(
            PREFIX + "/runs", json={"scenario_id": scenario, "request_id": run_id}
        )
        assert repeat.status_code == 200
        assert sum(m in {"POST", "PUT"} for m, _, _ in bridge.calls) == post_count


def test_distill_displays_original_task_result_over_real_http(tmp_path, temporal_server):
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

    service = Service(configuration(tmp_path, temporal_server.endpoint))
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
        assert "新增 1、更新 0、复用 0" in final["response_text"]
        task_id = final["evidence"]["job_id"]
        path = f"/p3/operations/{task_id}/result"
        assert any(method == "GET" and route == path for method, route, _ in bridge.calls)
        result = bridge.client.get(path, headers={"Authorization": "Bearer alice"})
        assert result.status_code == 200, result.text
        assert final["evidence"]["memories"] == result.json()["memories"]
        assert len(final["evidence"]["memories"]) == 1
        assert sum(route == "/p3/remember/distill" for _, route, _ in bridge.calls) == 1
