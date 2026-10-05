"""Source retraction is different from erasure and from archiving Working."""

from aether_agent_memory.remember.contracts.models import (
    DeleteReceipt,
    DeleteRequest,
    MemoryRef,
    MemorySnapshot,
)
from aether_agent_memory.runtime.contracts.http_evidence import HttpRequestEvidence
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Flow,
    Permission,
    RecordRef,
    RunResult,
    Scope,
    TaskRecord,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint

from .dedup import canonical_text
from .records import required_record
from .service import Remember


class Revalidation(Remember):
    @staticmethod
    def refkey(ref: MemoryRef) -> str:
        return fingerprint(ref.model_dump(mode="json"))

    def revoke_source(
        self,
        ctx: TrustedContext,
        source_id: str,
        request: DeleteRequest,
        *,
        http_request: HttpRequestEvidence | None = None,
    ) -> DeleteReceipt:
        with self.uow.transaction() as tx:
            row = tx.read("remember_sources", source_id)
            if row is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "source not found")
            mutation = self.mutations.begin(
                tx,
                ctx,
                "source.revoke",
                (
                    RecordRef(
                        owner=Flow.REMEMBER,
                        object_type="source",
                        object_id=source_id,
                        scope=Scope.model_validate(row["scope"]),
                    ),
                ),
                request.model_dump(mode="json"),
                http_request=http_request,
            )
            if mutation.previous is not None:
                return DeleteReceipt.model_validate(mutation.previous)
            self.identity.authorize(
                tx,
                ctx,
                Permission.DELETE,
                RecordRef(
                    owner=Flow.REMEMBER,
                    object_type="source",
                    object_id=source_id,
                    scope=Scope.model_validate(row["scope"]),
                ),
            )
            key, prior = self.replay(tx, ctx, "revoke_source_" + source_id, request)
            if prior:
                return DeleteReceipt.model_validate(prior["result"])
            if row["revision"] != request.expected_revision:
                tx.abort(ErrorCode.VERSION_CONFLICT, "source revision changed")
            tx.write(
                "remember_sources",
                source_id,
                {
                    **row,
                    "valid": False,
                    "revision": row["revision"] + 1,
                    "revocation_reason": request.reason,
                },
            )
            task_ids = []
            for memory_id, pointer in tx.rows("remember_current"):
                raw = required_record(tx, RecordRef.model_validate(pointer))
                if raw["status"] == "deleted" or not any(
                    s["source_id"] == source_id for s in raw["sources"]
                ):
                    continue
                relation = tx.read("remember_relations", memory_id) or {}
                tx.write(
                    "remember_relations",
                    memory_id,
                    {
                        **relation,
                        "evidence_state": "needs_revalidation",
                    },
                )
                ref = MemoryRef.model_validate(raw["ref"])
                artifact = tx.read("remember_artifacts", self.refkey(ref))
                if artifact:
                    tx.write(
                        "remember_artifacts",
                        self.refkey(ref),
                        {
                            **artifact,
                            "published": False,
                            "reason": "source_revoked",
                        },
                    )
                item = self.change(tx, self.current(tx, memory_id))
                self.emit(tx, ctx, item, "processing")
                task_ids.append(self.enqueue(tx, ctx, item, "remember.revalidate"))
            receipt = DeleteReceipt(
                operation_id=ctx.operation_id,
                blocked=True,
                cleanup_state="pending",
                task_ids=tuple(task_ids),
                remaining_targets=("evidence_revalidation",),
            )
            self.remember_result(tx, key, request, receipt)
            mutation.finish(receipt.model_dump(mode="json"), task_ids=receipt.task_ids)
            return receipt

    async def revalidate_sources(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot
    ) -> RunResult:
        return self.revalidate_source_metadata(ctx, task, item)

    def revalidate_source_metadata(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot
    ) -> RunResult:
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            relation = tx.read("remember_relations", item.ref.memory_id) or {}
            remaining = tuple(
                s
                for s in item.sources
                if ((tx.read("remember_sources", s.source_id) or {}).get("valid", False))
            )
            evidence = [
                e
                for e in relation.get("evidence", [])
                if e["source"]["source_id"] in {s.source_id for s in remaining}
            ]
            quotes = "\n".join(e["quote"] for e in evidence)
            supported = bool(remaining) and canonical_text(item.content) in canonical_text(quotes)
            support_reason = "independent_exact_support"
            if remaining and not supported:
                remaining_refs = {s.model_dump_json() for s in remaining}
                from aether_agent_memory.remember.contracts.models import SourceRef

                for _, form in tx.rows("remember_equivalent_forms"):
                    if (
                        form["memory_id"] != item.ref.memory_id
                        or form["canonical_version"] != item.ref.version
                        or not form["sources"]
                    ):
                        continue
                    form_refs = {
                        SourceRef.model_validate(s).model_dump_json() for s in form["sources"]
                    }
                    form_ids = {s["source_id"] for s in form["sources"]}
                    form_quotes = "\n".join(
                        e["quote"] for e in evidence if e["source"]["source_id"] in form_ids
                    )
                    # Accepted equivalence certificates bind this exact canonical
                    # version to still-valid, independently supported source forms.
                    if form_refs <= remaining_refs and canonical_text(
                        form["text"]
                    ) in canonical_text(form_quotes):
                        supported, support_reason = True, "verified_equivalent_support"
                        break
            tx.write(
                "remember_relations",
                item.ref.memory_id,
                {
                    **relation,
                    "evidence_state": "supported" if supported else "unsupported",
                    "revalidation_reason": support_reason
                    if supported
                    else "insufficient_remaining_support",
                    "evidence": evidence if supported else relation.get("evidence", []),
                },
            )
            if supported:
                updated = self.change(tx, self.current(tx, item.ref.memory_id), sources=remaining)
                self.emit(tx, ctx, updated, "processing")
            else:
                self.enqueue(tx, ctx, item, "remember.cleanup")
            return self.finish(
                tx,
                ctx,
                task,
                {
                    "evidence_state": "supported" if supported else "unsupported",
                    "remaining_sources": [s.source_id for s in remaining],
                    "requires_new_evidence": not supported,
                },
            )
