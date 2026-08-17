class B2MemoryError(Exception):
    pass


class MemoryNotFoundError(B2MemoryError):
    pass


class MemoryExpiredError(B2MemoryError):
    pass


class ContextBudgetExceededError(B2MemoryError):
    pass


class EmbeddingError(B2MemoryError):
    pass


class StorageError(B2MemoryError):
    pass


class InvalidStateTransitionError(B2MemoryError):
    pass
