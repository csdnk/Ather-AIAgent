import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest

from aether_p4_simulator.demo.models import StartRequest
from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError

from .registry_support import UnitRegistry
from .support import Upstream, finish

pytestmark = pytest.mark.unit


def request():
    return StartRequest(scenario_id="library-basic", request_id=uuid4())


def service_for(upstream):
    return DemoService(
        P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream)),
        registry=UnitRegistry(),
    )


@pytest.mark.parametrize(
    "variant,state,posts",
    [
        ("success", "passed", 6),
        ("empty", "failed", 3),
        ("not_ready", "blocked", 0),
        ("foreign_scope", "unconfirmed", 3),
        ("projection_failed", "failed", 1),
    ],
)
def test_service_publishes_honest_final_snapshot(variant, state, posts):
    upstream = Upstream(variant)
    service = service_for(upstream)
    try:
        initial = service.start(request())
        assert initial is not None
        final = finish(service, initial.run_id)
        assert final.state == state
        assert sum(r.method == "POST" for r in upstream.requests) == posts
        assert final.mode.embedding == "lexical"
        if state != "passed":
            assert final.steps[-1].state == "skipped"
            assert final.steps[-1].response_text == ""
        if state == "unconfirmed":
            with pytest.raises(ValidationError) as error:
                service.start(request())
            assert error.value.status == 409
        if state == "passed":
            assert all(step.state == "passed" for step in final.steps)
            final.steps.clear()
            assert len(service.get(initial.run_id).steps) == 6
    finally:
        service.close()


@pytest.mark.parametrize("same_id", [True, False])
def test_concurrent_start_is_registered_once_and_reads_do_not_deadlock(same_id):
    upstream = Upstream()
    release, entered, barrier = threading.Event(), threading.Event(), threading.Barrier(2)

    def transport(r):
        if r.url.path == "/p3/remember":
            entered.set()
            assert release.wait(3)
        return upstream(r)

    service = service_for(transport)
    first = request()

    def start(req):
        barrier.wait()
        try:
            return service.start(req)
        except ValidationError as error:
            return error

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(start, req) for req in [first, first if same_id else request()]]
            results = [f.result(timeout=2) for f in futures]
        assert any(result is not None for result in results)
        assert entered.wait(1)
        snapshots = [s for s in results if not isinstance(s, ValidationError)]
        if same_id:
            assert snapshots[0].run_id == snapshots[1].run_id
        else:
            assert len(snapshots) == 1
            assert next(e for e in results if isinstance(e, ValidationError)).status == 409
        assert service.get(snapshots[0].run_id).state == "running"
        release.set()
        assert finish(service, snapshots[0].run_id).state == "passed"
        assert sum(r.url.path == "/p3/remember" for r in upstream.requests) == 3
    finally:
        release.set()
        service.close()


def test_same_start_after_completion_never_writes_again_and_scopes_are_fresh():
    upstream = Upstream()
    service = service_for(upstream)
    first = request()
    try:
        initial = service.start(first)
        assert initial is not None
        final = finish(service, initial.run_id)
        count = len(upstream.requests)
        assert service.start(first).run_id == initial.run_id
        assert len(upstream.requests) == count
        # Compare generated scope before the second transport result is needed.
        second = service.start(request())
        second_final = finish(service, second.run_id)
        assert (
            second_final.steps[0].evidence.memories[0].scope.task_id
            != final.steps[0].evidence.memories[0].scope.task_id
        )
    finally:
        service.close()


def test_unexpected_exception_is_safe_and_conservative():
    def broken(r):
        raise RuntimeError("secret http://p3.test")

    service = service_for(broken)
    try:
        initial = service.start(request())
        assert initial is not None
        final = finish(service, initial.run_id)
        assert final.state == "unconfirmed"
        assert "secret" not in final.model_dump_json()
    finally:
        service.close()


def test_capacity_never_evicts_an_id_for_replay():
    upstream = Upstream("not_ready")
    service = service_for(upstream)
    first = request()
    try:
        finish(service, service.start(first).run_id)
        for _ in range(9):
            finish(service, service.start(request()).run_id)
        with pytest.raises(ValidationError) as error:
            service.start(request())
        assert error.value.status == 429
        assert service.start(first).run_id == str(first.request_id)
        assert not any(r.method == "POST" for r in upstream.requests)
    finally:
        service.close()


def test_close_waits_for_worker_before_closing_transport():
    entered, release = threading.Event(), threading.Event()
    upstream = Upstream()

    def transport(r):
        if r.url.path == "/p3/remember":
            entered.set()
            assert release.wait(3)
        return upstream(r)

    service = service_for(transport)
    run = service.start(request())
    assert entered.wait(1)
    closer = threading.Thread(target=service.close)
    closer.start()
    try:
        with pytest.raises(ValidationError) as error:
            service.start(request())
        assert error.value.status == 503
        assert closer.is_alive()
    finally:
        release.set()
        closer.join(3)
    assert not closer.is_alive()
    assert service.get(run.run_id).state == "passed"


def test_checkpoint_outage_stops_before_business_and_reports_unconfirmed():
    class UnavailableRegistry(UnitRegistry):
        def checkpoint(self, record, run):
            raise ConnectionError("checkpoint dependency unavailable")

    upstream = Upstream()
    registry = UnavailableRegistry()
    service = DemoService(
        P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream)),
        registry=registry,
    )
    initial = service.start(request())
    service.close()
    snapshot = service.get(initial.run_id)
    assert snapshot.state == "unconfirmed"
    assert snapshot.error.code == "checkpoint_unconfirmed"
    assert upstream.requests == []
    assert registry.get(initial.run_id).snapshot["state"] == "queued"


def test_each_business_intent_is_persisted_before_the_network_call():
    registry = UnitRegistry()
    upstream = Upstream()
    seen = []

    def transport(request):
        if request.method == "POST":
            row = next(iter(registry.records.values()))
            intent = next(
                item
                for item in row.snapshot.get("operations", [])
                if item["operation_id"] == request.headers["X-Operation-ID"]
            )
            assert intent["phase"] == "prepared"
            assert intent["request_hash"] and intent["job_id"] is None
            seen.append(intent["operation_id"])
        return upstream(request)

    service = DemoService(
        P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(transport)),
        registry=registry,
    )
    try:
        final = finish(service, service.start(request()).run_id)
        assert final.state == "passed", final.error
        assert len(seen) == 6 and len(set(seen)) == 6
        operations = registry.get(final.run_id).snapshot["operations"]
        assert len(operations) == 6
        assert all(item["phase"] == "observed" for item in operations)
    finally:
        service.close()
