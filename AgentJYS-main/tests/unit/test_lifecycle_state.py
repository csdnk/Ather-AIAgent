import pytest

from aether_agent_memory.core.enums import MemoryState
from aether_agent_memory.core.exceptions import InvalidStateTransitionError
from aether_agent_memory.lifecycle.state import ALLOWED_TRANSITIONS, transition


@pytest.mark.unit
def test_allowed_transitions_active() -> None:
    assert MemoryState.ARCHIVED in ALLOWED_TRANSITIONS[MemoryState.ACTIVE]
    assert MemoryState.EXPIRED in ALLOWED_TRANSITIONS[MemoryState.ACTIVE]
    assert MemoryState.SUPERSEDED in ALLOWED_TRANSITIONS[MemoryState.ACTIVE]


@pytest.mark.unit
def test_allowed_transitions_archived() -> None:
    assert MemoryState.EXPIRED in ALLOWED_TRANSITIONS[MemoryState.ARCHIVED]


@pytest.mark.unit
def test_terminal_states_have_no_outgoing() -> None:
    assert ALLOWED_TRANSITIONS[MemoryState.EXPIRED] == set()
    assert ALLOWED_TRANSITIONS[MemoryState.SUPERSEDED] == set()


@pytest.mark.unit
def test_transition_active_to_archived() -> None:
    assert transition(MemoryState.ACTIVE, MemoryState.ARCHIVED) == MemoryState.ARCHIVED


@pytest.mark.unit
def test_transition_active_to_superseded() -> None:
    assert transition(MemoryState.ACTIVE, MemoryState.SUPERSEDED) == MemoryState.SUPERSEDED


@pytest.mark.unit
def test_transition_archived_to_expired() -> None:
    assert transition(MemoryState.ARCHIVED, MemoryState.EXPIRED) == MemoryState.EXPIRED


@pytest.mark.unit
def test_transition_illegal_raises() -> None:
    with pytest.raises(InvalidStateTransitionError):
        transition(MemoryState.EXPIRED, MemoryState.ACTIVE)
    with pytest.raises(InvalidStateTransitionError):
        transition(MemoryState.ACTIVE, MemoryState.ACTIVE)


@pytest.mark.unit
def test_transition_same_state_noop_allowed() -> None:
    pass


@pytest.mark.unit
def test_transition_expired_to_anything_raises() -> None:
    for target in [MemoryState.ACTIVE, MemoryState.ARCHIVED, MemoryState.SUPERSEDED]:
        with pytest.raises(InvalidStateTransitionError):
            transition(MemoryState.EXPIRED, target)
