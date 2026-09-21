"""Status edges for one version; correction creates a new version atomically."""

from .models import MemoryStatus as S

MEMORY_TRANSITIONS = {
    S.ACTIVE: frozenset({S.ARCHIVED, S.SUPERSEDED, S.EXPIRED, S.DELETED}),
    S.ARCHIVED: frozenset({S.ACTIVE, S.SUPERSEDED, S.EXPIRED, S.DELETED}),
    S.SUPERSEDED: frozenset({S.DELETED}),
    S.EXPIRED: frozenset({S.DELETED}),
    S.DELETED: frozenset(),
}
