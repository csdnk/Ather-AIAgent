"""Bounded observation of a command submitted exactly once."""

import math
import time
from collections.abc import Callable

from pydantic import BaseModel

from .client import P3ValidationClient
from .errors import PendingOperation, ValidationError


def resolve_operation[T: BaseModel](
    client: P3ValidationClient,
    call: Callable[[], T],
    model: type[T],
    *,
    operation_id: str,
    write: bool,
    wait_seconds: float = 60,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    on_pending: Callable[[str], None] | None = None,
) -> T:
    if not math.isfinite(wait_seconds) or wait_seconds <= 0:
        raise ValidationError(400, "invalid_argument", "观察期限必须是有限正数")
    deadline = clock() + wait_seconds
    try:
        return call()
    except PendingOperation as pending:
        job_id = pending.job_id
    if on_pending is not None:
        on_pending(job_id)
    try:
        while (remaining := deadline - clock()) > 0:
            state = client.operation(job_id, timeout_seconds=min(10.0, remaining))
            if state.state == "attention_required":
                raise ValidationError(409, "operation_unconfirmed", "任务效果尚待核对")
            if state.state in {"failed", "cancelled"}:
                raise ValidationError(
                    409, "operation_failed", "P3 报告任务未成功", operation_id=operation_id
                )
            if state.state == "succeeded":
                remaining = deadline - clock()
                if remaining <= 0:
                    break
                try:
                    return client.operation_result(
                        job_id, model, timeout_seconds=min(10.0, remaining)
                    )
                except PendingOperation as pending:
                    if pending.job_id != job_id:
                        raise ValidationError(
                            502, "upstream_protocol_error", "操作结果引用不一致"
                        ) from None
            sleep(min(0.5, max(0.0, deadline - clock())))
        raise ValidationError(504, "observation_timeout", "观察期限已到，任务结果未确认")
    except ValidationError as error:
        # A read failure after acceptance says nothing about the command's effect.
        raise ValidationError(
            error.status,
            error.code,
            error.message,
            operation_id=operation_id,
            write_outcome=(
                "unconfirmed" if write and error.code != "operation_failed" else "not_applicable"
            ),
        ) from None
