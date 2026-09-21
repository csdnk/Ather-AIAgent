"""Stateful P3 storage-control contract simulator, not a physical data plane."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from aether_agent_memory.core.scope import Scope
from aether_agent_memory.operate.models import (
    ActionFeedback,
    ActionState,
    ActionType,
    ActuationTarget,
    PlacementObservation,
    RepresentationRef,
    ResourceBudget,
    TargetRole,
    TierAction,
)


class StorageControlSimulator:
    def __init__(self) -> None:
        self.representations: dict[str, RepresentationRef] = {}
        self.placements: dict[str, PlacementObservation] = {}
        self.sizes: dict[str, int] = {}
        self.capacity: dict[str, int] = {}
        self.used: dict[str, int] = {}
        self.actions: dict[str, TierAction] = {}
        self.outcomes: dict[str, ActionFeedback] = {}
        self.feedback: list[ActionFeedback] = []
        self.faults: dict[str, str] = {}
        self.stale_observations: dict[str, PlacementObservation] = {}
        self.submission_count = 0

    @staticmethod
    def _key(ref: RepresentationRef, scope: Scope) -> str:
        return json.dumps([scope.as_dict(), ref.key], sort_keys=True)

    def register(
        self,
        ref: RepresentationRef,
        scope: Scope,
        targets: list[ActuationTarget],
        *,
        size_bytes: int,
    ) -> None:
        key = self._key(ref, scope)
        previous = self.placements.get(key)
        if previous:
            for target in previous.targets:
                self.used[target.key] = self.used.get(target.key, 0) - self.sizes[key]
        self.representations[key] = ref
        self.sizes[key] = size_bytes
        self.placements[key] = PlacementObservation(
            representation=ref,
            scope=scope,
            targets=list(targets),
            epoch=(previous.epoch + 1 if previous else 1),
        )
        for target in targets:
            self.used[target.key] = self.used.get(target.key, 0) + size_bytes

    def set_capacity(self, target: ActuationTarget, capacity: int) -> None:
        if capacity < 0:
            raise ValueError("capacity cannot be negative")
        self.capacity[target.key] = capacity

    def resources(self) -> ResourceBudget:
        return ResourceBudget(capacity_bytes=dict(self.capacity), used_bytes=dict(self.used))

    def set_placements(
        self, ref: RepresentationRef, scope: Scope, targets: list[ActuationTarget]
    ) -> None:
        self.register(ref, scope, targets, size_bytes=self.sizes[self._key(ref, scope)])

    async def observe(
        self, representation: RepresentationRef, scope: Scope
    ) -> PlacementObservation:
        key = self._key(representation, scope)
        if key not in self.placements:
            raise ValueError("representation is not registered")
        result = self.stale_observations.get(key, self.placements[key])
        return result.model_copy(deep=True, update={"observed_at": datetime.now(UTC)})

    async def submit(self, action: TierAction) -> ActionFeedback:
        previous = self.actions.get(action.action_id)
        if previous:
            if previous.intent() != action.intent():
                raise ValueError("duplicate action_id with conflicting intent")
            return self.outcomes[action.action_id].model_copy(deep=True)
        self.submission_count += 1
        self.actions[action.action_id] = action.model_copy(deep=True)
        self.outcomes[action.action_id] = ActionFeedback(
            action_id=action.action_id, state=ActionState.SUBMITTED
        )
        fault = self.faults.get(action.action_id)
        if fault == "running":
            return self.outcomes[action.action_id].model_copy(deep=True)
        if fault == "accepted_then_failed":
            self._outcome(action, ActionState.FAILED, error="accepted then failed")
            return ActionFeedback(action_id=action.action_id, state=ActionState.SUBMITTED)
        result = self.finish(action.action_id)
        if fault == "timeout_after_success":
            raise TimeoutError("response lost after execution")
        if fault == "feedback_lost":
            self.feedback = [f for f in self.feedback if f.action_id != action.action_id]
            return ActionFeedback(action_id=action.action_id, state=ActionState.UNKNOWN)
        return result

    def finish(self, action_id: str) -> ActionFeedback:
        action = self.actions[action_id]
        prior = self.outcomes[action_id]
        if prior.state in {ActionState.SUCCEEDED, ActionState.FAILED}:
            return prior.model_copy(deep=True)
        key = self._key(action.representation, action.scope)
        observed = self.placements.get(key)
        if observed is None or observed.representation != action.representation:
            return self._outcome(action, ActionState.FAILED, error="stale representation revision")
        if observed.epoch != action.expected_epoch:
            return self._outcome(action, ActionState.FAILED, error="stale placement epoch")
        if action.size_bytes != self.sizes[key]:
            return self._outcome(action, ActionState.FAILED, error="representation size mismatch")
        if self.faults.get(action_id) == "divergent":
            return self._outcome(action, ActionState.SUCCEEDED, observed=observed)
        targets = list(observed.targets)
        target = action.target
        if (
            action.action_type in {ActionType.PROMOTE, ActionType.PREFETCH}
            and target not in targets
        ):
            if target.role == TargetRole.AUTHORITATIVE:
                return self._outcome(
                    action, ActionState.FAILED, error="cannot create primary ownership by migration"
                )
            free = self.capacity.get(target.key, 0) - self.used.get(target.key, 0)
            if action.size_bytes > free:
                return self._outcome(action, ActionState.FAILED, error="capacity full")
            targets.append(target)
            self.used[target.key] = self.used.get(target.key, 0) + action.size_bytes
        elif action.action_type == ActionType.DEMOTE and target in targets:
            if target.role == TargetRole.AUTHORITATIVE:
                return self._outcome(
                    action, ActionState.FAILED, error="cannot reclaim authoritative copy"
                )
            targets.remove(target)
            self.used[target.key] -= action.size_bytes
        if self.faults.get(action_id) != "divergent":
            self.placements[key] = observed.model_copy(
                update={
                    "targets": targets,
                    "epoch": observed.epoch + 1,
                    "observed_at": datetime.now(UTC),
                }
            )
        return self._outcome(action, ActionState.SUCCEEDED, observed=self.placements[key])

    def _outcome(
        self,
        action: TierAction,
        state: ActionState,
        *,
        observed: PlacementObservation | None = None,
        error: str | None = None,
    ) -> ActionFeedback:
        result = ActionFeedback(
            action_id=action.action_id, state=state, observed=observed, error=error
        )
        self.outcomes[action.action_id] = result
        self.actions[action.action_id] = action.model_copy(update={"state": state})
        self.feedback.append(result)
        return result.model_copy(deep=True)

    async def query(self, action: TierAction) -> ActionFeedback | None:
        stored = self.actions.get(action.action_id)
        if stored and stored.intent() != action.intent():
            raise ValueError("query intent does not match action_id")
        outcome = self.outcomes.get(action.action_id)
        return outcome.model_copy(deep=True) if outcome else None
