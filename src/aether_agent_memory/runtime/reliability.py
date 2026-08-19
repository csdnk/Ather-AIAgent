from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    owner: str
    max_attempts: int
    notes: str = ""


@dataclass(frozen=True, slots=True)
class DeadlinePolicy:
    enforce_at_runtime: bool = True
    default_timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class CircuitState:
    name: str
    enabled: bool = False
    state: str = "not_implemented"


@dataclass(frozen=True, slots=True)
class BulkheadPolicy:
    name: str
    enabled: bool = False
    limit: int | None = None
