"""Unknown never transitions to a new submission without evidence."""

from .models import ActionState as S

ACTION_TRANSITIONS = {
    S.GENERATED: frozenset({S.SUBMITTED, S.CANCELLED, S.FAILED}),
    S.SUBMITTED: frozenset({S.SUCCEEDED, S.FAILED, S.UNKNOWN}),
    S.UNKNOWN: frozenset({S.SUCCEEDED, S.FAILED}),
    S.SUCCEEDED: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}
