import importlib.util
from pathlib import Path

import pytest


def module():
    p = Path(__file__).parents[2] / "scripts/quality/p3_environment.py"
    assert p.exists(), "explicit isolated P3 identity configuration required"
    spec = importlib.util.spec_from_file_location("quality_p3", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_identity_fixes_distinct_tenants_without_wildcard_provisioning():
    m = module()
    with pytest.raises(ValueError):
        m.identity_configuration("aether-p3-demo")
    c = m.identity_configuration("aether-quality-20261009")["ruoyi"]
    assert c["auto_provision"] is False
    assert c["client_id"] == "aether-quality"
    assert c["allowed_client_ids"] == ["default"]
    assert c["base_url"] == "http://backend.aether-quality-20261009.svc.cluster.local:48080"
    bindings = {(x["ruoyi_user_id"], x["ruoyi_tenant_id"]): x for x in c["mappings"]}
    assert bindings[("50004", "501")]["business_tenant_id"] == "ry_tenant_501"
    assert bindings[("50006", "502")]["business_tenant_id"] == "ry_tenant_502"
    assert len({x["business_user_id"] for x in bindings.values()}) == len(bindings)
    assert not any(p.startswith("maintenance:") for p in c["role_permissions"]["aether_user"])


def test_client_configuration_does_not_store_real_credential_or_enable_login_grants():
    payload = module().service_client_request("synthetic-private-value")
    assert payload["clientId"] == "aether-quality"
    assert payload["secret"] == "synthetic-private-value"
    assert payload["authorizedGrantTypes"] == []
    assert payload["scopes"] == []


def test_disabled_probe_rejects_wrong_password_server_errors_and_returned_tokens():
    path = Path(__file__).parents[2] / "scripts/quality/verify_p3_identity.py"
    spec = importlib.util.spec_from_file_location("p3_probe", path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    assert probe.disabled_login_denied(200, {"code": 1002000001, "data": None})
    assert not probe.disabled_login_denied(500, {"code": 1002000001})
    for code in [0, 401, 500, 1002000000]:
        assert not probe.disabled_login_denied(200, {"code": code})
    assert not probe.disabled_login_denied(
        200, {"code": 1002000001, "data": {"accessToken": "unexpected"}}
    )
