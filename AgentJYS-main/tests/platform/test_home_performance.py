"""Home metrics are authorized separately and never describe live workflows."""

from types import SimpleNamespace

import pytest
from test_p3_admin_diagnostics import operator, runtime, seed

from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics
from aether_agent_memory.runtime.foundation.common import FoundationError


@pytest.mark.parametrize("role", ["aether_tenant_admin", "aether_user"])
def test_global_performance_rejects_non_platform_before_reading_logs(tmp_path, role):
    identity, ctx, _ = operator(tmp_path, role)
    service = AdminDiagnostics(runtime(identity, tmp_path), SimpleNamespace())
    with pytest.raises(FoundationError, match="diagnostic permission"):
        service.performance(ctx)


def test_home_performance_uses_real_working_kind_and_no_temporal_network(tmp_path, monkeypatch):
    identity, ctx, _ = operator(tmp_path)
    host = runtime(identity, tmp_path)
    seed(host)
    host.foundation.telemetry = object()
    service = AdminDiagnostics(host, SimpleNamespace())
    observed = []

    def metrics(telemetry, stamp, *, working_memory_ids):
        observed.append(working_memory_ids)
        return {"embedding": {"rate": 2.0}, "working_memory": {"p99_ms": 20}}

    # Only the external log reader is substituted; authorization and metadata are real.
    monkeypatch.setattr(
        "aether_agent_memory.runtime.flows.performance_observations.performance_observations",
        metrics,
    )
    result = service.performance(ctx)
    assert observed == [{"m1"}]
    assert result["memory_observations"]["embedding"]["rate"] == 2.0
    assert result["memory_observations"]["compression"]["ratio"] is None


def test_postgres_metadata_statistics_never_open_business_transaction(tmp_path, monkeypatch):
    identity, ctx, _ = operator(tmp_path)
    host = runtime(identity, tmp_path)
    host.foundation.telemetry = SimpleNamespace(backend="postgresql")
    service = AdminDiagnostics(
        host,
        SimpleNamespace(
            ledger=SimpleNamespace(config=SimpleNamespace(namespace="ours", deployment_id="d"))
        ),
    )
    # Authorization is independently covered above; statistics must not acquire the UOW lock.
    service.platform = lambda ctx: None

    def forbidden_transaction():
        raise AssertionError("statistics acquired the business lock")

    monkeypatch.setattr(identity.uow, "transaction", forbidden_transaction)
    monkeypatch.setattr(
        "aether_agent_memory.runtime.flows.performance_metadata.performance_metadata",
        lambda *args, **kwargs: ({"m1"}, {"compression": {"ratio": 2.0}}),
    )
    monkeypatch.setattr(
        "aether_agent_memory.runtime.flows.performance_observations.performance_observations",
        lambda *args, **kwargs: {},
    )
    assert service.performance(ctx)["memory_observations"]["compression"]["ratio"] == 2.0
