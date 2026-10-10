import copy
import importlib.util
import json
from pathlib import Path

import pytest


def module():
    path = Path(__file__).parents[2] / "scripts/quality/storage_environment.py"
    assert path.exists(), "TLS storage environment implementation required"
    spec = importlib.util.spec_from_file_location("quality_storage", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def deployment(name):
    return {
        "metadata": {
            "name": name,
            "namespace": "aether-quality-20261009",
            "labels": {"owner": "aether-continuous-quality"},
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": name,
                            "env": [{"name": "EXISTING", "value": "preserve"}],
                            "volumeMounts": [
                                {
                                    "name": "data",
                                    "mountPath": "/var/lib/postgresql/data"
                                    if name == "postgres"
                                    else "/data",
                                }
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "data", "persistentVolumeClaim": {"claimName": "owned-test-data"}}
                    ],
                }
            }
        },
    }


def test_database_rollout_refuses_ephemeral_state():
    for name in ["postgres", "redis"]:
        d = deployment(name)
        d["spec"]["template"]["spec"]["volumes"][0] = {"name": "data", "emptyDir": {}}
        with pytest.raises(ValueError, match="persistent"):
            module().add_database_tls(d)


def test_unused_pvc_cannot_hide_actual_ephemeral_database_mount():
    d = deployment("postgres")
    pod = d["spec"]["template"]["spec"]
    pod["containers"][0]["volumeMounts"][0]["mountPath"] = "/unused"
    pod["containers"][0]["volumeMounts"].append(
        {"name": "actual-data", "mountPath": "/var/lib/postgresql/data"}
    )
    pod["volumes"].append({"name": "actual-data", "emptyDir": {}})
    with pytest.raises(ValueError, match="persistent"):
        module().add_database_tls(d)


def test_pgdata_override_and_nested_mount_must_be_persistent():
    d = deployment("postgres")
    pod = d["spec"]["template"]["spec"]
    pod["containers"][0]["env"].append({"name": "PGDATA", "value": "/custom/data"})
    with pytest.raises(ValueError, match="persistent"):
        module().add_database_tls(d)
    pod["containers"][0]["env"][-1]["value"] = "/var/lib/postgresql/data/nested"
    pod["containers"][0]["volumeMounts"].append(
        {"name": "nested", "mountPath": "/var/lib/postgresql/data/nested"}
    )
    pod["volumes"].append({"name": "nested", "emptyDir": {}})
    with pytest.raises(ValueError, match="persistent"):
        module().add_database_tls(d)


def test_milvus_keeps_required_default_config_files():
    objects = module().milvus_manifests("aether-quality-20261009")
    d = next(o for o in objects if o["kind"] == "Deployment")
    command = d["spec"]["template"]["spec"]["containers"][0]["command"][-1]
    assert "cp -R /milvus/configs/. /config/" in command


def test_tls_changes_reject_foreign_resources_and_preserve_existing_data():
    m = module()
    for name in ["postgres", "redis"]:
        d = deployment(name)
        original = copy.deepcopy(d)
        out = m.add_database_tls(d)
        assert d == original
        pod = out["spec"]["template"]["spec"]
        assert pod["volumes"][0] == original["spec"]["template"]["spec"]["volumes"][0]
        assert (
            pod["containers"][0]["env"]
            == original["spec"]["template"]["spec"]["containers"][0]["env"]
        )
        assert "/tls" in json.dumps(pod)
        d["metadata"]["namespace"] = "aether-p3-demo"
        with pytest.raises(ValueError):
            m.add_database_tls(d)
        d["metadata"]["namespace"] = "aether-quality-20261009"
        d["metadata"]["labels"]["owner"] = "other"
        with pytest.raises(ValueError):
            m.add_database_tls(d)


def test_tls_preserves_legacy_ports_and_rejects_repeated_patch():
    m = module()
    pg = m.add_database_tls(deployment("postgres"))
    assert "ssl=on" in pg["spec"]["template"]["spec"]["containers"][0]["args"]
    redis = m.add_database_tls(deployment("redis"))
    command = redis["spec"]["template"]["spec"]["containers"][0]["command"][-1]
    assert "--port 6379" in command and "--tls-port 6380" in command
    assert '--requirepass "$REDIS_PASSWORD"' in command
    with pytest.raises(ValueError):
        m.add_database_tls(redis)


def test_milvus_uses_private_owned_tls_and_auth_without_shared_state():
    m = module()
    with pytest.raises(ValueError):
        m.milvus_manifests("milvus")
    objects = m.milvus_manifests("aether-quality-20261009")
    assert not any(o["kind"] in ["ResourceQuota", "PersistentVolumeClaim"] for o in objects)
    for obj in objects:
        if obj["kind"] == "Service":
            assert obj["spec"]["type"] == "ClusterIP"
            assert [p["port"] for p in obj["spec"]["ports"]] == [19530]
        if obj["kind"] == "Deployment":
            pod = obj["spec"]["template"]["spec"]
            assert pod["automountServiceAccountToken"] is False
            assert pod["enableServiceLinks"] is False
            assert "@sha256:" in pod["containers"][0]["image"]
            assert pod["containers"][0]["resources"]["limits"]["cpu"] == "1"
    config = next(o for o in objects if o["kind"] == "ConfigMap")["data"]["user.yaml"]
    assert "authorizationEnabled: true" in config
    assert "tlsMode: 1" in config
    import yaml

    security = yaml.safe_load(config)["common"]["security"]
    assert security["defaultRootPassword"] == "${MILVUS_ROOT_PASSWORD}"
    assert security["tlsMode"] == 1
    assert yaml.safe_load(config)["proxy"]["http"]["enabled"] is False
    assert "aether-p3-demo" not in json.dumps(objects)
