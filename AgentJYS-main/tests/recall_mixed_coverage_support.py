"""AET-39 safety assertions and externally approved server-policy profiles.

P3_RECALL_MIXED_POLICY_FILE may supply allow/deny profiles. The JSON contains
profiles keyed by those names, each with decision_reference, policy_version,
settings (actual RecallSettings fields), and optional terminal_policy wait/reject.
Absence leaves Q09 restricted; it never means approval or supplies a fake toggle.
"""

import json
from collections import Counter
from hashlib import sha256
from pathlib import Path

from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.contracts.models import ContextPack
from recall_projection_state_support import ProjectionStateProbe


def mixed_policy(path, mode):
    assert mode in {"allow", "deny"}
    if not path:
        return {}, {
            "requested_mode": mode,
            "confirmed": False,
            "origin": "current_server_deployment",
            "restriction": "Q09: approved partial-delivery and wait/terminal policy unavailable",
        }
    raw = Path(path).read_bytes()
    document = json.loads(raw)
    assert set(document) == {"profiles"}
    assert set(document["profiles"]) == {"allow", "deny"}, "both approved profiles required"
    profile = document["profiles"][mode]
    assert set(profile) <= {"decision_reference", "policy_version", "settings", "terminal_policy"}
    assert all(
        isinstance(profile.get(key), str) and profile[key].strip()
        for key in ("decision_reference", "policy_version")
    ), "approval reference and expected target policy version are required"
    assert isinstance(profile.get("settings"), dict)
    # Reject guessed policy keys instead of silently pretending they affect the server.
    RecallSettings.model_validate(profile["settings"])
    terminal = profile.get("terminal_policy")
    assert terminal in {None, "wait", "reject"}
    assert mode != "allow" or terminal is None
    return profile["settings"], {
        "requested_mode": mode,
        "confirmed": True,
        "origin": str(Path(path).resolve()),
        "manifest_sha256": sha256(raw).hexdigest(),
        "decision_reference": profile["decision_reference"],
        "expected_policy_version": profile["policy_version"],
        "terminal_policy": terminal,
        "settings_sha256": sha256(
            json.dumps(profile["settings"], sort_keys=True).encode()
        ).hexdigest(),
    }


class MixedCoverageProbe(ProjectionStateProbe):
    def __init__(self, runtime, monkeypatch):
        super().__init__(runtime, monkeypatch)
        self.body_refs, self.enumerations = [], []
        bodies = runtime.recall.assembly.bodies
        original_bodies = bodies.load_bodies
        memories = runtime.recall.memories
        original_working = memories.working

        async def load_bodies(ctx, refs):
            self.body_refs.append((ctx.operation_id, tuple(refs)))
            return await original_bodies(ctx, refs)

        def working(ctx, selection, page):
            self.enumerations.append(ctx.operation_id)
            return original_working(ctx, selection, page)

        monkeypatch.setattr(bodies, "load_bodies", load_bodies)
        monkeypatch.setattr(memories, "working", working)

    def observe(self, operation_id, vector_probe):
        result = super().observe(operation_id, vector_probe)
        result.update(
            body_refs=[
                ref.model_dump(mode="json")
                for op, refs in self.body_refs
                if op == operation_id
                for ref in refs
            ],
            working_enumerations=self.enumerations.count(operation_id),
        )
        return result


def assert_mixed_observation(observation, state, ready):
    rows = observation["readiness"]
    assert rows, "actual readiness was not inspected"
    for row in rows:
        assert row["source"] == "working" and not row["complete"]
        assert row["ready_count"] == 1
        assert row["pending_count"] == (1 if state == "pending" else 0)
        assert row["failed_count"] == (1 if state == "failed" else 0)
    assert observation["working_enumerations"] == 0, "body listing fallback executed"
    assert all(ref == ready.ref.model_dump(mode="json") for ref in observation["body_refs"])
    assert observation["routes"] and all(
        route["source"] == "working" for route in observation["routes"]
    ), "source switched or vector execution missing"


def assert_mixed_pack(payload, ready, missing, state, policy_version):
    pack = ContextPack.model_validate(payload)
    assert pack.selected_sources == ("working",)
    assert pack.scope == ready.ref.scope
    assert pack.coverage.working == "partial" and pack.coverage.long_term == "not_requested"
    assert pack.outcome == "degraded"
    assert "working_index_" + state in pack.degradation_reasons
    assert pack.policy_version == policy_version
    items = [item for group in pack.groups for item in group.items]
    assert Counter(item.memory for item in items) == Counter([ready.ref])
    item = items[0]
    assert item.content == ready.content and item.sources == ready.sources
    assert item.representation == "original" and item.content in pack.rendered_context
    assert missing["ref"] not in [item.memory.model_dump(mode="json") for item in items]
    assert missing["text"] not in pack.rendered_context
    return pack
