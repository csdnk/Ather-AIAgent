"""Read a required domain record; writes validate its concrete contract first."""

from typing import Any

from aether_agent_memory.runtime.contracts.models import ErrorCode, RecordRef
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.storage import SQLiteTransaction


def required_record(tx: SQLiteTransaction, ref: RecordRef) -> dict[str, Any]:
    value = tx.get(ref)
    if value is None:
        raise FoundationError(ErrorCode.NOT_FOUND, "required record is missing")
    return value
