"""Typed monitoring boundary. Reads cached evidence; probes never run maintenance."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aether_agent_memory.runtime.contracts.foundation import (
    HealthObservation,
    NodeLogRecord,
    RuntimeHealthSnapshot,
)
from aether_agent_memory.runtime.contracts.models import (
    CapabilityHealth,
    ErrorCode,
    HealthReport,
    Permission,
    TrustedContext,
)

from .common import fingerprint, later, now
from .identity import Identity
from .storage import SQLiteUnitOfWork
from .telemetry import Telemetry, current_node


class Monitoring:
    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        telemetry: Telemetry,
        *,
        clock: Callable[[], str] = now,
    ) -> None:
        self.uow, self.identity, self.telemetry, self.clock = uow, identity, telemetry, clock
        self.config_version = "foundation_2"
        self.required: tuple[str, ...] = (
            "save",
            "working_read",
            "context_budget",
            "long_term",
            "scheduling",
        )

    def authorize(self, ctx: TrustedContext) -> None:
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            if Permission.DIAGNOSE not in ctx.principal.permissions:
                tx.abort(ErrorCode.FORBIDDEN, "diagnose permission required")

    @staticmethod
    def key(ctx: TrustedContext) -> str:
        return fingerprint(ctx.principal.model_dump(mode="json"))

    def publish(
        self, ctx: TrustedContext, report: dict[str, Any], *, ttl_seconds: float = 10
    ) -> RuntimeHealthSnapshot:
        """Trusted probe adapter only; an HTTP caller cannot supply a probe report."""
        self.authorize(ctx)
        if not 0 < ttl_seconds <= 60:
            raise ValueError("health TTL must be in (0,60]")
        checked = report["checked_at"]
        if checked > self.clock():
            raise ValueError("future health evidence")
        observations = []
        for name, value in report["dependencies"].items():
            observations.append(
                HealthObservation(
                    name=name,
                    category="dependency",
                    state=value["state"],
                    checked_at=value["checked_at"],
                    fresh_until=later(value["checked_at"], ttl_seconds),
                    elapsed_ms=value["elapsed_ms"],
                    reason_code="probe_" + value["state"],
                )
            )
        # Capabilities must expire with their oldest supporting dependency, not
        # receive a new lifetime merely because report assembly finished later.
        earliest = min((o.checked_at for o in observations), default=checked)
        for name, state in report["capabilities"].items():
            observations.append(
                HealthObservation(
                    name=name,
                    category="capability",
                    state=state,
                    checked_at=earliest,
                    fresh_until=later(earliest, ttl_seconds),
                    elapsed_ms=0,
                    reason_code="capability_" + state,
                )
            )
        logs = report["dependencies"].get("logs", {})
        observations.append(
            HealthObservation(
                name="logging",
                category="capability",
                state=logs.get("state", "unknown"),
                checked_at=earliest,
                fresh_until=later(earliest, ttl_seconds),
                elapsed_ms=0,
                reason_code="log_probe",
            )
        )
        required = (*self.required, "logging")
        capabilities = {o.name: o for o in observations if o.category == "capability"}
        ready = all(
            n in capabilities
            and capabilities[n].state == "available"
            and capabilities[n].fresh_until > checked
            for n in required
        )
        snapshot = RuntimeHealthSnapshot(
            checked_at=checked,
            config_version=report.get("config_version", self.config_version),
            liveness="alive",
            readiness="ready" if ready else "not_ready",
            required_capabilities=required,
            observations=tuple(observations),
        )
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            tx.write("health_snapshots", self.key(ctx), snapshot.model_dump(mode="json"))
        return snapshot

    def health(self, ctx: TrustedContext) -> RuntimeHealthSnapshot:
        self.authorize(ctx)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            raw = tx.read("health_snapshots", self.key(ctx))
            configuration = tx.read("runtime_configuration", "active")
            version = configuration["version"] if configuration else self.config_version
        checked = self.clock()
        if raw is None:
            return RuntimeHealthSnapshot(
                checked_at=checked,
                config_version=version,
                liveness="alive",
                readiness="unknown",
                required_capabilities=(*self.required, "logging"),
                observations=(),
            )
        prior = RuntimeHealthSnapshot.model_validate(raw)
        observations = tuple(
            o
            if o.fresh_until > checked and prior.config_version == version
            else o.model_copy(
                update={
                    "state": "unknown",
                    "reason_code": "stale_evidence"
                    if prior.config_version == version
                    else "configuration_changed",
                }
            )
            for o in prior.observations
        )
        ready = all(
            any(
                o.category == "capability"
                and o.name == name
                and o.state == "available"
                and o.fresh_until > checked
                for o in observations
            )
            for name in prior.required_capabilities
        )
        return RuntimeHealthSnapshot(
            checked_at=checked,
            config_version=version,
            liveness="alive",
            readiness="ready" if ready and prior.config_version == version else "not_ready",
            required_capabilities=prior.required_capabilities,
            observations=observations,
        )

    def legacy_health(self, ctx: TrustedContext) -> HealthReport:
        snapshot = self.health(ctx)
        by_name = {o.name: o for o in snapshot.observations if o.category == "capability"}
        rows = []
        for name in ("save", "working_read", "long_term", "context", "scheduling"):
            observation = by_name.get("context_budget" if name == "context" else name)
            state = observation.state if observation else "unknown"
            rows.append(
                CapabilityHealth(
                    capability=name,
                    state="unavailable" if state == "disabled" else state,
                    checked_at=snapshot.checked_at,
                    reason=observation.reason_code if observation else "probe_not_collected",
                )
            )
        return HealthReport(capabilities=tuple(rows), config_version=snapshot.config_version)

    def emit(self, record: NodeLogRecord) -> None:
        """Only a current trusted span supplies the principal/scope absent in the DTO."""
        record = NodeLogRecord.model_validate_json(record.model_dump_json())
        node = current_node.get()
        if node is None or (record.trace_id, record.request_id, record.operation_id) != (
            node.ctx.trace_id,
            node.ctx.request_id,
            node.ctx.operation_id,
        ):
            raise ValueError("typed logs require their matching trusted span")
        self.telemetry.emit_record(node.ctx, record)

    def logs(
        self, ctx: TrustedContext, trace_id: str, *, after: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        self.authorize(ctx)
        result = self.telemetry.page(ctx, trace_id, after=after, limit=limit)
        result["records"] = [
            {"sequence": row["sequence"], **row["contract"]}
            for row in result["records"]
            if "contract" in row
        ]
        self.authorize(ctx)
        return result
