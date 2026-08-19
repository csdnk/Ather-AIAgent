from uuid import NAMESPACE_URL, uuid5

from aether_agent_memory.b3.models import (
    ActionType,
    HeuristicPolicyConfig,
    ResourceState,
    SchedulableObject,
    ScheduleAction,
    ScheduleRequest,
)
from aether_agent_memory.core.enums import StorageTier

_TIERS = [
    StorageTier.L0_DRAM,
    StorageTier.L1_NVME,
    StorageTier.L2_HDD,
    StorageTier.L3_OBJECT,
    StorageTier.L4_ARCHIVE,
]


class HeuristicPolicy:
    """Phase-1 policy: frequency + semantic value + recency + migration cost."""

    def __init__(self, config: HeuristicPolicyConfig | None = None) -> None:
        self.config = config or HeuristicPolicyConfig()

    def decide(self, request: ScheduleRequest) -> list[ScheduleAction]:
        return [
            self.decide_one(
                obj,
                request.resource_state,
                request.trace_id,
                request.request_id,
            )
            for obj in request.objects
        ]

    def decide_one(
        self,
        obj: SchedulableObject,
        resources: ResourceState,
        trace_id: str,
        request_id: str,
    ) -> ScheduleAction:
        score, components = self.score(obj, resources)
        action_type, target = self._select_action(obj, resources, score)
        reason = (
            f"score={score:.3f}; frequency={components['frequency']:.3f}; "
            f"semantic={components['semantic']:.3f}; recency={components['recency']:.3f}; "
            f"cost={components['cost']:.3f}"
        )
        return ScheduleAction(
            action_id=uuid5(
                NAMESPACE_URL,
                f"{request_id}:{obj.object_id}:{action_type.value}:{target}",
            ).hex,
            request_id=request_id,
            action_type=action_type,
            object_id=obj.object_id,
            object_type=obj.object_type,
            source_tier=obj.current_tier,
            target_tier=target,
            priority=round(score * 100),
            reason=reason,
            trace_id=trace_id,
            policy_version=self.config.policy_version,
            expected_effect=self._expected_effect(action_type, target),
            callback_required=action_type not in (ActionType.KEEP, ActionType.PIN),
            score=score,
            score_frequency=components["frequency"],
            score_semantic=components["semantic"],
            score_decay=components["recency"],
            score_cost=components["cost"],
            metadata=dict(obj.metadata),
        )

    def score(
        self,
        obj: SchedulableObject,
        resources: ResourceState,
    ) -> tuple[float, dict[str, float]]:
        frequency = 0.75 * obj.access.access_frequency + 0.25 * obj.access.hit_rate
        semantic = (
            obj.semantic.semantic_relevance
            + obj.semantic.importance
            + obj.semantic.task_relevance
            + obj.business_priority
        ) / 4.0
        components = {
            "frequency": frequency,
            "semantic": semantic,
            "recency": obj.access.recency_score,
            "cost": 1.0 - resources.migration_cost_score,
        }
        score = (
            self.config.frequency_weight * components["frequency"]
            + self.config.semantic_weight * components["semantic"]
            + self.config.recency_weight * components["recency"]
            + self.config.cost_weight * components["cost"]
        )
        return min(max(score, 0.0), 1.0), components

    def _select_action(
        self,
        obj: SchedulableObject,
        resources: ResourceState,
        score: float,
    ) -> tuple[ActionType, StorageTier | None]:
        if obj.pinned:
            return ActionType.PIN, obj.current_tier
        if not obj.migratable or not resources.network_available:
            return ActionType.KEEP, obj.current_tier
        if obj.expired and self.config.allow_evict and score <= self.config.eviction_threshold:
            return ActionType.EVICT, None

        promote_target = self._neighbor(obj.current_tier, hotter=True)
        if (
            obj.semantic.task_relevance >= self.config.prefetch_threshold
            and promote_target is not None
            and self._target_available(promote_target, resources, for_promotion=True)
        ):
            return ActionType.PREFETCH, promote_target
        if (
            score >= self.config.promote_threshold
            and promote_target is not None
            and self._target_available(promote_target, resources, for_promotion=True)
        ):
            return ActionType.PROMOTE, promote_target

        demote_target = self._neighbor(obj.current_tier, hotter=False)
        if (
            score <= self.config.demote_threshold
            and demote_target is not None
            and self._target_available(demote_target, resources, for_promotion=False)
        ):
            return ActionType.DEMOTE, demote_target
        return ActionType.KEEP, obj.current_tier

    @staticmethod
    def _neighbor(tier: StorageTier, *, hotter: bool) -> StorageTier | None:
        index = _TIERS.index(tier)
        target_index = index - 1 if hotter else index + 1
        if target_index < 0 or target_index >= len(_TIERS):
            return None
        return _TIERS[target_index]

    def _target_available(
        self,
        target: StorageTier,
        resources: ResourceState,
        *,
        for_promotion: bool,
    ) -> bool:
        state = resources.tiers.get(target)
        if state is None:
            return True
        allowed = state.allow_promote if for_promotion else state.allow_demote
        return bool(allowed and state.utilization < self.config.target_max_utilization)

    @staticmethod
    def _expected_effect(action: ActionType, target: StorageTier | None) -> str:
        if action in (ActionType.PROMOTE, ActionType.PREFETCH):
            return f"reduce future access latency on {target.value if target else 'hot tier'}"
        if action == ActionType.DEMOTE:
            return f"release hot-tier capacity to {target.value if target else 'cold tier'}"
        if action == ActionType.EVICT:
            return "release capacity for expired low-value object"
        if action == ActionType.PIN:
            return "preserve authoritative object on its current tier"
        if action == ActionType.UNPIN:
            return "release object from explicit tier protection"
        return "avoid unnecessary migration"
