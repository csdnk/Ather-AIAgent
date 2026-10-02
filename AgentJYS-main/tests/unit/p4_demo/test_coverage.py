"""HTTP success is not a substitute for a checked story outcome."""

import pytest

from aether_p4_simulator.demo.coverage import Coverage
from aether_p4_simulator.demo.models import StartRequest
from aether_p4_simulator.validation.calls import ApiCall

from .support import Upstream, finish
from .test_service import request, service_for


@pytest.mark.parametrize("earlier_response", [False, True])
def test_call_without_http_response_cannot_be_counted_as_passed(earlier_response):
    coverage = Coverage()
    if earlier_response:
        coverage.observe(
            ApiCall(method="GET", path="/p3/health", status_code=200, elapsed_ms=1), 0
        )
        coverage.mark("GET", "/p3/health", "passed")
    coverage.observe(
        ApiCall(method="GET", path="/p3/health", status_code=None, elapsed_ms=1), 1
    )
    coverage.finish(1, "passed")
    row = next(row for row in coverage.snapshot() if row.path == "/p3/health")
    assert row.state == "blocked"
    assert "未收到" in row.detail
    assert row.calls == (2 if earlier_response else 1)
    assert row.http_statuses == ([200] if earlier_response else [])


def test_only_observed_and_checked_routes_are_counted():
    service = service_for(Upstream())
    try:
        run = finish(service, service.start(request()).run_id)
        entries = {(row.method, row.path): row for row in run.coverage}
        assert entries["POST", "/p3/remember"].calls == 3
        assert entries["POST", "/p3/remember"].state == "passed"
        assert entries["POST", "/p3/remember"].step_ids == [1, 2, 5]
        untouched = entries["POST", "/p3/remember/distill"]
        assert untouched.calls == 0 and untouched.state == "unexecuted"
        assert entries["POST", "/p3/backups"].state == "unexecuted"
        for auth_path in ("/p3/auth/config", "/p3/auth/me"):
            assert entries["GET", auth_path].calls == 0
            assert entries["GET", auth_path].state == "unexecuted"
    finally:
        service.close()


def test_successful_http_with_failed_recall_check_is_not_functional_success():
    service = service_for(Upstream("empty"))
    try:
        run = finish(service, service.start(request()).run_id)
        row = next(c for c in run.coverage if c.path == "/p3/recall")
        assert row.calls == 1 and row.http_statuses == [200]
        assert row.state == "failed"
    finally:
        service.close()


def test_reusing_run_id_for_another_fixed_story_conflicts_before_writing():
    service = service_for(Upstream("not_ready"))
    try:
        req = request()
        finish(service, service.start(req).run_id)
        from aether_p4_simulator.validation.errors import ValidationError

        with pytest.raises(ValidationError, match="开始标识"):
            service.start(StartRequest(scenario_id="weather-weekend", request_id=req.request_id))
    finally:
        service.close()
