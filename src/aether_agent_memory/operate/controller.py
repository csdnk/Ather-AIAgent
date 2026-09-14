from __future__ import annotations

from datetime import UTC, datetime

from aether_agent_memory.operate.models import (
    ActionFeedback,
    ActionState,
    ActionType,
    ExecutionMode,
    PlacementObservation,
    TierAction,
)
from aether_agent_memory.operate.ports import (
    ActionJournalPort,
    StorageControlPort,
    StorageControlUnavailableError,
)


class OperateController:
    def __init__(self, actuator: StorageControlPort, journal: ActionJournalPort) -> None:
        self.actuator = actuator
        self.journal = journal

    async def execute(self, action: TierAction) -> TierAction:
        current = await self.journal.reserve(action)
        if current.state in {ActionState.SUBMITTED, ActionState.UNKNOWN}:
            return await self.reconcile(current)
        if current.action_id != action.action_id:
            return current  # Another intent owns this representation/target fence.
        if current.execution_mode == ExecutionMode.SHADOW or current.state != ActionState.GENERATED:
            return current
        submitted = current.model_copy(update={"state": ActionState.SUBMITTED})
        if not await self.journal.transition(submitted, {ActionState.GENERATED}):
            return await self.journal.get(current.action_id) or current
        try:
            if submitted.action_type == ActionType.KEEP:
                feedback = ActionFeedback(
                    action_id=submitted.action_id,
                    state=ActionState.SUCCEEDED,
                    observed=await self.actuator.observe(submitted.representation, submitted.scope),
                )
            else:
                feedback = await self.actuator.submit(submitted)
            return await self._accept(submitted, feedback)
        except StorageControlUnavailableError as exc:
            return await self._save(submitted, ActionState.FAILED, error=str(exc))
        except Exception as exc:
            # Any post-dispatch failure may hide a successful remote action.
            return await self._save(
                submitted, ActionState.UNKNOWN, error=f"{type(exc).__name__}: {exc}"
            )

    async def reconcile(self, action: TierAction) -> TierAction:
        if action.state == ActionState.GENERATED and action.execution_mode == ExecutionMode.LIVE:
            # Submission always follows the durable GENERATED -> SUBMITTED CAS.
            # Cancel an abandoned reservation so a fresh plan can acquire its fence.
            # A concurrent submitter wins or loses that same CAS; never cancel SUBMITTED.
            abandoned = action.model_copy(
                update={
                    "state": ActionState.FAILED,
                    "error": "intent not submitted; replan required",
                }
            )
            await self.journal.transition(abandoned, {ActionState.GENERATED})
            action = await self.journal.get(action.action_id) or action
        if action.state not in {ActionState.UNKNOWN, ActionState.SUBMITTED}:
            return action
        try:
            feedback = await self.actuator.query(action)
            if feedback is not None:
                return await self._accept(action, feedback)
            observed = await self.actuator.observe(action.representation, action.scope)
            if self._satisfied(action, observed):
                return await self._save(action, ActionState.SUCCEEDED, observed=observed)
        except Exception:
            pass
        # No evidence is not evidence of failure. Never blindly re-submit UNKNOWN.
        return await self._save(action, ActionState.UNKNOWN, error="remote outcome unresolved")

    async def _accept(self, action: TierAction, feedback: ActionFeedback) -> TierAction:
        if feedback.action_id != action.action_id or feedback.state == ActionState.GENERATED:
            raise ValueError("invalid actuator feedback identity/state")
        observed = feedback.observed
        if feedback.state == ActionState.SUCCEEDED:
            if observed is None or not self._satisfied(action, observed):
                observed = await self.actuator.observe(action.representation, action.scope)
            if not self._satisfied(action, observed):
                return await self._save(
                    action, ActionState.UNKNOWN, error="desired placement differs from observed"
                )
        elif observed is not None and (
            observed.scope != action.scope or observed.representation != action.representation
        ):
            raise ValueError("feedback observation scope/revision mismatch")
        return await self._save(action, feedback.state, observed=observed, error=feedback.error)

    @staticmethod
    def _satisfied(action: TierAction, observed: PlacementObservation) -> bool:
        if observed.scope != action.scope or observed.representation != action.representation:
            return False
        age = (datetime.now(UTC) - observed.observed_at).total_seconds()
        if age > 60 or age < -5 or observed.epoch < action.expected_epoch:
            return False
        if action.action_type == ActionType.DEMOTE:
            return action.target not in observed.targets
        return action.target in observed.targets

    async def _save(
        self,
        action: TierAction,
        state: ActionState,
        *,
        observed: PlacementObservation | None = None,
        error: str | None = None,
    ) -> TierAction:
        updated = action.model_copy(update={"state": state, "observed": observed, "error": error})
        await self.journal.transition(updated, {ActionState.SUBMITTED, ActionState.UNKNOWN})
        return await self.journal.get(action.action_id) or updated
