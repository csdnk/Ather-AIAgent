"""Persistent signal evaluation and maintenance through the shared Task engine.

Registered handlers receive an IncidentRecord as their durable input. Repair
completion and independent business verification remain separate transitions.
"""

from __future__ import annotations

import asyncio
import operator
from collections.abc import Awaitable, Callable

from aether_agent_memory.runtime.contracts.foundation import (
    DispositionRule,
    IncidentRecord,
    OperationDefinition,
    SignalDefinition,
    SignalObservation,
)
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    MaintenanceRecord,
    OperationRecord,
    Permission,
    RecordRef,
    RecoveryDecision,
    RunResult,
    TaskRecord,
    TaskSpec,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import TaskHandler

from .common import fingerprint, later
from .tasks import Tasks
from .telemetry import observed

Verifier = Callable[[TrustedContext, IncidentRecord, TaskRecord], Awaitable[tuple[RecordRef, ...]]]
Sampler = Callable[[TrustedContext], Awaitable[list[tuple[RecordRef, SignalObservation]]]]


class RegisteredHandler:
    """Apply operation recovery policy in addition to the handler's evidence."""

    def __init__(self, definition: OperationDefinition, handler: TaskHandler) -> None:
        self.definition, self.handler = definition, handler

    async def run(self, ctx: TrustedContext, task: TaskRecord) -> RunResult:
        return await self.handler.run(ctx, task)

    async def recover(self, ctx: TrustedContext, task: TaskRecord) -> RecoveryDecision:
        if self.definition.recovery == "manual":
            return RecoveryDecision(
                action="attention",
                effect_status="unknown",
                reason="operation requires manual recovery",
                evidence=(task.input_ref,),
            )
        decision = await self.handler.recover(ctx, task)
        if self.definition.recovery == "query_only" and decision.action.value == "resume":
            return RecoveryDecision(
                action="attention",
                effect_status=decision.effect_status,
                reason="registered operation forbids automatic resubmission",
                evidence=decision.evidence,
                original_operation_id=decision.original_operation_id,
            )
        return decision


@observed("runtime.dispositions")
class Dispositions:
    def __init__(self, tasks: Tasks) -> None:
        self.tasks, self.uow, self.identity = tasks, tasks.uow, tasks.identity
        self.signals: dict[str, SignalDefinition] = {}
        self.operations: dict[str, OperationDefinition] = {}
        self.verifiers: dict[str, Verifier] = {}
        self.samplers: dict[str, Sampler] = {}
        for category in ("maintenance", "io", "model"):
            self.tasks.class_limits.setdefault(category, 1)

    def register_signal(self, definition: SignalDefinition, sampler: Sampler | None = None) -> None:
        with self.uow.transaction() as tx:
            old = tx.read("signal_definitions", definition.signal_id)
            if old and old != definition.model_dump(mode="json"):
                raise ValueError("signal change requires a new identifier")
            tx.write("signal_definitions", definition.signal_id, definition.model_dump(mode="json"))
        self.signals[definition.signal_id] = definition
        if sampler:
            self.samplers[definition.signal_id] = sampler

    def register_operation(self, definition: OperationDefinition, handler: TaskHandler) -> None:
        if (
            definition.input_model != "runtime.IncidentRecord"
            or definition.output_model != "runtime.RunResult"
        ):
            raise ValueError(
                "maintenance adapter requires IncidentRecord input and RunResult output"
            )
        if definition.operation_kind in self.tasks.handlers:
            raise ValueError("duplicate operation handler")
        if any(s not in self.signals for s in definition.signal_ids):
            raise ValueError("operation references unregistered signal")
        with self.uow.transaction() as tx:
            old = tx.read("operation_definitions", definition.operation_kind)
            if old and old != definition.model_dump(mode="json"):
                raise ValueError("operation change requires a new identifier")
            tx.write(
                "operation_definitions",
                definition.operation_kind,
                definition.model_dump(mode="json"),
            )
        self.tasks.register(
            definition.operation_kind,
            "maintenance" if definition.execution_class == "inline" else definition.execution_class,
            RegisteredHandler(definition, handler),
            permission=definition.permission,
        )
        self.operations[definition.operation_kind] = definition

    def register_verifier(self, name: str, verify: Verifier) -> None:
        if name in self.verifiers:
            raise ValueError("duplicate verifier")
        self.verifiers[name] = verify

    def configure_rule(self, ctx: TrustedContext, rule: DispositionRule) -> None:
        if (
            rule.signal_id not in self.signals
            or rule.operation_kind not in self.operations
            or rule.verification_operation not in self.verifiers
        ):
            raise ValueError("rule has unresolved registry references")
        signal, operation = self.signals[rule.signal_id], self.operations[rule.operation_kind]
        if rule.signal_id not in operation.signal_ids:
            raise ValueError("rule signal is not registered on its operation")
        if (signal.unit == "state") != isinstance(rule.threshold, str):
            raise ValueError("rule threshold does not match signal unit")
        if rule.max_attempts > self.tasks.max_attempts:
            raise ValueError("rule exceeds runtime attempt budget")
        key = fingerprint([ctx.principal.home_scope.model_dump(mode="json"), rule.rule_id])
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            if Permission.CONFIGURE not in ctx.principal.permissions:
                tx.abort(ErrorCode.FORBIDDEN, "configure permission required")
            old = tx.read("disposition_rules", key)
            if old and old["rule"] == rule.model_dump(mode="json"):
                return
            if rule.revision != (old["rule"]["revision"] + 1 if old else 1):
                tx.abort(ErrorCode.VERSION_CONFLICT, "rule revision changed")
            tx.write(
                "disposition_rules",
                key,
                {
                    "scope": ctx.principal.home_scope.model_dump(mode="json"),
                    "rule": rule.model_dump(mode="json"),
                },
            )

    def observe(
        self, ctx: TrustedContext, subject: RecordRef, observation: SignalObservation
    ) -> tuple[IncidentRecord, ...]:
        observation = SignalObservation.model_validate_json(observation.model_dump_json())
        definition = self.signals.get(observation.signal.signal_id)
        if definition != observation.signal:
            raise ValueError("observation does not match registered signal")
        stamp = self.tasks.clock()
        if observation.observed_at > stamp:
            raise ValueError("future sample")
        if later(observation.observed_at, definition.stale_after_ms / 1000) <= stamp:
            observation = observation.model_copy(update={"state": "stale", "value": None})
        subject_key = fingerprint(subject.model_dump(mode="json"))
        scope = subject.scope.model_dump(mode="json")
        label_key = fingerprint([definition.signal_id, scope, observation.labels])
        sample_key = fingerprint([subject_key, label_key])
        incidents = []
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.DIAGNOSE, subject)
            for ref in observation.evidence_refs:
                self.identity.authorize(tx, ctx, Permission.READ, ref)
                if ref.scope != subject.scope or tx.get(ref) is None:
                    tx.abort(
                        ErrorCode.CONTRACT_VIOLATION, "signal evidence missing or out of scope"
                    )
            labels = [
                k
                for k, r in tx.rows("signal_labels")
                if r["signal_id"] == definition.signal_id and r["scope"] == scope
            ]
            if label_key not in labels and len(labels) >= definition.max_label_sets:
                tx.abort(ErrorCode.CAPACITY_EXCEEDED, "signal label cardinality exceeded")
            prior = tx.read("signal_samples", sample_key)
            if prior and observation.observed_at <= prior["observation"]["observed_at"]:
                if prior["observation"] == observation.model_dump(mode="json"):
                    return ()
                tx.abort(ErrorCode.VERSION_CONFLICT, "sample timestamp is not increasing")
            if prior and observation.observed_at < later(
                prior["observation"]["observed_at"], definition.sample_interval_ms / 1000
            ):
                tx.abort(ErrorCode.INVALID_ARGUMENT, "sample interval violated")
            tx.write(
                "signal_labels", label_key, {"signal_id": definition.signal_id, "scope": scope}
            )
            tx.write(
                "signal_samples",
                sample_key,
                {
                    "subject": subject.model_dump(mode="json"),
                    "observation": observation.model_dump(mode="json"),
                },
            )
            rules = [
                DispositionRule.model_validate(r["rule"])
                for _, r in tx.rows("disposition_rules")
                # Rules belong to the configuring identity's scope. A subject
                # may be in a narrower session/task scope; object authorization
                # above still gates every measurement and maintenance action.
                if r["scope"] == ctx.principal.home_scope.model_dump(mode="json")
                and r["rule"]["signal_id"] == definition.signal_id
            ]
            for rule in rules:
                compare = {
                    "gt": operator.gt,
                    "ge": operator.ge,
                    "lt": operator.lt,
                    "le": operator.le,
                    "eq": operator.eq,
                }[rule.comparator]
                matched = (
                    observation.state == "known"
                    and observation.value is not None
                    and compare(observation.value, rule.threshold)
                )
                key = fingerprint([sample_key, rule.rule_id, rule.revision])
                state = tx.read("rule_states", key) or {
                    "count": 0,
                    "sequence": 0,
                    "cooldown_until": stamp,
                }
                gap = (
                    prior is not None
                    and later(prior["observation"]["observed_at"], definition.stale_after_ms / 1000)
                    <= observation.observed_at
                )
                state["count"] = (0 if gap else state["count"]) + 1 if matched else 0
                active = tx.read("incidents", state.get("active", ""))
                if active and active["record"]["state"] != "resolved":
                    tx.write("rule_states", key, state)
                    continue
                if state["count"] >= rule.consecutive_samples and stamp >= state["cooldown_until"]:
                    # Policy revision, new labels or another rule cannot sidestep
                    # an unresolved operation on the same exact subject.
                    unresolved = any(
                        r["record"]["subject"] == subject.model_dump(mode="json")
                        and r["record"]["state"] != "resolved"
                        for _, r in tx.rows("incidents")
                    )
                    if unresolved:
                        tx.write("rule_states", key, state)
                        continue
                    op = self.operations[rule.operation_kind]
                    if subject.owner != op.owner:
                        tx.abort(
                            ErrorCode.CONTRACT_VIOLATION, "operation owner differs from subject"
                        )
                    self.identity.authorize(tx, ctx, op.permission, subject)
                    sequence = state["sequence"] + 1
                    incident_id = fingerprint([key, sequence])[:32]
                    task_id = "maint_" + incident_id
                    deadline = min(ctx.deadline_at, later(stamp, rule.deadline_ms / 1000))
                    incident = IncidentRecord(
                        incident_id=incident_id,
                        rule_id=rule.rule_id,
                        rule_revision=rule.revision,
                        subject=subject,
                        revision=1,
                        state="recovering",
                        operation_id=task_id,
                        evidence_refs=observation.evidence_refs,
                        verification="pending",
                        opened_at=stamp,
                        updated_at=stamp,
                    )
                    input_ref = RecordRef(
                        owner=subject.owner,
                        object_type="maintenance_input",
                        object_id=incident_id,
                        scope=subject.scope,
                    )
                    payload = incident.model_dump(mode="json")
                    tx.put_if_revision(input_ref, payload, None)
                    self.tasks.enqueue(
                        tx,
                        ctx,
                        TaskSpec(
                            task_id=task_id,
                            owner_flow=subject.owner,
                            kind=rule.operation_kind,
                            subject=subject,
                            input_ref=input_ref,
                            input_hash=fingerprint(payload),
                            idempotency_key=incident_id,
                            initiator_id=ctx.principal.principal_id,
                            initiator_auth_epoch=ctx.principal.auth_epoch,
                            deadline_at=deadline,
                            max_attempts=rule.max_attempts,
                        ),
                    )
                    operation = OperationRecord(
                        operation_id=task_id,
                        subject=subject,
                        phase="maintenance",
                        state="accepted",
                        task_ids=(task_id,),
                        reason="signal_rule_triggered",
                    )
                    tx.write(
                        "operations",
                        task_id,
                        {
                            "record": operation.model_dump(mode="json"),
                            "signature": fingerprint([ctx.principal.principal_id, incident_id]),
                        },
                    )
                    configuration = tx.read("runtime_configuration", "active")
                    audit = MaintenanceRecord(
                        record_id="audit_" + incident_id,
                        actor_id=ctx.principal.principal_id,
                        operation_id=task_id,
                        subject=subject,
                        reason="signal_rule_triggered",
                        occurred_at=stamp,
                        phase="accepted",
                        config_version=configuration["version"]
                        if configuration
                        else "foundation_2",
                    )
                    tx.write("maintenance", audit.record_id, audit.model_dump(mode="json"))
                    tx.write(
                        "incidents",
                        incident_id,
                        {
                            "record": incident.model_dump(mode="json"),
                            "task_id": task_id,
                            "rule": rule.model_dump(mode="json"),
                            "deadline_at": deadline,
                            "verify_after": stamp,
                            "rule_state_key": key,
                        },
                    )
                    state.update(
                        sequence=sequence,
                        active=incident_id,
                        count=0,
                        cooldown_until=later(stamp, rule.cooldown_ms / 1000),
                    )
                    incidents.append(incident)
                tx.write("rule_states", key, state)
            tx.before_commit.append(lambda: self.identity.revalidate(tx, ctx))
        return tuple(incidents)

    def incidents(self, ctx: TrustedContext) -> tuple[IncidentRecord, ...]:
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            return tuple(
                IncidentRecord.model_validate(r["record"])
                for _, r in tx.rows("incidents")
                if self.identity.permits(
                    tx, ctx, Permission.DIAGNOSE, RecordRef.model_validate(r["record"]["subject"])
                )
            )

    async def reconcile(self, ctx: TrustedContext, *, limit: int = 20) -> int:
        if not 1 <= limit <= 100:
            raise ValueError("invalid reconciliation limit")
        count = 0
        for incident in self.incidents(ctx):
            if count >= limit:
                break
            if incident.state in {"resolved", "attention_required"}:
                continue
            with self.uow.transaction() as tx:
                self.identity.authorize(tx, ctx, Permission.RECOVER, incident.subject)
                row = tx.read("incidents", incident.incident_id)
                _, task = self.tasks.load(tx, row["task_id"])
                stamp = self.tasks.clock()
                expired = stamp >= row["deadline_at"]
                failure = task.state.value in {"failed", "cancelled", "attention_required"}
                if not expired and not failure and task.state.value != "succeeded":
                    continue
                definition = self.operations.get(row["rule"]["operation_kind"])
                if (
                    definition is None
                    or task.state.value == "succeeded"
                    and (task.result_ref is None or definition.output_model != "runtime.RunResult")
                ):
                    failure = True
                if row["verify_after"] > stamp and not expired:
                    continue
                rule = DispositionRule.model_validate(row["rule"])
                updated = IncidentRecord.model_validate(
                    {
                        **row["record"],
                        "state": "attention_required" if expired or failure else "verifying",
                        "verification": "unknown"
                        if expired
                        else "failed"
                        if failure
                        else "pending",
                        "revision": row["record"]["revision"] + 1,
                        "updated_at": stamp,
                    }
                )
                tx.write(
                    "incidents",
                    incident.incident_id,
                    {
                        **row,
                        "record": updated.model_dump(mode="json"),
                        "verify_after": later(stamp, 1),
                    },
                )
            count += 1
            if updated.state != "verifying":
                continue
            verify = self.verifiers.get(rule.verification_operation)
            refs: tuple[RecordRef, ...] = ()
            if verify:
                try:
                    refs = await asyncio.wait_for(
                        verify(ctx, updated, task), timeout=min(2, rule.deadline_ms / 1000)
                    )
                except Exception:
                    refs = ()
            with self.uow.transaction() as tx:
                self.identity.authorize(tx, ctx, Permission.RECOVER, incident.subject)
                current = tx.read("incidents", incident.incident_id)
                if current["record"]["revision"] != updated.revision:
                    continue
                valid = bool(refs) and self.tasks.clock() < row["deadline_at"]
                for ref in refs:
                    if (
                        ref.scope != incident.subject.scope
                        or not self.identity.permits(tx, ctx, Permission.READ, ref)
                        or tx.get(ref) is None
                    ):
                        valid = False
                done = IncidentRecord.model_validate(
                    {
                        **current["record"],
                        "state": "resolved" if valid else "verifying",
                        "verification": "passed" if valid else "pending",
                        "verification_refs": refs if valid else (),
                        "revision": updated.revision + 1,
                        "updated_at": self.tasks.clock(),
                    }
                )
                tx.write(
                    "incidents",
                    incident.incident_id,
                    {**current, "record": done.model_dump(mode="json")},
                )
                if valid:
                    state = tx.read("rule_states", current["rule_state_key"])
                    state["cooldown_until"] = later(self.tasks.clock(), rule.cooldown_ms / 1000)
                    tx.write("rule_states", current["rule_state_key"], state)
        return count

    async def cycle(self, ctx: TrustedContext) -> dict[str, int]:
        sampled = 0
        for signal_id, sampler in self.samplers.items():
            key = fingerprint([ctx.principal.model_dump(mode="json"), signal_id])
            stamp = self.tasks.clock()
            with self.uow.transaction() as tx:
                self.identity.revalidate(tx, ctx)
                previous = tx.read("sampler_runs", key)
                if previous and previous["next_at"] > stamp:
                    continue
                tx.write(
                    "sampler_runs",
                    key,
                    {"next_at": later(stamp, self.signals[signal_id].sample_interval_ms / 1000)},
                )
            rows = await asyncio.wait_for(sampler(ctx), timeout=2)
            if len(rows) > 100:
                raise ValueError("sampler exceeds per-cycle bound")
            for subject, observation in rows:
                if observation.signal.signal_id != signal_id:
                    raise ValueError("sampler emitted another signal")
                self.observe(ctx, subject, observation)
                sampled += 1
        reconciled = await self.reconcile(ctx)
        return {"sampled": sampled, "reconciled": reconciled}
