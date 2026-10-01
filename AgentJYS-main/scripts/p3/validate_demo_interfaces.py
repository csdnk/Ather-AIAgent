"""Verify installed P3 HTTP contracts using isolated tests, not the running demo.

Only completed responses at the real P3 ASGI boundary and fully passed,
explicitly selected tests earn evidence. This is not external-provider acceptance.
"""

import argparse
import json
import os
import sys
import threading
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from aether_p4_simulator.validation.calls import ROUTES

ROOT = Path(__file__).resolve().parents[2]
TESTS = (
    "tests/integration/test_p4_demo_handoff.py"
    "::test_inventory_matches_registered_p3_method_paths",
    "tests/integration/test_p4_demo_handoff.py"
    "::test_operational_http_restore_is_isolated_and_version_checked",
    "tests/integration/test_p4_demo_stories.py::test_fixed_story_real_results",
    "tests/integration/test_p4_demo_stories.py"
    "::test_distill_displays_original_task_result_over_real_http",
    "tests/integration/test_continuous_service.py"
    "::test_http_pipeline_is_continuous_durable_and_idempotent",
    "tests/integration/test_continuous_service.py"
    "::test_document_ingress_persists_original_and_enforces_scope",
    "tests/integration/test_continuous_service.py"
    "::test_correction_and_delete_invalidate_previous_context",
    "tests/integration/test_p4_demo_temporal.py"
    "::test_real_pending_job_observed_before_release_without_reposting",
    "tests/runtime/flows/test_http.py::test_http_uses_shared_services_auth_scoping_and_health",
    "tests/runtime/flows/test_remember_lcm_complete.py::test_new_policy_http_contracts",
    "tests/runtime/temporal/test_temporal_http.py::test_http_wait_timeout_preserves_workflow",
    "tests/runtime/temporal/test_temporal_http.py"
    "::test_full_http_commands_use_temporal_and_probes_do_not_write_p2",
    "tests/runtime/temporal/test_temporal_service.py"
    "::test_temporal_unavailable_is_visible_without_liveness_failure",
    "tests/runtime/temporal/test_temporal_service.py"
    "::test_retired_manual_cycle_does_not_report_fictitious_scheduled_work",
    "tests/runtime/temporal/test_control_admission.py"
    "::test_http_control_reaches_original_workflow_after_ack_loss",
    "tests/runtime/temporal/test_control_admission.py"
    "::test_closed_original_control_is_reported_and_never_restarted",
    "tests/runtime/temporal/test_control_admission.py"
    "::test_periodic_controls_require_deployment_operator_and_keep_chain",
    "tests/integration/test_monitor_catalogs.py"
    "::test_monitor_catalogs_page_and_filter_without_exposing_bodies",
    "tests/integration/test_monitor_catalogs.py"
    "::test_monitor_catalogs_enforce_auth_scope_and_cursor_binding",
    "tests/integration/test_rf_completion.py"
    "::test_automatic_cache_repair_and_independent_readback",
)


class Evidence:
    """Opt-in, serial pytest observer. Never changes request or response content."""

    def __init__(self, *, tests=TESTS, routes=ROUTES):
        self.tests = set(tests)
        self.routes = tuple(routes)
        self.collected = set()
        self.phases = defaultdict(dict)
        self.responses = defaultdict(lambda: defaultdict(Counter))
        self.active = None
        self.lock = threading.Lock()

    def pytest_configure(self, config):
        if getattr(config.option, "numprocesses", None) not in (None, 0):
            raise pytest.UsageError("HTTP evidence attribution requires serial pytest (no -n).")

    def pytest_collection_modifyitems(self, items):
        self.collected = {
            item.nodeid
            for item in items
            if item.nodeid.split("[", 1)[0] in self.tests
        }

    def pytest_runtest_logstart(self, nodeid, location):
        with self.lock:
            self.active = nodeid if nodeid in self.collected else None

    def pytest_runtest_logfinish(self, nodeid, location):
        with self.lock:
            self.active = None

    def pytest_runtest_logreport(self, report):
        if report.nodeid in self.collected:
            self.phases[report.nodeid][report.when] = report.passed and not hasattr(
                report, "wasxfail"
            )

    def pytest_sessionstart(self, session):
        self.original = FastAPI.__call__
        observer = self

        async def observed(app, scope, receive, send):
            with observer.lock:
                nodeid = observer.active
            if not nodeid or scope["type"] != "http" or app.title != "P3 runtime":
                return await observer.original(app, scope, receive, send)
            status, complete = None, False

            async def record(message):
                nonlocal status, complete
                await send(message)
                if message["type"] == "http.response.start":
                    status = message["status"]
                elif message["type"] == "http.response.body" and not message.get("more_body"):
                    complete = True

            try:
                return await observer.original(app, scope, receive, record)
            finally:
                route = scope.get("route")
                if (
                    complete
                    and status is not None
                    and isinstance(route, APIRoute)
                    and route.endpoint.__module__.startswith("aether_agent_memory.")
                ):
                    with observer.lock:
                        if nodeid == observer.active:
                            observer.responses[nodeid][scope["method"], route.path][status] += 1

        FastAPI.__call__ = observed

    def pytest_sessionfinish(self, session, exitstatus):
        FastAPI.__call__ = self.original

    def summary(self, exit_code):
        passed = {
            case
            for case in self.collected
            if self.phases[case] == {"setup": True, "call": True, "teardown": True}
        }
        rows = []
        for method, path in self.routes:
            evidence = []
            for case in sorted(passed):
                statuses = self.responses[case].get((method, path))
                if statuses:
                    evidence.append(
                        {
                            "test": case,
                            "statuses": sorted(statuses),
                            "responses": sum(statuses.values()),
                        }
                    )
            retired = (method, path) == ("POST", "/p3/maintenance/cycle")
            verified = any(
                status == 410 if retired else 200 <= status < 300
                for item in evidence
                for status in item["statuses"]
            )
            rows.append(
                {
                    "method": method,
                    "path": path,
                    "verified": verified,
                    "contract": "retired_rejection" if retired else "response_and_test_assertions",
                    "evidence": evidence,
                }
            )
        missing = [
            {"method": row["method"], "path": row["path"]}
            for row in rows
            if not row["verified"]
        ]
        incomplete = sorted(self.collected - passed)
        not_collected = sorted(
            self.tests - {case.split("[", 1)[0] for case in self.collected}
        )
        return {
            "status": "passed"
            if not (exit_code or missing or incomplete or not_collected)
            else "incomplete",
            "boundary": "P3 ASGI routes + test-owned Temporal; P4 story tests also use TCP",
            "limitations": [
                "Lexical/local providers are not real P2, vector/Milvus or model acceptance.",
                "S4 provider_unavailable is an expected blocked outcome, not model success.",
                "The successful distill-result test uses a deterministic test-only provider.",
                "A 410 on the retired maintenance route is rejection, not executed maintenance.",
                "Evidence covers asserted contracts, not every possible behavior of an endpoint.",
            ],
            "pytest_exit_code": exit_code,
            "total_routes": len(rows),
            "verified_routes": len(rows) - len(missing),
            "missing": missing,
            "incomplete_cases": incomplete,
            "not_collected_tests": not_collected,
            "routes": rows,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", required=True, type=Path, help="New external evidence directory"
    )
    parser.add_argument(
        "--full-suite", action="store_true", help="Run all tests and collect the same evidence"
    )
    args = parser.parse_args()
    directory = args.directory.resolve()
    if directory.is_relative_to(ROOT):
        parser.error("--directory must be outside the checkout")
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("--directory must be new (never reuse live or previous test data)")
    os.chdir(ROOT)
    # Match `python -m pytest`: existing helpers import the local `tests` package.
    sys.path.insert(0, str(ROOT))
    evidence = Evidence()
    result = pytest.main(
        [
            "-q",
            "-ra",
            "--basetemp=" + str(directory / "tmp"),
            "-o",
            "cache_dir=" + str(directory / "pytest-cache"),
            *(["tests"] if args.full_suite else TESTS),
        ],
        plugins=[evidence],
    )
    report = evidence.summary(int(result))
    report_path = directory / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"HTTP contracts: {report['verified_routes']}/{report['total_routes']}; {report['status']}"
    )
    print(f"Evidence: {report_path}")
    return int(result) or (0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
