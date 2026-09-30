"""Integrated RF services: persistence, fencing, tenant isolation and real restore."""

from hashlib import sha256

import pytest

from aether_agent_memory.runtime.contracts.foundation import (
    ConfigurationSnapshot,
    DispositionRule,
    IncidentRecord,
    NodeLogRecord,
    OperationDefinition,
    SignalDefinition,
    SignalObservation,
)
from aether_agent_memory.runtime.contracts.models import (
    Permission,
    Principal,
    RecordRef,
    RecoveryDecision,
    RunResult,
    Scope,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later, now
from aether_agent_memory.runtime.foundation.host import Foundation


class Clock:
    def __init__(self):
        self.value = now()

    def __call__(self):
        return self.value

    def advance(self, seconds=1):
        self.value = later(self.value, seconds)


def identities():
    return [
        (
            sha256(name.encode()).hexdigest(),
            Principal(
                principal_id=name,
                home_scope=Scope(
                    tenant_id=tenant, application_id="app", user_id=name, agent_id="agent"
                ),
                permissions=tuple(Permission),
                auth_epoch=1,
            ),
        )
        for name, tenant in (("admin", "one"), ("other", "two"))
    ]


@pytest.fixture
def app(tmp_path):
    app = Foundation(
        tmp_path / "rf.db", maintenance_principals=("admin",), engineering_profile=True
    )
    app.identity.provision(identities())
    clock = Clock()
    app.tasks.clock = app.identity.clock = app.monitoring.clock = clock
    app.test_clock = clock
    yield app
    app.close()


def ctx(app, name="admin"):
    return app.identity.context(name, timeout_seconds=300)


def ref(context, name="target", kind="test_target"):
    return RecordRef(
        owner="runtime", object_type=kind, object_id=name, scope=context.principal.home_scope
    )


def test_typed_logs_are_used_by_real_spans_and_scoped(app):
    context = ctx(app)
    with app.telemetry.span(context, "runtime.example"):
        pass
    logs = app.monitoring.logs(context, context.trace_id)
    assert [r["phase"] for r in logs["records"]] == ["started", "returned"]
    for row in logs["records"]:
        NodeLogRecord.model_validate({k: v for k, v in row.items() if k != "sequence"})
    assert not app.monitoring.logs(ctx(app, "other"), context.trace_id)["records"]
    with app.telemetry.span(context, "runtime.explicit") as node:
        record = NodeLogRecord(
            occurred_at=now(),
            service="aether-p3",
            instance=app.telemetry.instance_id,
            flow="runtime",
            request_id=context.request_id,
            operation_id=context.operation_id,
            trace_id=context.trace_id,
            span_id=node.span_id,
            node="explicit",
            phase="started",
            level="info",
        )
        app.monitoring.emit(record)
    with pytest.raises(ValueError, match="trusted span"):
        app.monitoring.emit(record)


def test_health_cache_expiry_authorization_and_legacy_projection(app):
    context = ctx(app)
    stamp = app.test_clock()
    assert app.monitoring.health(context).readiness == "unknown"
    report = {
        "checked_at": stamp,
        "dependencies": {"logs": {"state": "available", "checked_at": stamp, "elapsed_ms": 1}},
        "capabilities": dict.fromkeys(app.monitoring.required, "available"),
    }
    assert app.monitoring.publish(context, report, ttl_seconds=2).readiness == "ready"
    assert app.diagnostics.health(context).capabilities[0].state == "available"
    assert app.monitoring.health(ctx(app, "other")).readiness == "unknown"
    app.test_clock.advance(3)
    assert app.monitoring.health(context).readiness == "not_ready"
    assert all(o.state == "unknown" for o in app.monitoring.health(context).observations)
    app.identity.provision(identities()[1:])
    with pytest.raises(FoundationError):
        app.monitoring.health(context)


class Repair:
    def __init__(self, app):
        self.app = app
        self.calls = 0

    async def run(self, context, task):
        self.calls += 1
        with self.app.uow.transaction() as tx:
            self.app.tasks.guard(tx, task)
            incident = IncidentRecord.model_validate(tx.get(task.input_ref))
            tx.put_if_revision(incident.subject, {"healthy": True}, tx.revision(incident.subject))
            result = ref(context, task.task_id, "repair_result")
            tx.put_if_revision(result, {"repaired": True}, None)
            self.app.tasks.complete(tx, context, task, result)
        return RunResult(
            outcome="committed", effect_status="confirmed", result_ref=result, reason="repaired"
        )

    async def recover(self, context, task):
        return RecoveryDecision(
            action="resume",
            effect_status="no_effect",
            reason="atomic repair rollback",
            evidence=(task.input_ref,),
        )


def configure(app, *, verification=True, samples=2):
    service = app.dispositions
    signal = SignalDefinition(
        signal_id="unhealthy",
        owner="runtime",
        source="test_reader",
        unit="count",
        sample_interval_ms=1000,
        stale_after_ms=3000,
        label_names=("provider",),
        max_label_sets=1,
    )
    service.register_signal(signal)
    repair = Repair(app)
    service.register_operation(
        OperationDefinition(
            operation_kind="repair_test",
            owner="runtime",
            execution_class="maintenance",
            input_model="runtime.IncidentRecord",
            output_model="runtime.RunResult",
            permission=Permission.RECOVER,
            side_effect="transactional",
            idempotency_required=True,
            success_evidence=("read_back",),
            recovery="resume",
            signal_ids=(signal.signal_id,),
            validation_scenarios=("normal", "interrupted"),
        ),
        repair,
    )

    async def verify(context, incident, task):
        with app.uow.transaction() as tx:
            if not verification or tx.get(incident.subject) != {"healthy": True}:
                return ()
            proof = ref(context, "verify_" + incident.incident_id, "read_proof")
            tx.put_if_revision(proof, {"healthy": True}, tx.revision(proof))
        return (proof,)

    service.register_verifier("verify_test", verify)
    service.configure_rule(
        ctx(app),
        DispositionRule(
            rule_id="repair_rule",
            revision=1,
            signal_id=signal.signal_id,
            comparator="ge",
            threshold=1.0,
            consecutive_samples=samples,
            operation_kind="repair_test",
            max_attempts=2,
            deadline_ms=30000,
            cooldown_ms=10000,
            verification_operation="verify_test",
        ),
    )
    return signal, repair


def sample(app, signal, target, *, value=1.0, provider="local", stamp=None):
    return SignalObservation(
        signal=signal,
        observed_at=stamp or app.test_clock(),
        state="known",
        value=value,
        labels={"provider": provider},
        evidence_refs=(target,),
    )


def seed(app):
    context = ctx(app)
    target = ref(context)
    with app.uow.transaction() as tx:
        tx.put_if_revision(target, {"healthy": False}, None)
    return context, target


def test_signal_staleness_cardinality_and_cross_tenant_evidence(app):
    signal, _ = configure(app, samples=1)
    context, target = seed(app)
    assert (
        app.dispositions.observe(
            context, target, sample(app, signal, target, stamp=later(app.test_clock(), -5))
        )
        == ()
    )
    with pytest.raises(FoundationError):
        app.dispositions.observe(context, target, sample(app, signal, target, provider="second"))
    with pytest.raises(FoundationError):
        app.dispositions.observe(ctx(app, "other"), target, sample(app, signal, target))
    assert app.dispositions.incidents(context) == ()


def test_configuration_and_real_backup_restore_without_overwriting_live_data(app):
    context, target = seed(app)
    values = dict(
        version="config_1",
        deployment_id="local",
        provider_ids=("sqlite",),
        policy_versions=("test",),
        secret_refs=(),
    )
    configuration = ConfigurationSnapshot(
        **values, config_hash=fingerprint(values), activated_at=now()
    )
    app.lifecycle.activate(context, configuration, expected_version=None)
    with pytest.raises(FoundationError):
        app.lifecycle.backup(ctx(app, "other"), "forbidden")
    snapshot = app.lifecycle.backup(context, "snapshot_1")
    assert snapshot.restore_state == "untested" and snapshot.consistency_watermark > 0
    assert app.lifecycle.backup(context, "snapshot_1") == snapshot
    with app.uow.transaction() as tx:
        tx.put_if_revision(target, {"healthy": True}, tx.revision(target))
    restored = app.lifecycle.restore(context, "snapshot_1", "drill_1")
    assert restored.restore_state == "passed" and restored.restore_evidence
    with app.uow.transaction() as tx:
        assert tx.get(target) == {"healthy": True}  # live database never replaced
    with pytest.raises(FileExistsError):
        app.lifecycle.restore(context, "snapshot_1", "drill_1")
    app.lifecycle.path("snapshot_1").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        app.lifecycle.restore(context, "snapshot_1", "drill_2")


def test_configuration_change_invalidates_previous_health_evidence(app):
    context = ctx(app)
    stamp = app.test_clock()
    report = {
        "checked_at": stamp,
        "dependencies": {"logs": {"state": "available", "checked_at": stamp, "elapsed_ms": 0}},
        "capabilities": dict.fromkeys(app.monitoring.required, "available"),
    }
    app.monitoring.publish(context, report)
    values = dict(
        version="changed",
        deployment_id="local",
        provider_ids=("sqlite",),
        policy_versions=("v1",),
        secret_refs=(),
    )
    configuration = ConfigurationSnapshot(
        **values, config_hash=fingerprint(values), activated_at=now()
    )
    app.lifecycle.activate(context, configuration, expected_version=None)
    snapshot = app.monitoring.health(context)
    assert snapshot.readiness == "not_ready"
    assert snapshot.config_version == "changed"
    assert all(o.state == "unknown" for o in snapshot.observations)
    assert app.diagnostics.health(context).capabilities[0].state == "unknown"


def test_rule_revision_cannot_mint_another_unresolved_operation(app):
    signal, _ = configure(app, samples=1)
    context, target = seed(app)
    app.dispositions.observe(context, target, sample(app, signal, target))
    with app.uow.transaction() as tx:
        old = DispositionRule.model_validate(tx.rows("disposition_rules")[0][1]["rule"])
    app.dispositions.configure_rule(context, old.model_copy(update={"revision": 2}))
    app.test_clock.advance(11)
    assert app.dispositions.observe(context, target, sample(app, signal, target)) == ()
    assert len(app.dispositions.incidents(context)) == 1
