"""Conservative identity rules, independent of models and vector availability."""

import unicodedata
from hashlib import sha256

from aether_agent_memory.remember.contracts.models import RememberRequest
from aether_agent_memory.runtime.contracts.models import Scope
from aether_agent_memory.runtime.foundation.common import fingerprint


def canonical_text(text: str) -> str:
    """Only normalize Unicode composition, line endings and outer whitespace.

    Preserve internal spacing, case, punctuation, numbers and negation. In
    particular, fuzzy similarity is never sufficient evidence of equivalence.
    Original text and evidence offsets remain untouched in storage.
    """
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def source_identity(scope: Scope, request: RememberRequest) -> str:
    source = request.source.model_dump(mode="json")
    # Document versions are stable; messages/tool results are occurrences. A
    # sender must reuse the original occurrence timestamp when redelivering.
    if request.source.kind == "document":
        source.pop("occurred_at")
    return fingerprint(["source_identity_v2", scope.model_dump(mode="json"), source])


def source_signature(request: RememberRequest, text: str) -> str:
    return fingerprint(
        [sha256(text.encode("utf-8")).hexdigest(), request.trigger, request.importance_category]
        + ([request.task_context] if request.task_context else [])
        + ([request.content.model_dump(mode="json")] if request.content.kind == "document" else [])
    )
