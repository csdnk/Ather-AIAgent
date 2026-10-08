"""Exercise production scheduler with an explicit in-memory metadata transaction."""

from types import SimpleNamespace

import pytest

from aether_agent_memory.remember.basic.pipeline import RememberPipeline
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import Scope


class Tx:
    def __init__(self):
        self.data = {}

    def rows(self, table):
        return [(key, row) for (name, key), row in self.data.items() if name == table]

    def read(self, table, key):
        return self.data.get((table, key))

    def write(self, table, key, row):
        self.data[table, key] = row


def scheduler():
    owner = object.__new__(RememberPipeline)
    owner.policy = RememberPolicy()
    owner.identity = SimpleNamespace(clock=lambda: "2026-10-07T02:00:00Z")
    owner.final_guard = lambda *args: SimpleNamespace(items=[SimpleNamespace(decision="allowed")])
    tx = Tx()
    owner.current = lambda _, key: SimpleNamespace(
        ref=MemoryRef.model_validate(tx.read("remember_pending", key)["ref"])
    )
    owner.enqueue = lambda *args: "task-1"
    scope = Scope(tenant_id="t", application_id="app", user_id="u", agent_id="a", session_id="s")
    return owner, tx, scope


def add(tx, scope, key, *, size=1, state="pending", at="2026-10-07T01:59:00Z"):
    tx.write(
        "remember_pending",
        key,
        {
            "ref": MemoryRef(scope=scope, memory_id=key, version=1).model_dump(mode="json"),
            "bytes": size,
            "tokens": 1,
            "state": state,
            "created_at": at,
        },
    )


@pytest.mark.parametrize(
    "count,size,at,expected",
    [
        (31, 1, "2026-10-07T01:59:00Z", False),
        (32, 1, "2026-10-07T01:59:00Z", True),
        (1, 7999, "2026-10-07T01:59:00Z", False),
        (1, 8000, "2026-10-07T01:59:00Z", True),
        (1, 1, "2026-10-07T01:00:00Z", True),
    ],
)
def test_trigger_boundaries(count, size, at, expected):
    owner, tx, scope = scheduler()
    for i in range(count):
        add(tx, scope, f"m{i}", size=size, at=at)
    assert bool(owner.schedule(tx, None, scope)) is expected


def test_legacy_overlap_setting_cannot_reprocess_old_or_cross_scope_messages():
    owner, tx, scope = scheduler()
    owner.policy = owner.policy.model_copy(update={"consolidation_overlap_messages": 2})
    for i in range(3):
        add(tx, scope, f"old{i}", state="processed", at=f"2026-10-07T01:5{i}:00Z")
    foreign_scope = scope.model_copy(update={"session_id": "elsewhere"})
    add(tx, foreign_scope, "foreign", state="processed")
    add(tx, foreign_scope, "foreign_pending", size=8000, at="2026-10-07T01:55:00Z")
    add(tx, scope, "new", size=8000)
    assert owner.schedule(tx, None, scope) == ("task-1",)
    batch = tx.read("remember_batches", "task-1")
    assert [x["memory_id"] for x in batch["refs"]] == ["new"]
    assert batch["context_refs"] == []
    for key in ("old0", "old1", "old2", "foreign"):
        assert tx.read("remember_pending", key)["state"] == "processed"
        assert "task_id" not in tx.read("remember_pending", key)
    assert tx.read("remember_pending", "foreign_pending")["state"] == "pending"
    assert "task_id" not in tx.read("remember_pending", "foreign_pending")
    assert tx.read("remember_pending", "new")["state"] == "scheduled"
    assert tx.read("remember_pending", "new")["task_id"] == "task-1"


def test_unprocessed_adjacent_message_blocks_reaching_older_context():
    owner, tx, scope = scheduler()
    add(tx, scope, "old", state="processed", at="2026-10-07T01:50:00Z")
    add(tx, scope, "waiting", state="waiting_compression", at="2026-10-07T01:51:00Z")
    add(tx, scope, "new", size=8000)
    owner.schedule(tx, None, scope)
    assert tx.read("remember_batches", "task-1")["context_refs"] == []


@pytest.mark.parametrize(
    "state,effect,released",
    [
        ("failed", "no_effect", True),
        ("attention_required", "unknown", False),
        ("running", "not_started", False),
        ("retry_wait", "no_effect", False),
    ],
)
def test_only_confirmed_compression_terminal_releases_pending(state, effect, released):
    from aether_agent_memory.runtime.contracts.models import Principal, TrustedContext

    owner, tx, scope = scheduler()
    owner.processing_seconds = 86400
    owner.identity.revalidate = lambda *args: None
    ctx = TrustedContext(
        principal=Principal(principal_id="u", home_scope=scope, permissions=(), auth_epoch=1),
        request_id="req",
        operation_id="op",
        trace_id="a" * 32,
        span_id="b" * 16,
        deadline_at="2026-10-07T03:00:00.000Z",
    )
    add(tx, scope, "new", size=1, state="waiting_compression")
    row = tx.read("remember_pending", "new")
    tx.write(
        "remember_pending",
        "new",
        {**row, "context": ctx.model_dump(mode="json"), "compression_task_id": "compression-1"},
    )
    tx.write("tasks", "compression-1", {"record": {"state": state, "effect_status": effect}})
    assert bool(owner.periodic_pending(tx, "new")) is released
    assert (tx.read("remember_pending", "new")["state"] == "scheduled") is released
