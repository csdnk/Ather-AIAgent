"""Permitted task edges. Authorization, lease and effect guards remain mandatory."""

from .models import TaskState as S

TASK_TRANSITIONS = {
    S.PENDING: frozenset({S.RUNNING, S.CANCELLED, S.FAILED}),
    S.RUNNING: frozenset(
        {
            S.SUCCEEDED,
            S.RETRY_WAIT,
            S.RECOVERY_WAIT,
            S.CANCELLED,
            S.FAILED,
            S.ATTENTION,
        }
    ),
    S.RETRY_WAIT: frozenset({S.RUNNING, S.CANCELLED, S.FAILED}),
    S.RECOVERY_WAIT: frozenset({S.RETRY_WAIT, S.SUCCEEDED, S.CANCELLED, S.FAILED, S.ATTENTION}),
    S.SUCCEEDED: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
    S.ATTENTION: frozenset(),
}
