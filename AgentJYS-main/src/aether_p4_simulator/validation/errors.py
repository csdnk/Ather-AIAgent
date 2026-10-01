"""Safe local errors. Never use upstream messages, URLs or exception text here."""

from typing import Literal

from pydantic import TypeAdapter
from pydantic import ValidationError as ModelError

from aether_agent_memory.runtime.contracts.models import Identifier

WriteOutcome = Literal["not_applicable", "unconfirmed"]


class ValidationError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        operation_id: str | None = None,
        write_outcome: WriteOutcome = "not_applicable",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.operation_id = operation_id
        self.write_outcome = write_outcome

    def to_dict(self) -> dict[str, str | int | None]:
        """Only these fields may cross the P4/browser boundary."""
        return {
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "operation_id": self.operation_id,
            "write_outcome": self.write_outcome,
        }


class PendingOperation(ValidationError):  # noqa: N818 - accepted-operation signal, not a failure
    """An accepted command: observe this job, never resubmit the command."""

    def __init__(
        self, job_id: str, *, operation_id: str | None = None, write: bool = False
    ) -> None:
        try:
            self.job_id = TypeAdapter(Identifier).validate_python(job_id, strict=True)
        except ModelError:
            raise ValidationError(502, "upstream_protocol_error", "P3 任务引用不合法") from None
        super().__init__(
            409,
            "request_in_progress",
            "P3 已受理，正在观察原任务",
            operation_id=operation_id,
            write_outcome="unconfirmed" if write else "not_applicable",
        )


# Bind business codes to statuses as well as names: a payload cannot turn a
# permission failure into a version conflict or a failed read into success.
_HTTP_ERRORS = {
    400: ("invalid_argument", "P3 拒绝了请求参数"),
    401: ("unauthenticated", "P3 凭据无效或未配置"),
    403: ("forbidden", "当前凭据没有此项 P3 权限"),
    404: ("not_found", "P3 中未找到该资源"),
    409: ("upstream_conflict", "P3 请求冲突，请核对当前版本与操作记录"),
    410: ("upstream_protocol_error", "P3 返回的失效错误码不符合接口约定"),
    422: ("invalid_argument", "P3 请求不符合接口约定"),
    429: ("upstream_rate_limited", "P3 请求过于频繁，请稍后重试"),
    503: ("dependency_unavailable", "P3 依赖暂不可用"),
    504: ("upstream_timeout", "等待 P3 响应超时"),
}
_BUSINESS_ERRORS = {
    (409, "VERSION_CONFLICT"): ("version_conflict", "记忆版本已变化，请重新读取后确认"),
    (409, "IDEMPOTENCY_CONFLICT"): ("idempotency_conflict", "同一操作 ID 的内容不一致"),
    (400, "REQUEST_IN_PROGRESS"): ("request_in_progress", "P3 正在处理此操作，请先核对状态"),
    (400, "COMMIT_UNCONFIRMED"): ("commit_unconfirmed", "P3 写入结果尚未确认，请核对后再试"),
    (400, "BUDGET_TOO_SMALL"): ("budget_too_small", "当前 Token 预算不足以容纳结果"),
    (410, "RESULT_INVALIDATED"): ("recall_invalidated", "该结果已失效，请重新召回"),
    (410, "MEMORY_GONE"): ("memory_gone", "该记忆已不可用"),
}


def upstream_error(
    status: int,
    business_code: str | None,
    *,
    operation_id: str | None = None,
    write: bool = False,
) -> ValidationError:
    code, message = _BUSINESS_ERRORS.get(
        (status, business_code or ""),
        _HTTP_ERRORS.get(status, ("upstream_error", "P3 请求失败，请核对服务状态")),
    )
    unconfirmed = write and (status >= 500 or code == "commit_unconfirmed")
    return ValidationError(
        status,
        code,
        message,
        operation_id=operation_id,
        write_outcome="unconfirmed" if unconfirmed else "not_applicable",
    )
