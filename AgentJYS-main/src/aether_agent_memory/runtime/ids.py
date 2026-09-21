from __future__ import annotations

import re
from uuid import uuid4

_COMPACT_ID = re.compile(r"[0-9a-f]{32}\Z")


def new_id() -> str:
    """Return a compact runtime id for requests, traces, tasks and actions."""
    return uuid4().hex


def is_compact_id(value: str) -> bool:
    """Return whether ``value`` is a canonical 128-bit capability identifier."""
    return _COMPACT_ID.fullmatch(value) is not None
