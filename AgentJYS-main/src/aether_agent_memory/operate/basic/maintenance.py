"""Bounded cache integrity sampling, repair and independent readback through RF."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from aether_agent_memory.remember.basic.pipeline import RememberPipeline
from aether_agent_memory.remember.contracts.models import MemoryRef, MemorySnapshot
from aether_agent_memory.runtime.contracts.foundation import (
    DispositionRule,
    IncidentRecord,
    OperationDefinition,
    SignalDefinition,
    SignalObservation,
)
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    Permission,
    RecordRef,
    RecoveryDecision,
    RunResult,
    TaskRecord,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import fingerprint, later

if TYPE_CHECKING:
    from aether_agent_memory.runtime.flows.host import ThreeFlows
    from aether_agent_memory.runtime.storage.ports import MetadataTransaction


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

    def eligible(self, tx: MetadataTransaction, ctx: TrustedContext, memory: MemoryRef) -> bool:
        return (
            self.remember.final_guard(tx, ctx, (memory,), "actuate").items[0].decision == "allowed"
        )

    def inspect(self, memory: MemoryRef, digest: str) -> bool:
        return bool(self.executor.inspect(memory, digest))

    async def sample(self, ctx: TrustedContext) -> list[tuple[RecordRef, SignalObservation]]:
        result, cursor_key, cursor = await self.sample_batch(ctx)
        with self.rf.uow.transaction() as tx:
            self.rf.identity.revalidate(tx, ctx)
            tx.write("cache_sample_cursors", cursor_key, cursor)
        return result

    async def sample_batch(
        self, ctx: TrustedContext
    ) -> tuple[list[tuple[RecordRef, SignalObservation]], str, str]:
        """Read a bounded batch; its caller advances the cursor with observed facts."""
        self.rf.dispositions.configure_rule(ctx, self.rule)
        cursor_key = fingerprint(ctx.principal.model_dump(mode="json"))
        with self.rf.uow.transaction() as tx:
            cursor = tx.read("cache_sample_cursors", cursor_key) or ""
        rows = await asyncio.to_thread(self.executor.copies, cursor, self.batch_size)
        if not rows:
            rows = await asyncio.to_thread(self.executor.copies, "", self.batch_size)
        result = []
        for copy in rows:
            key, memory, digest = copy.key, copy.memory, copy.content_hash
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
                subject_key = fingerprint(subject.model_dump(mode="json"))
                label_key = fingerprint(
                    [self.signal.signal_id, subject.scope.model_dump(mode="json"), {}]
                )
                prior = tx.read("signal_samples", fingerprint([subject_key, label_key]))
                # Only committed observations throttle sampling, not attempted batches.
                if prior and self.rf.tasks.clock() < later(
                    prior["observation"]["observed_at"], self.signal.sample_interval_ms / 1000
                ):
                    continue
            readable = await asyncio.to_thread(self.inspect, memory, digest)
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
        return result, cursor_key, rows[-1].key if rows else ""

    def target(
        self, tx: MetadataTransaction, ctx: TrustedContext, task: TaskRecord
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
        self, tx: MetadataTransaction, ctx: TrustedContext, task: TaskRecord, digest: str
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
        item = await self.prepare_repair(ctx, task)
        if isinstance(item, RunResult):
            return item
        await asyncio.to_thread(self.repair_prepared, ctx, task, item)
        with self.rf.uow.transaction() as tx:
            return self.finish(tx, ctx, task, item.content_hash)

    def repair_prepared(self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot) -> None:
        with self.rf.uow.transaction() as tx:
            memory, digest = self.target(tx, ctx, task)
            if (
                item.ref != memory
                or item.content_hash != digest
                or not self.eligible(tx, ctx, memory)
            ):
                raise ValueError("cache target changed before repair")
        self.executor.repair(item, task.task_id, ctx)
        with self.rf.uow.transaction() as tx:
            self.target(tx, ctx, task)
            if not self.eligible(tx, ctx, item.ref):
                raise ValueError("cache target changed after repair")
            self.rf.tasks.guard(tx, task)

    async def prepare_repair(
        self, ctx: TrustedContext, task: TaskRecord
    ) -> MemorySnapshot | RunResult:
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
            return item

    async def recover(self, ctx: TrustedContext, task: TaskRecord) -> RecoveryDecision:
        with self.rf.uow.transaction() as tx:
            memory, digest = self.target(tx, ctx, task)
            eligible = self.eligible(tx, ctx, memory)
        original = await asyncio.to_thread(self.executor.repair_record, task.task_id)
        readable = await asyncio.to_thread(self.inspect, memory, digest) if eligible else False
        if original == (memory.model_dump_json(), digest) and readable:
            with self.rf.uow.transaction() as tx:
                self.target(tx, ctx, task)
                if not self.eligible(tx, ctx, memory):
                    tx.abort(ErrorCode.RESULT_INVALIDATED, "cache target changed after readback")
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
            eligible = self.eligible(tx, ctx, memory)
        if not eligible or not await asyncio.to_thread(self.inspect, memory, digest):
            return ()
        with self.rf.uow.transaction() as tx:
            self.rf.identity.authorize(tx, ctx, Permission.RECOVER, incident.subject)
            if not self.eligible(tx, ctx, memory):
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
