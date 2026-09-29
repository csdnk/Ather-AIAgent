"""Bounded cache integrity sampling, repair and independent readback through RF."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from aether_agent_memory.remember.basic.pipeline import RememberPipeline
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.foundation import (
    DispositionRule,
    IncidentRecord,
    OperationDefinition,
    SignalDefinition,
    SignalObservation,
)
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    Permission,
    RecordRef,
    RecoveryDecision,
    RunResult,
    TaskRecord,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import fingerprint
from aether_agent_memory.runtime.foundation.requests import text_hash

if TYPE_CHECKING:
    from aether_agent_memory.runtime.flows.host import ThreeFlows
    from aether_agent_memory.runtime.foundation.storage import SQLiteTransaction


class CacheMaintenance:
    def __init__(self, host: ThreeFlows, *, interval: float = 5, batch_size: int = 16) -> None:
        self.host, self.rf, self.executor = host, host.foundation, host.executor
        self.remember = cast(RememberPipeline, host.remember)
        self.batch_size = batch_size
        self.signal = SignalDefinition(
            signal_id="cache_integrity_v1",
            owner="operate",
            source="registered_cache_copies",
            unit="state",
            sample_interval_ms=int(interval * 1000),
            stale_after_ms=int(max(interval * 3, 10) * 1000),
            label_names=(),
            max_label_sets=1,
        )
        self.rule = DispositionRule(
            rule_id="repair_cache_v1",
            revision=1,
            signal_id=self.signal.signal_id,
            comparator="eq",
            threshold="corrupt",
            consecutive_samples=1,
            operation_kind="operate_repair_cache",
            max_attempts=3,
            deadline_ms=60000,
            cooldown_ms=30000,
            verification_operation="cache_readback_v1",
        )
        engine = self.rf.dispositions
        engine.register_signal(self.signal, self.sample)
        engine.register_operation(
            OperationDefinition(
                operation_kind=self.rule.operation_kind,
                owner="operate",
                execution_class="maintenance",
                input_model="runtime.IncidentRecord",
                output_model="runtime.RunResult",
                permission=Permission.RECOVER,
                side_effect="external",
                idempotency_required=True,
                success_evidence=("body_hash", "readback"),
                recovery="query_only",
                signal_ids=(self.signal.signal_id,),
                validation_scenarios=("corrupt", "missing", "revoked", "restart"),
            ),
            self,
        )
        engine.register_verifier(self.rule.verification_operation, self.verify)

    def eligible(self, tx: SQLiteTransaction, ctx: TrustedContext, memory: MemoryRef) -> bool:
        return (
            self.remember.final_guard(tx, ctx, (memory,), "actuate").items[0].decision == "allowed"
        )

    def inspect(self, memory: MemoryRef, digest: str) -> bool:
        from aether_agent_memory.operate.contracts.models import Tier

        with self.executor.db() as db:
            row = db.execute(
                "SELECT tier,content_hash FROM copies WHERE key=?", (self.executor.key(memory),)
            ).fetchone()
        if not row or row[1] != digest:
            return False
        try:
            data = self.executor.path(memory, Tier(row[0])).read_bytes()
            return text_hash(data.decode("utf-8")) == digest
        except (OSError, UnicodeError):
            return False

    async def sample(self, ctx: TrustedContext) -> list[tuple[RecordRef, SignalObservation]]:
        self.rf.dispositions.configure_rule(ctx, self.rule)
        cursor_key = fingerprint(ctx.principal.model_dump(mode="json"))
        with self.rf.uow.transaction() as tx:
            cursor = tx.read("cache_sample_cursors", cursor_key) or ""
        with self.executor.db() as db:
            rows = db.execute(
                "SELECT key,memory,content_hash FROM copies WHERE key>? ORDER BY key LIMIT ?",
                (cursor, self.batch_size),
            ).fetchall()
            if not rows:
                rows = db.execute(
                    "SELECT key,memory,content_hash FROM copies ORDER BY key LIMIT ?",
                    (self.batch_size,),
                ).fetchall()
        result = []
        for key, raw, digest in rows:
            memory = MemoryRef.model_validate_json(raw)
            subject = RecordRef(
                owner="operate",
                object_type="cache_copy",
                object_id=key,
                scope=memory.scope,
                version=memory.version,
            )
            with self.rf.uow.transaction() as tx:
                if not self.rf.identity.permits(
                    tx, ctx, Permission.RECOVER, subject
                ) or not self.eligible(tx, ctx, memory):
                    continue
            readable = self.inspect(memory, digest)
            evidence = RecordRef(
                owner="operate",
                object_type="cache_observation",
                object_id=fingerprint([key, digest, readable]),
                scope=memory.scope,
            )
            with self.rf.uow.transaction() as tx:
                self.rf.identity.authorize(tx, ctx, Permission.RECOVER, subject)
                if not self.eligible(tx, ctx, memory):
                    continue
                if tx.get(evidence) is None:
                    tx.put_if_revision(
                        evidence,
                        {
                            "memory": memory.model_dump(mode="json"),
                            "content_hash": digest,
                            "readable": readable,
                        },
                        None,
                    )
            result.append(
                (
                    subject,
                    SignalObservation(
                        signal=self.signal,
                        observed_at=self.rf.tasks.clock(),
                        state="known",
                        value="healthy" if readable else "corrupt",
                        labels={},
                        evidence_refs=(evidence,),
                    ),
                )
            )
        with self.rf.uow.transaction() as tx:
            self.rf.identity.revalidate(tx, ctx)
            tx.write("cache_sample_cursors", cursor_key, rows[-1][0] if rows else "")
        return result

    def target(
        self, tx: SQLiteTransaction, ctx: TrustedContext, task: TaskRecord
    ) -> tuple[MemoryRef, str]:
        self.rf.tasks.guard(tx, task)
        self.rf.identity.authorize(tx, ctx, Permission.RECOVER, task.subject)
        incident = IncidentRecord.model_validate(tx.get(task.input_ref))
        evidence = tx.get(incident.evidence_refs[0])
        if evidence is None:
            raise ValueError("missing cache observation")
        memory = MemoryRef.model_validate(evidence["memory"])
        if (
            memory.scope != task.subject.scope
            or self.executor.key(memory) != task.subject.object_id
        ):
            raise ValueError("cache observation does not match task")
        return memory, str(evidence["content_hash"])

    def finish(
        self, tx: SQLiteTransaction, ctx: TrustedContext, task: TaskRecord, digest: str
    ) -> RunResult:
        ref = RecordRef(
            owner="operate",
            object_type="cache_repair_result",
            object_id=task.task_id,
            scope=task.subject.scope,
        )
        if tx.get(ref) is None:
            tx.put_if_revision(ref, {"content_hash": digest, "operation_id": task.task_id}, None)
        self.rf.tasks.complete(tx, ctx, task, ref)
        return RunResult(
            outcome="committed",
            effect_status=EffectStatus.CONFIRMED,
            result_ref=ref,
            reason="cache bytes restored and verified",
        )

    async def run(self, ctx: TrustedContext, task: TaskRecord) -> RunResult:
        with self.rf.uow.transaction() as tx:
            memory, digest = self.target(tx, ctx, task)
            if not self.eligible(tx, ctx, memory):
                return RunResult(
                    outcome="obsolete",
                    effect_status=EffectStatus.NO_EFFECT,
                    reason="cache target invalidated",
                )
        await self.remember.hydrate(ctx, (memory,), "actuate")
        with self.rf.uow.transaction() as tx:
            self.target(tx, ctx, task)
            if not self.eligible(tx, ctx, memory):
                return RunResult(
                    outcome="obsolete",
                    effect_status=EffectStatus.NO_EFFECT,
                    reason="cache target invalidated",
                )
            item = self.remember.current(tx, memory.memory_id)
            if item.ref != memory or item.content_hash != digest:
                raise ValueError("cache target version changed")
            self.executor.repair(item, task.task_id)
            return self.finish(tx, ctx, task, digest)

    async def recover(self, ctx: TrustedContext, task: TaskRecord) -> RecoveryDecision:
        with self.rf.uow.transaction() as tx:
            memory, digest = self.target(tx, ctx, task)
            with self.executor.db() as db:
                original = db.execute(
                    "SELECT memory,content_hash FROM repairs WHERE id=?", (task.task_id,)
                ).fetchone()
            if (
                original == (memory.model_dump_json(), digest)
                and self.eligible(tx, ctx, memory)
                and self.inspect(memory, digest)
            ):
                self.finish(tx, ctx, task, digest)
                return RecoveryDecision(
                    action="query_only",
                    effect_status="confirmed",
                    original_operation_id=task.task_id,
                    reason="original cache repair verified",
                    evidence=(task.input_ref,),
                )
        return RecoveryDecision(
            action="attention",
            effect_status="unknown",
            original_operation_id=task.task_id,
            reason="repair outcome requires inspection",
            evidence=(task.input_ref,),
        )

    async def verify(
        self, ctx: TrustedContext, incident: IncidentRecord, task: TaskRecord
    ) -> tuple[RecordRef, ...]:
        with self.rf.uow.transaction() as tx:
            self.rf.identity.authorize(tx, ctx, Permission.RECOVER, incident.subject)
            evidence = tx.get(incident.evidence_refs[0])
            if evidence is None:
                return ()
            memory = MemoryRef.model_validate(evidence["memory"])
            digest = str(evidence["content_hash"])
            if not self.eligible(tx, ctx, memory) or not self.inspect(memory, digest):
                return ()
            proof = RecordRef(
                owner="operate",
                object_type="cache_readback",
                object_id=task.task_id,
                scope=memory.scope,
            )
            if tx.get(proof) is None:
                tx.put_if_revision(
                    proof,
                    {
                        "memory": memory.model_dump(mode="json"),
                        "content_hash": digest,
                        "checked_at": self.rf.tasks.clock(),
                    },
                    None,
                )
            return (proof,)
