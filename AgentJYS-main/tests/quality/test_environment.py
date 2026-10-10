import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module():
    path = ROOT / "scripts/quality/environment.py"
    assert path.exists(), "isolated quality environment provisioner is required"
    spec = importlib.util.spec_from_file_location("quality_environment", path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_rejects_existing_business_namespace():
    with pytest.raises(ValueError, match="quality"):
        module().manifests("aether-p3-demo")


def test_database_services_are_private_and_resource_bounded():
    objects = module().manifests("aether-quality-20261009")
    quota = next(x for x in objects if x["kind"] == "ResourceQuota")
    assert quota["spec"]["hard"]["requests.cpu"] == "4"
    assert quota["spec"]["hard"]["requests.storage"] == "0"
    assert {x["metadata"]["name"] for x in objects if x["kind"] == "Service"} == {
        "postgres",
        "mysql",
        "redis",
    }
    for x in objects:
        if x["kind"] == "Service":
            assert x["spec"].get("type", "ClusterIP") == "ClusterIP"
        if x["kind"] == "Deployment":
            pod = x["spec"]["template"]["spec"]
            assert pod["automountServiceAccountToken"] is False
            assert "nodeName" not in pod
            for c in pod["containers"]:
                assert c["resources"]["requests"] and c["resources"]["limits"]
                assert c["readinessProbe"]


def test_passwords_are_secret_references_not_committed_values():
    objects = module().manifests("aether-quality-20261009")
    assert not any(x["kind"] == "Secret" for x in objects)
    for x in objects:
        if x["kind"] == "Deployment":
            for c in x["spec"]["template"]["spec"]["containers"]:
                for e in c.get("env", []):
                    if "PASSWORD" in e["name"]:
                        assert "secretKeyRef" in e["valueFrom"]


def test_existing_namespace_ownership_is_required():
    with pytest.raises(ValueError, match="ownership"):
        module().verify_owner({"metadata": {"labels": {"owner": "another-task"}}})
    module().verify_owner({"metadata": {"labels": {"owner": "aether-continuous-quality"}}})
