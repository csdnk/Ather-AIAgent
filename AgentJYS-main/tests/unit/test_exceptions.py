import pytest

from aether_agent_memory.core.exceptions import (
    B2MemoryError,
    ContextBudgetExceededError,
    EmbeddingError,
    InvalidStateTransitionError,
    MemoryExpiredError,
    MemoryNotFoundError,
    StorageError,
)


@pytest.mark.unit
def test_base_error_is_exception() -> None:
    assert issubclass(B2MemoryError, Exception)


@pytest.mark.unit
def test_all_subclasses_inherit_base() -> None:
    for exc in [
        MemoryNotFoundError,
        MemoryExpiredError,
        ContextBudgetExceededError,
        EmbeddingError,
        StorageError,
        InvalidStateTransitionError,
    ]:
        assert issubclass(exc, B2MemoryError)


@pytest.mark.unit
def test_errors_carry_message() -> None:
    with pytest.raises(MemoryNotFoundError, match="missing"):
        raise MemoryNotFoundError("missing memory")
