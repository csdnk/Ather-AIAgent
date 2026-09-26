"""Ebbinghaus-inspired retention heuristic, separate from truth and lifecycle.

Half-lives are policy choices to calibrate against task outcomes, not empirical
human-memory constants. Internal reads never reinforce memory.
"""

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DecayPolicy:
    version: str = "retention_v1"
    working_half_life_hours: float = 24.0
    episodic_half_life_hours: float = 168.0
    semantic_half_life_hours: float = 720.0
    reinforcement_gap_hours: float = 6.0
    reinforcement_gain: float = 0.25
    max_half_life_hours: float = 8760.0
    cooling_threshold: float = 0.6
    dormant_threshold: float = 0.2
    protected_importance: float = 0.8

    def __post_init__(self) -> None:
        values = (
            self.working_half_life_hours,
            self.episodic_half_life_hours,
            self.semantic_half_life_hours,
            self.reinforcement_gap_hours,
            self.max_half_life_hours,
        )
        if not all(math.isfinite(v) and v > 0 for v in values):
            raise ValueError("decay durations must be finite and positive")
        if not 0 < self.dormant_threshold < self.cooling_threshold < 1:
            raise ValueError("invalid decay thresholds")
        if not math.isfinite(self.reinforcement_gain) or not 0 <= self.reinforcement_gain <= 1:
            raise ValueError("invalid reinforcement gain")
        if not math.isfinite(self.protected_importance) or not 0 <= self.protected_importance <= 1:
            raise ValueError("invalid protection threshold")
        if self.max_half_life_hours < max(values[:3]):
            raise ValueError("maximum half-life must cover initial half-lives")


DEFAULT_POLICY = DecayPolicy()


def initial(kind: str, hour: float, policy: DecayPolicy = DEFAULT_POLICY) -> dict[str, Any]:
    half = {
        "working": policy.working_half_life_hours,
        "episodic": policy.episodic_half_life_hours,
        "semantic": policy.semantic_half_life_hours,
    }[kind]
    return {
        "anchor_hour": hour,
        "half_life_hours": half,
        "reinforcements": 0,
        "last_reinforced_hour": None,
        "policy_version": policy.version,
    }


def evaluate(
    state: dict[str, Any], hour: float, importance: float, policy: DecayPolicy = DEFAULT_POLICY
) -> dict[str, Any]:
    age = max(0.0, hour - state["anchor_hour"])
    raw = 2.0 ** (-age / state["half_life_hours"])
    protected = importance >= policy.protected_importance
    strength = max(raw, policy.cooling_threshold) if protected else raw
    band = (
        "active"
        if strength >= policy.cooling_threshold
        else "cooling"
        if strength >= policy.dormant_threshold
        else "dormant"
    )
    return {
        **state,
        "raw_strength": raw,
        "strength": strength,
        "band": band,
        "protected": protected,
        "elapsed_hours": age,
        "evaluated_hour": hour,
    }


def reinforce(state: dict[str, Any], hour: float, policy: DecayPolicy = DEFAULT_POLICY) -> bool:
    last = state["last_reinforced_hour"]
    if hour < state["anchor_hour"] or last is not None and hour == state["anchor_hour"]:
        return False
    # Every new, genuine use restores strength. Only half-life growth is rate limited.
    state["anchor_hour"] = hour
    if last is not None and hour - last < policy.reinforcement_gap_hours:
        return True
    state["half_life_hours"] = min(
        policy.max_half_life_hours, state["half_life_hours"] * (1 + policy.reinforcement_gain)
    )
    state["last_reinforced_hour"] = hour
    state["reinforcements"] += 1
    return True
