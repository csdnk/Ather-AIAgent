"""Metadata-only guards. Missing replicas must not turn authorization into 'missing'."""

from typing import Any

from aether_agent_memory.remember.contracts.models import (
    EligibilityBatch,
    EligibilityResult,
    MemoryRef,
)
from aether_agent_memory.runtime.contracts.models import Permission, RecordRef, TrustedContext
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .service import Remember, memory_ref


def qualify(
    owner: Remember,
    tx: MetadataTransaction,
    ctx: TrustedContext,
    refs: tuple[MemoryRef, ...],
    purpose: str,
) -> EligibilityBatch:
    owner.identity.revalidate(tx, ctx)
    results = []
    for ref in refs:
        permission = Permission.HISTORY if purpose == "history" else Permission.READ
        allowed = owner.identity.permits(tx, ctx, permission, memory_ref(ref))
        pointer = tx.read("remember_current", ref.memory_id)
        current: dict[str, Any] | None = (
            tx.get(RecordRef.model_validate(pointer)) if pointer else None
        )
        item: dict[str, Any] | None = tx.get(memory_ref(ref, versioned=True))
        reason = "allowed"
        if not allowed:
            reason = "unauthorized"
        elif current is None or item is None:
            reason = "missing"
        elif current["status"] == "deleted" and purpose != "cleanup":
            reason = "deleted"
        elif purpose not in {"history", "cleanup"} and (
            current["ref"] != ref.model_dump(mode="json") or item["status"] != "active"
        ):
            reason = "old_version_or_inactive"
        elif purpose not in {"history", "cleanup"} and (
            item.get("expires_at") and item["expires_at"] <= owner.identity.clock()
        ):
            reason = "expired"
        elif purpose != "cleanup":
            for source in item["sources"]:
                row = tx.read("remember_sources", source["source_id"])
                if not row or not row.get("valid") or row["ref"] != source:
                    reason = "source_deleted"
                    break
            dependency = tx.read("remember_relations", ref.memory_id) or {}
            if dependency.get("evidence_state") in {"needs_revalidation", "unsupported"}:
                reason = "evidence_" + dependency["evidence_state"]
            for parent in dependency.get("derived_from", []):
                p = tx.read("remember_current", parent["memory_id"])
                raw: dict[str, Any] | None = tx.get(RecordRef.model_validate(p)) if p else None
                if (
                    raw is None
                    or raw["ref"] != parent
                    or raw["status"] in {"deleted", "superseded", "expired"}
                ):
                    reason = "derived_evidence_changed"
        results.append(
            EligibilityResult(
                ref=ref,
                decision="allowed" if reason == "allowed" else "excluded",
                reason=reason,
                checked_revision=current["object_revision"] if current else None,
            )
        )
    return EligibilityBatch(items=tuple(results), authorization_epoch=ctx.principal.auth_epoch)
