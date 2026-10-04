from uuid import uuid4

import httpx
import pytest

from aether_p4_simulator.demo.models import RunSnapshot
from aether_p4_simulator.demo.registry import P3RunRegistry
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError

from .registry_support import UnitRegistry
from .support import fixed_definition

pytestmark = pytest.mark.unit


def test_new_registry_requests_managed_scope_policy():
    run = RunSnapshot(run_id=str(uuid4()), steps=[])
    definition = fixed_definition().binding()
    raw = UnitRegistry().register(run, "owner", definition).record.model_dump(mode="json")
    raw.update(scope_policy="p4_task_v1", scope_id="p4r_" + "a" * 32)

    def respond(request):
        import json

        assert json.loads(request.content)["scope_policy"] == "p4_task_v1"
        assert json.loads(request.content)["state_policy"] == "p4_state_v1"
        return httpx.Response(201, json={"created": True, "record": raw})

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(respond))
    try:
        assert (
            P3RunRegistry(client).register(run, "owner", definition).record.scope_policy
            == "p4_task_v1"
        )
    finally:
        client.close()


@pytest.mark.parametrize("created", [True, False])
@pytest.mark.parametrize("policy", ["scope_policy", "state_policy"])
def test_registry_rejects_scope_policy_downgrade(created, policy):
    run = RunSnapshot(run_id=str(uuid4()), steps=[])
    definition = fixed_definition().binding()
    raw = UnitRegistry().register(run, "owner", definition).record.model_dump(mode="json")
    raw[policy] = None
    if policy == "scope_policy":
        raw["state_policy"] = None
    calls = []

    def respond(request):
        calls.append(request.method)
        return httpx.Response(200, json={"created": created, "record": raw})

    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(ValidationError, match="登记"):
            P3RunRegistry(client).register(run, "owner", definition)
        assert calls == ["POST"]
    finally:
        client.close()


@pytest.mark.parametrize(
    "variant", ["get_run", "register_owner", "register_scenario", "checkpoint_scope"]
)
def test_registry_rejects_foreign_or_changed_wire_bindings(variant):
    run = RunSnapshot(run_id=str(uuid4()), steps=[])
    definition = fixed_definition().binding()
    record = UnitRegistry().register(run, "owner", definition).record
    raw = record.model_dump(mode="json")
    if variant == "get_run":
        raw["run_id"] = str(uuid4())
    elif variant == "register_owner":
        raw["owner_id"] = "foreign"
    elif variant == "register_scenario":
        raw["scenario_id"] = "weather-weekend"
    else:
        raw["scope_id"] = "foreign-scope"
    payload = {"created": True, "record": raw} if variant.startswith("register") else raw
    calls = []

    def respond(request):
        calls.append(request.method)
        return httpx.Response(200, json=payload)

    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(respond),
    )
    registry = P3RunRegistry(client)
    try:
        with pytest.raises(ValidationError, match="登记"):
            if variant.startswith("register"):
                registry.register(run, "owner", definition)
            elif variant == "get_run":
                registry.get(run.run_id)
            else:
                registry.checkpoint(record, run)
        assert calls == [
            "POST" if variant.startswith("register") else "GET" if variant == "get_run" else "PUT"
        ]
    finally:
        client.close()


def test_registry_reads_legacy_record_but_refuses_execution():
    run = RunSnapshot(run_id=str(uuid4()), steps=[])
    record = UnitRegistry().register(run, "owner").record
    calls = []

    def respond(request):
        calls.append(request.method)
        return httpx.Response(200, json=record.model_dump(mode="json"))

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(respond))
    try:
        registry = P3RunRegistry(client)
        assert registry.get(run.run_id) == record
        with pytest.raises(ValidationError, match="登记"):
            registry.checkpoint(record, run)
        assert calls == ["GET"]
    finally:
        client.close()
