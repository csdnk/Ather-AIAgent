"""Durable, scope-isolated high-watermark admission for retained heat statistics.

Membership/count changes share the heat transaction. A latched pressure episode
visits one bounded page per scheduler page call, then waits for the low watermark to
rearm. No body deletion or placement decisions are performed here.
"""

from typing import Any

from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.foundation.common import fingerprint
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .triggers import TriggerSchedule, seconds


class BufferSchedule(TriggerSchedule):
    def track_heat(self, tx: MetadataTransaction, key: str, memory: MemoryRef) -> None:
        if tx.read("operate_buffer_members", key):
            return  # Repeated reads and version replacement count as one scoped memory.
        scope_key = fingerprint(memory.scope.model_dump(mode="json"))
        tx.write("operate_buffer_members", key, {"scope_key": scope_key})
        tx.write("operate_buffer_index", scope_key + ":" + key, {"memory_key": key})
        control = tx.read("operate_buffer_scopes", scope_key) or {
            "count": 0,
            "latched": False,
            "episode": 0,
        }
        control["count"] += 1
        if control["count"] >= self.settings.high_watermark and not control["latched"]:
            control["latched"] = True
            control["episode"] += 1
            tx.write(
                "operate_buffer_pressure",
                scope_key,
                {"cursor": scope_key + ":", "episode": control["episode"]},
            )
        tx.write("operate_buffer_scopes", scope_key, control)

    def write_heat(
        self, tx: MetadataTransaction, key: str, memory: MemoryRef, value: dict[str, Any]
    ) -> None:
        tx.write("operate_heat", key, value)
        self.track_heat(tx, key, memory)

    def drop_heat(self, tx: MetadataTransaction, key: str) -> None:
        super().drop_heat(tx, key)
        member = tx.read("operate_buffer_members", key)
        if not member:
            return  # Legacy rows not yet visited by the bounded upgrade sweep.
        scope_key = member["scope_key"]
        tx.raw.delete("p3_rf_operate_buffer_members", "system", key)
        tx.raw.delete("p3_rf_operate_buffer_index", "system", scope_key + ":" + key)
        control = tx.read("operate_buffer_scopes", scope_key)
        control["count"] -= 1
        if control["count"] <= self.settings.low_watermark:
            control["latched"] = False
            tx.raw.delete("p3_rf_operate_buffer_pressure", "system", scope_key)
        tx.write("operate_buffer_scopes", scope_key, control)

    def upgrade_buffer(self, tx: MetadataTransaction) -> bool:
        control = tx.read("operate_buffer_upgrade", "control") or {"cursor": "", "done": False}
        if control["done"]:
            return True
        rows = tx.rows_after("operate_heat", control["cursor"], limit=self.settings.batch_size)
        for key, heat in rows:
            view = tx.read("operate_views", key)
            if view and view["memory"]["version"] == heat["version"]:
                self.track_heat(tx, key, MemoryRef.model_validate(view["memory"]))
            control["cursor"] = key
        control["done"] = len(rows) < self.settings.batch_size
        tx.write("operate_buffer_upgrade", "control", control)
        return bool(control["done"])

    def pressure_wakeup(self, tx: MetadataTransaction, key: str) -> None:
        view = tx.read("operate_views", key)
        if (
            not view
            or view.get("cleanup")
            or view.get("cleanup_completed")
            or not view.get("scheduler_event")
            # Stable cold statistics already have a metadata-only retirement;
            # waking them would reset retention and never release the buffer.
            or view.get("wake_reason") in {"retire_stats", "stable_cold", "authorization_required"}
            or view.get("waiting_capability") == self.capability_signature()
            or self.paused
            or not self.should_evaluate(cleanup=False)
        ):
            return
        memory = MemoryRef.model_validate(view["memory"])
        if self.pending(tx, memory):
            return  # Includes retries, unknown outcomes and manual attention.
        now = self.identity.clock()
        # Pressure can bypass the ordinary input-coalescing window, never a
        # dependency backoff. The due worker obtains this memory's own authority.
        when = max(now, view.get("retry_not_before") or now)
        due = view.get("next_evaluation_at")
        if due and seconds(due) <= seconds(when) and not view.get("dormant"):
            return  # An earlier wake-up already covers the pressure request.
        view["dormant"] = False
        self.schedule_at(tx, key, view, when, "buffer_high_watermark")

    def refresh_buffer(self, tx: MetadataTransaction) -> None:
        if not self.upgrade_buffer(tx):
            return  # Finish indexing historical rows before traversing a pressure episode.
        scopes = tx.rows_after("operate_buffer_pressure", "", limit=1)
        for scope_key, pressure in scopes:
            rows = tx.rows_after(
                "operate_buffer_index", pressure["cursor"], limit=self.settings.batch_size
            )
            members = [(k, v) for k, v in rows if k.startswith(scope_key + ":")]
            for index_key, member in members:
                self.pressure_wakeup(tx, member["memory_key"])
                pressure["cursor"] = index_key
            if len(members) < self.settings.batch_size:
                tx.raw.delete("p3_rf_operate_buffer_pressure", "system", scope_key)
            else:
                tx.write("operate_buffer_pressure", scope_key, pressure)

    def refresh_scheduler(self, tx: MetadataTransaction) -> None:
        super().refresh_scheduler(tx)
        self.refresh_buffer(tx)
