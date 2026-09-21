from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RuntimeErrorBase(Exception):  # noqa: N818 - required by the runtime contract.
    code: str
    message: str
    retryable: bool = False
    component: str = "P3"
    trace_id: str | None = None

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "component": self.component,
            "trace_id": self.trace_id,
        }


class ValidationError(RuntimeErrorBase):
    def __init__(self, message: str, *, component: str = "P3", trace_id: str | None = None) -> None:
        super().__init__("VALIDATION_ERROR", message, False, component, trace_id)


class ScopeError(RuntimeErrorBase):
    def __init__(self, message: str, *, component: str = "P3", trace_id: str | None = None) -> None:
        super().__init__("SCOPE_ERROR", message, False, component, trace_id)


class ConflictError(RuntimeErrorBase):
    def __init__(self, message: str, *, component: str = "P3", trace_id: str | None = None) -> None:
        super().__init__("CONFLICT", message, False, component, trace_id)


class BusyError(RuntimeErrorBase):
    def __init__(self, message: str, *, component: str = "P3", trace_id: str | None = None) -> None:
        super().__init__("BUSY", message, True, component, trace_id)


class TimeoutError(RuntimeErrorBase):
    def __init__(self, message: str, *, component: str = "P3", trace_id: str | None = None) -> None:
        super().__init__("TIMEOUT", message, True, component, trace_id)


class DependencyUnavailableError(RuntimeErrorBase):
    def __init__(self, message: str, *, component: str, trace_id: str | None = None) -> None:
        super().__init__("DEPENDENCY_UNAVAILABLE", message, True, component, trace_id)


class ProjectionPendingError(RuntimeErrorBase):
    def __init__(self, message: str, *, component: str = "P3", trace_id: str | None = None) -> None:
        super().__init__("PROJECTION_PENDING", message, True, component, trace_id)


class DegradedError(RuntimeErrorBase):
    def __init__(self, message: str, *, component: str = "P3", trace_id: str | None = None) -> None:
        super().__init__("DEGRADED", message, True, component, trace_id)


class InternalRuntimeError(RuntimeErrorBase):
    def __init__(self, message: str, *, component: str = "P3", trace_id: str | None = None) -> None:
        super().__init__("INTERNAL_RUNTIME_ERROR", message, False, component, trace_id)
