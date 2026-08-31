from __future__ import annotations


class ContextProjectionSupersededError(RuntimeError):
    """The queued work targets an older Context item revision."""


class ContextRevisionConflictError(RuntimeError):
    """A stale Context fact attempted to replace a newer revision."""


__all__ = ["ContextProjectionSupersededError", "ContextRevisionConflictError"]
