"""Original fixed inputs must exist before the first business request."""

import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from aether_p4_simulator.demo import definition as definition_module
from aether_p4_simulator.demo import scenarios
from aether_p4_simulator.demo.definition import RunDefinition
from aether_p4_simulator.demo.execution import StoryRun
from aether_p4_simulator.demo.models import StartRequest
from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.validation.client import P3ValidationClient

from .support import Upstream


def test_demo_saves_versioned_definition_before_business():
    upstream = Upstream()
    demo = DemoService(
        P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    )
    run_id = uuid4()
    try:
        demo.start(StartRequest(scenario_id="library-basic", request_id=run_id))
    finally:
        demo.close()
    record = upstream.registry.get(str(run_id))
    assert getattr(record, "definition", None) is not None, (
        "execution needs its original definition"
    )
    assert str(run_id) in upstream.registry.definitions
    assert record.snapshot["state"] == "passed"


def test_frozen_future_requests_ignore_changed_scenario_and_clock(monkeypatch):
    upstream = Upstream()
    original = upstream.registry.save_definition
    original_turns = scenarios.LIBRARY

    def save_then_change(record, payload):
        original(record, payload)
        monkeypatch.setattr(
            scenarios,
            "LIBRARY",
            tuple(replace(turn, user_text="changed future") for turn in original_turns),
        )
        monkeypatch.setattr(definition_module, "now", lambda: "2099-01-01T00:00:00.000Z")

    upstream.registry.save_definition = save_then_change
    run_id = uuid4()
    demo = DemoService(
        P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    )
    try:
        demo.start(StartRequest(scenario_id="library-basic", request_id=run_id))
    finally:
        demo.close()
    frozen = RunDefinition.model_validate_json(upstream.registry.definitions[str(run_id)])
    bodies = [
        json.loads(request.content)
        for request in upstream.requests
        if request.url.path == "/p3/remember"
    ]
    expected = [
        (index, turn) for index, turn in enumerate(original_turns) if turn.action == "remember"
    ]
    assert len(bodies) == len(expected) == 3
    for body, (index, turn) in zip(bodies, expected, strict=True):
        assert body["content"]["text"] == turn.user_text
        assert body["source"]["occurred_at"] == frozen.event_times[index]
        assert not body["source"]["occurred_at"].startswith("2099")
    assert upstream.registry.get(str(run_id)).snapshot["state"] == "passed"


@pytest.mark.parametrize(
    "scenario",
    ["library-full", "weather-weekend", "preference-update", "learning-review", "forget-sources"],
)
def test_fixed_story_reads_original_step_and_source_time_after_definition_roundtrip(
    monkeypatch, scenario
):
    frozen = RunDefinition.capture(uuid4(), scenario)
    payload = frozen.encode()
    monkeypatch.setitem(scenarios.STORY_TEXTS, scenario, ("changed",))
    monkeypatch.setattr(scenarios, "STORY_INPUTS", {"source_version": "changed"})
    restored = RunDefinition.model_validate_json(payload)
    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(
            lambda request: pytest.fail("no network for definition read")
        ),
    )
    try:
        run = StoryRun(client, "scope", scenario, None, lambda _: None, definition=restored)
        with run.step(2, "/p3/remember") as step:
            source = run.source_input("future")
            assert step.user_text == frozen.texts[1]
            assert source.occurred_at == frozen.event_times[1]
            assert source.external_version == "1"
        assert restored.inputs == frozen.inputs and restored.rules == frozen.rules
    finally:
        client.close()


def test_changed_executor_stops_before_any_business_call(monkeypatch):
    upstream = Upstream()
    original = upstream.registry.save_definition

    def changed(record, payload):
        original(record, payload)
        monkeypatch.setattr(definition_module, "implementation_hash", lambda: "a" * 64)

    upstream.registry.save_definition = changed
    run_id = uuid4()
    demo = DemoService(
        P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    )
    try:
        demo.start(StartRequest(scenario_id="library-basic", request_id=run_id))
    finally:
        demo.close()
    record = upstream.registry.get(str(run_id))
    assert record.snapshot["state"] == "unconfirmed"
    assert record.snapshot["error"]["code"] == "definition_executor_changed"
    assert record.snapshot["operations"] == [] and upstream.requests == []


@pytest.mark.parametrize("fault", ["missing_future_parameter", "invalid_future_parameter"])
def test_incomplete_definition_is_rejected_before_any_business_write(monkeypatch, fault):
    original = RunDefinition.capture.__func__

    def malformed(cls, run_id, scenario_id):
        definition = original(cls, run_id, scenario_id)
        values = (
            tuple(value for value in definition.inputs if value.name != "basic_token_budget")
            if fault == "missing_future_parameter"
            else tuple(
                value.model_copy(update={"value": -1}) if value.name == "retention_hours" else value
                for value in definition.inputs
            )
        )
        return definition.model_copy(update={"inputs": values})

    monkeypatch.setattr(RunDefinition, "capture", classmethod(malformed))
    upstream = Upstream()
    run_id = uuid4()
    demo = DemoService(
        P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    )
    try:
        demo.start(StartRequest(scenario_id="library-basic", request_id=run_id))
    finally:
        demo.close()
    record = upstream.registry.get(str(run_id))
    assert upstream.requests == [], "definition validation must precede business execution"
    assert record.snapshot["state"] == "unconfirmed"
    assert record.snapshot["error"]["code"] == "definition_invalid"
