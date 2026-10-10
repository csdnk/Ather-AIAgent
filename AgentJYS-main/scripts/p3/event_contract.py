"""Reusable event conformance example; production consumers must perform the same checks.

No storage, delivery or scheduling is implemented by this validation helper.
"""

import json
from typing import Any

import yaml
from pydantic import BaseModel

from aether_agent_memory.runtime.contracts.models import EventEnvelope
from catalog import ROOT, models


def validate_event(data: dict[str, Any]) -> BaseModel:
    envelope = EventEnvelope.model_validate_json(json.dumps(data))
    definitions = yaml.safe_load((ROOT / "contracts/p3/events.yaml").read_text(encoding="utf-8"))
    spec = next((e for e in definitions["events"] if e["type"] == envelope.event_type), None)
    if spec is None or spec["producer"] != envelope.producer:
        raise ValueError("unsupported event type or producer")
    payload = models()[spec["payload_model"]].model_validate_json(json.dumps(envelope.payload))
    wire = payload.model_dump(mode="json")
    subject_id: Any = wire
    for part in spec["subject_id_path"].split("."):
        subject_id = subject_id[part]
    expected_version = wire["memory"]["version"] if spec["subject_version_path"] else None
    if (
        envelope.subject.owner != spec["subject_owner"]
        or envelope.subject.object_type != spec["subject_type"]
        or envelope.subject.object_id != subject_id
        or envelope.subject.version != expected_version
        or envelope.subject.scope.model_dump(mode="json") != wire["memory"]["scope"]
    ):
        raise ValueError("event subject does not match its domain payload")
    return payload
