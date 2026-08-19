from __future__ import annotations

from uuid import uuid4


def new_id() -> str:
    """Return a compact runtime id for requests, traces, tasks and actions."""
    return uuid4().hex
