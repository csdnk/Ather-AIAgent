"""Stateful in-memory P2 behavior model. Only advance() executes pending effects.

No database assumptions. seed() is a fixture input, never called by reconciliation.
Capacity is global in this demo; Memory identities include tenant and scope.
"""

from collections import OrderedDict
from dataclasses import dataclass, replace
from hashlib import sha256

from .models import Feedback, Intent, Memory, MemoryKey, Observation, Tier
from .ports import SubmissionRejectedError


@dataclass
class ObjectState:
    memory: Memory
    content: bytes
    base: Tier = "cold"
    hot_content: bytes | None = None
    epoch: int = 0


@dataclass
class Action:
    intent: Intent
    feedback: Feedback
    reserved: int = 0


class MockP2:
    def __init__(
        self,
        *,
        capacities: dict[Tier, int] | None = None,
        action_limit: int = 10000,
        object_limit: int = 10000,
    ) -> None:
        self.capacities: dict[Tier, int] = {"cold": 10**9, "warm": 10**8, "hot": 10**7}
        self.capacities.update(capacities or {})
        if any(type(v) is not int or v < 0 for v in self.capacities.values()):
            raise ValueError("capacity must be nonnegative bytes")
        if action_limit < 1 or object_limit < 1:
            raise ValueError("simulator limits must be positive")
        self.action_limit, self.object_limit = action_limit, object_limit
        self.objects: dict[MemoryKey, ObjectState] = {}
        self.actions: OrderedDict[str, Action] = OrderedDict()
        self.unknown_keys: set[MemoryKey] = set()
        self.resources_unavailable = False
        self.drop_next_response = False
        self.query_unavailable = False
        self.fail_next_action = False
        self.effects = 0

    def seed(
        self,
        key: MemoryKey,
        content: str,
        *,
        base: Tier = "cold",
        version: int = 1,
        importance: float = 0.5,
    ) -> Memory:
        if base not in {"cold", "warm"}:
            raise ValueError("base must be cold or warm")
        raw = content.encode("utf-8")
        memory = Memory(key, version, sha256(raw).hexdigest(), len(raw), importance)
        old = self.objects.get(key)
        if old and (
            version < old.memory.version or (version == old.memory.version and memory != old.memory)
        ):
            raise ValueError("version cannot regress or change content in place")
        if old and memory == old.memory:
            return memory
        if old is None and len(self.objects) >= self.object_limit:
            raise BufferError("P2 fixture object capacity reached")
        free = self._free()[base] + (len(old.content) if old and old.base == base else 0)
        if free < len(raw):
            raise BufferError("insufficient fixture capacity")
        self.objects[key] = ObjectState(memory, raw, base, epoch=old.epoch + 1 if old else 0)
        return memory

    def _free(self) -> dict[Tier, int]:
        free = dict(self.capacities)
        for obj in self.objects.values():
            free[obj.base] -= len(obj.content)
            free["hot"] -= len(obj.hot_content) if obj.hot_content is not None else 0
        for action in self.actions.values():
            if action.feedback.state == "running":
                free[action.intent.target] -= action.reserved
        return {tier: max(0, value) for tier, value in free.items()}

    async def observe(self, key: MemoryKey) -> Observation | None:
        obj = self.objects.get(key)
        if obj is None:
            return None
        return Observation(
            obj.memory,
            obj.epoch,
            obj.base,
            obj.hot_content is not None,
            key not in self.unknown_keys,
            sha256(obj.content).hexdigest() == obj.memory.content_hash,
        )

    async def resources(self) -> dict[Tier, int]:
        if self.resources_unavailable:
            raise ConnectionError("simulated resource query outage")
        return self._free()

    async def submit(self, intent: Intent) -> Feedback:
        existing = self.actions.get(intent.action_id)
        if existing:
            if existing.intent != intent:
                raise ValueError("idempotency conflict: action ID changed intent")
            return existing.feedback
        # No TTL eviction of action IDs: losing these IDs would break idempotency.
        if len(self.actions) >= self.action_limit:
            raise SubmissionRejectedError("P2 action journal full; start a new isolated demo")
        obj = self.objects.get(intent.memory.key)
        reason = ""
        if obj is None or obj.memory != intent.memory or obj.epoch != intent.epoch:
            reason = "stale or missing object"
        elif any(
            a.feedback.state == "running" and a.intent.memory.key == intent.memory.key
            for a in self.actions.values()
        ):
            reason = "another action owns this Memory"
        elif intent.operation == "ensure_hot_replica" and intent.target != "hot":
            reason = "invalid hot target"
        elif intent.operation in {"move_base", "remove_hot_replica"} and intent.target == "hot":
            reason = "invalid base target"
        elif intent.operation == "remove_hot_replica" and obj.base != intent.target:
            reason = "retained base is not at target"
        reserved = 0
        if not reason and obj is not None:
            if intent.operation == "ensure_hot_replica":
                reserved = max(
                    0,
                    intent.memory.size
                    - (len(obj.hot_content) if obj.hot_content is not None else 0),
                )
            elif intent.operation == "move_base" and obj.base != intent.target:
                reserved = intent.memory.size
            if self._free()[intent.target] < reserved:
                reason = "insufficient capacity including reservations"
        feedback = Feedback(intent.action_id, "failed" if reason else "running", reason)
        self.actions[intent.action_id] = Action(intent, feedback, 0 if reason else reserved)
        if self.drop_next_response:
            self.drop_next_response = False
            raise ConnectionError("accepted action but response was lost")
        return feedback

    async def query(self, action_id: str) -> Feedback:
        if self.query_unavailable:
            raise ConnectionError("simulated action query outage")
        action = self.actions.get(action_id)
        return action.feedback if action else Feedback(action_id, "not_found")

    async def verify_read(self, memory: Memory, tier: Tier) -> bool:
        obj = self.objects.get(memory.key)
        if obj is None or obj.memory != memory:
            return False
        data = obj.hot_content if tier == "hot" else obj.content if obj.base == tier else None
        return data is not None and sha256(data).hexdigest() == memory.content_hash

    def advance(self, action_id: str | None = None) -> int:
        """The scenario driver calls this; queries and Operate never call it."""
        completed = 0
        for key, action in list(self.actions.items()):
            if action.feedback.state != "running" or (action_id and key != action_id):
                continue
            intent = action.intent
            obj = self.objects.get(intent.memory.key)
            reason = ""
            if self.fail_next_action:
                self.fail_next_action = False
                reason = "injected execution failure"
            elif obj is None or obj.memory != intent.memory or obj.epoch != intent.epoch:
                reason = "object changed before execution"
            elif sha256(obj.content).hexdigest() != intent.memory.content_hash:
                reason = "base content cannot pass read verification"
            if reason:
                action.feedback = Feedback(key, "failed", reason)
            else:
                assert obj is not None
                if intent.operation == "ensure_hot_replica":
                    obj.hot_content = bytes(obj.content)
                elif intent.operation == "move_base":
                    obj.base = intent.target
                else:
                    obj.hot_content = None
                obj.epoch += 1
                self.effects += 1
                action.feedback = replace(action.feedback, state="succeeded")
            action.reserved = 0
            completed += 1
        return completed
