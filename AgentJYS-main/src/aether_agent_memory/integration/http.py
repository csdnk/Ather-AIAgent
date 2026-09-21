from __future__ import annotations

from http import HTTPStatus
from typing import Any

from aether_agent_memory.runtime.errors import RuntimeErrorBase


def runtime_error_to_http(exc: RuntimeErrorBase) -> tuple[HTTPStatus, dict[str, Any]]:
    if exc.code in {"VALIDATION_ERROR", "SCOPE_ERROR"}:
        status = HTTPStatus.UNPROCESSABLE_ENTITY
    elif exc.code == "CONFLICT":
        status = HTTPStatus.CONFLICT
    elif exc.code in {"BUSY", "TIMEOUT"}:
        status = HTTPStatus.SERVICE_UNAVAILABLE
    elif exc.code == "DEPENDENCY_UNAVAILABLE":
        status = HTTPStatus.BAD_GATEWAY
    else:
        status = HTTPStatus.INTERNAL_SERVER_ERROR
    return status, {"error": f"{type(exc).__name__}: {exc.message}", "runtime_error": exc.to_dict()}
