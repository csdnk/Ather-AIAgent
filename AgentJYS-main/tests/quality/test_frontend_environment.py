import importlib.util
from pathlib import Path

import pytest


def test_frontend_routes_only_to_owned_backend_and_keeps_assets_local():
    p = Path(__file__).parents[2] / "scripts/quality/frontend_environment.py"
    assert p.exists(), "private frontend environment required"
    spec = importlib.util.spec_from_file_location("quality_frontend", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    with pytest.raises(ValueError):
        m.manifests("aether-p3-demo")
    objects = m.manifests("aether-quality-20261009")
    nginx = objects[0]["data"]["default.conf"]
    assert "http://backend:48080/admin-api/" in nginx
    assert "location /ruoyi/" in nginx
    assert "aether-p3-demo" not in nginx
    svc = next(o for o in objects if o["kind"] == "Service")
    assert svc["spec"]["type"] == "ClusterIP"
    pod = next(o for o in objects if o["kind"] == "Deployment")["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert "@sha256:" in pod["containers"][0]["image"]
