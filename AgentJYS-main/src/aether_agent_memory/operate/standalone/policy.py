"""Bounded exponential statistics and hysteresis, based on the earlier Operate plan."""

import math
from dataclasses import dataclass

from .models import Stats, Tier


@dataclass(frozen=True)
class Settings:
    buffer_limit: int = 1000
    high_watermark: int = 800
    low_watermark: int = 600
    max_memories: int = 10000
    stats_retention_seconds: float = 3600
    dedup_limit: int = 100000
    dedup_seconds: float = 86400
    decay_seconds: float = 3600
    hot_up: float = 0.70
    hot_down: float = 0.55
    warm_up: float = 0.25
    warm_down: float = 0.15
    audit_seconds: float = 300
    retry_seconds: float = 5
    io_timeout_seconds: float = 3
    workers: int = 4
    batch_size: int = 64
    history_limit: int = 200

    def __post_init__(self) -> None:
        counts = (
            self.buffer_limit,
            self.high_watermark,
            self.low_watermark,
            self.max_memories,
            self.dedup_limit,
            self.workers,
            self.batch_size,
            self.history_limit,
        )
        if any(type(n) is not int or n < 0 for n in counts):
            raise ValueError("counts must be nonnegative integers")
        if not 0 <= self.low_watermark < self.high_watermark < self.buffer_limit:
            raise ValueError("require low < high < hard buffer limit")
        if (
            min(
                self.max_memories,
                self.dedup_limit,
                self.workers,
                self.batch_size,
                self.history_limit,
            )
            < 1
        ):
            raise ValueError("limits must be positive")
        if not 0 <= self.warm_down < self.warm_up < self.hot_down < self.hot_up <= 1:
            raise ValueError("invalid hysteresis thresholds")
        if any(
            not math.isfinite(n) or n <= 0
            for n in (
                self.decay_seconds,
                self.dedup_seconds,
                self.stats_retention_seconds,
                self.audit_seconds,
                self.retry_seconds,
                self.io_timeout_seconds,
            )
        ):
            raise ValueError("durations must be finite and positive")


def accumulate(stats: Stats, event_at: float, now: float, settings: Settings) -> None:
    event_at = min(now, event_at)
    stats.access_sum = stats.access_sum * math.exp(
        -max(0, now - stats.updated_at) / settings.decay_seconds
    ) + math.exp(-max(0, now - event_at) / settings.decay_seconds)
    stats.updated_at = now
    stats.last_access = max(
        stats.last_access if stats.last_access is not None else event_at, event_at
    )
    stats.access_count += 1


def heat_at(stats: Stats, now: float, settings: Settings) -> float:
    effective = stats.access_sum * math.exp(
        -max(0, now - stats.updated_at) / settings.decay_seconds
    )
    recency = (
        math.exp(-max(0, now - stats.last_access) / settings.decay_seconds)
        if stats.last_access is not None
        else 0.0
    )
    return 0.6 * (1 - math.exp(-effective / 5)) + 0.2 * stats.memory.importance + 0.2 * recency


def evaluate(stats: Stats, now: float, settings: Settings) -> Tier:
    heat = stats.heat = heat_at(stats, now, settings)
    stats.evaluated_at = now
    if heat >= settings.hot_up or (stats.desired == "hot" and heat >= settings.hot_down):
        stats.desired = "hot"
    elif heat >= settings.warm_up or (
        stats.desired in {"warm", "hot"} and heat >= settings.warm_down
    ):
        stats.desired = "warm"
    else:
        stats.desired = "cold"
    return stats.desired


def next_delay(stats: Stats, now: float, settings: Settings) -> float:
    """Find a threshold crossing once per reconciliation, not on every clock tick."""
    threshold = {"hot": settings.hot_down, "warm": settings.warm_down}.get(stats.desired)
    if threshold is None or heat_at(stats, now + settings.audit_seconds, settings) >= threshold:
        return settings.audit_seconds
    low, high = 0.0, settings.audit_seconds
    for _ in range(40):
        middle = (low + high) / 2
        if heat_at(stats, now + middle, settings) >= threshold:
            low = middle
        else:
            high = middle
    return max(0.01, high + 0.01)
