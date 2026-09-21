from aether_agent_memory.core.enums import MemoryState
from aether_agent_memory.core.exceptions import InvalidStateTransitionError

ALLOWED_TRANSITIONS: dict[MemoryState, set[MemoryState]] = {
    MemoryState.ACTIVE: {MemoryState.ARCHIVED, MemoryState.EXPIRED, MemoryState.SUPERSEDED},
    MemoryState.ARCHIVED: {MemoryState.EXPIRED},
    MemoryState.EXPIRED: set(),
    MemoryState.SUPERSEDED: set(),
}


def transition(current: MemoryState, target: MemoryState) -> MemoryState:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidStateTransitionError(f"illegal transition {current.value} -> {target.value}")
    return target
