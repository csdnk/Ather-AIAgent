"""Clock, canonical encoding and explicit runtime errors."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import rfc8785

from aether_agent_memory.runtime.contracts.models import ErrorCode


class FoundationError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def later(timestamp: str, seconds: float) -> str:
    return (
        (datetime.fromisoformat(timestamp.replace("Z", "+00:00")) + timedelta(seconds=seconds))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def encode(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def fingerprint(value: Any) -> str:
    return sha256(rfc8785.dumps(value)).hexdigest()
