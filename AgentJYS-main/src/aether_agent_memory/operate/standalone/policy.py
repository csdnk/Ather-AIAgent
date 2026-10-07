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
    # Legacy construction compatibility only; no polling cap or retry override.
    audit_seconds: float = 300
    retry_seconds: float = 60
    evaluation_window_seconds: float = 60
    evaluation_timeout_seconds: float = 86400
    retry_delays: tuple[float, ...] = (60, 300, 900, 3600)
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
        if not 0 <= self.hot_down < self.hot_up <= 1:
            raise ValueError("invalid hysteresis thresholds")
        if not self.retry_delays or tuple(sorted(self.retry_delays)) != self.retry_delays:
            raise ValueError("retry delays must be nonempty and increasing")
        if any(
            not math.isfinite(n) or n <= 0
            for n in (
                self.decay_seconds,
                self.dedup_seconds,
                self.stats_retention_seconds,
                self.audit_seconds,
                self.retry_seconds,
                self.evaluation_window_seconds,
                self.evaluation_timeout_seconds,
                *self.retry_delays,
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
    else:
        stats.desired = "cold"
    return stats.desired


def next_delay(stats: Stats, now: float, settings: Settings) -> float | None:
    """Predict cooling without a periodic audit cap; None means no future crossing."""
    if stats.desired != "hot":
        return None
    threshold = settings.hot_down
    # Importance does not decay. Some custom policies will never cross the threshold.
    if 0.2 * stats.memory.importance >= threshold:
        return None
    low, high = 0.0, settings.decay_seconds
    while heat_at(stats, now + high, settings) >= threshold:
        high *= 2
    for _ in range(48):
        middle = (low + high) / 2
        if heat_at(stats, now + middle, settings) >= threshold:
            low = middle
        else:
            high = middle
    return max(0.01, high + 0.01)
