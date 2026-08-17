from datetime import UTC, datetime

from aether_agent_memory.core.memory import Memory


def is_ttl_expired(memory: Memory, now: datetime | None = None) -> bool:
    return memory.is_expired(now=now)


def ebb_decay_weight(
    memory: Memory,
    now: datetime | None = None,
    half_life_hours: float = 168.0,
) -> float:
    current = now or datetime.now(UTC)
    age = current - memory.created_at
    age_hours = max(age.total_seconds() / 3600.0, 0.0)
    if age_hours <= 0:
        return 1.0
    weight = float(0.5 ** (age_hours / half_life_hours))
    return max(weight, 0.0)
