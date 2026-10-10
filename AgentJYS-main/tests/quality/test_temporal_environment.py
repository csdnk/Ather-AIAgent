import importlib.util
import json
from pathlib import Path

import pytest


def module():
    path = Path(__file__).parents[2] / 'scripts/quality/temporal_environment.py'
    assert path.exists(), 'dedicated Temporal environment module required'
    spec = importlib.util.spec_from_file_location('quality_temporal', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_only_owned_quality_namespace_and_separate_databases():
    m = module()
    with pytest.raises(ValueError):
        m.manifests('aether-p3-demo')
    config = m.server_config('synthetic-password', '10.0.0.1')
    stores = config['persistence']['datastores']
    assert stores['default']['sql']['databaseName'] != stores['visibility']['sql']['databaseName']
    assert all(s['sql']['connectAddr'] == 'postgres:5432' for s in stores.values())


def test_private_bounded_no_shared_storage_or_credentials_in_manifests():
    objects = module().manifests('aether-quality-20261009')
    assert not any(o['kind'] in ['PersistentVolumeClaim', 'ResourceQuota', 'Namespace'] for o in objects)
    for o in objects:
        if o['kind'] == 'Service':
            assert o['spec']['type'] == 'ClusterIP'
        if o['kind'] in ['Deployment', 'Job']:
            pod = o['spec']['template']['spec']
            assert pod['automountServiceAccountToken'] is False
            assert pod['enableServiceLinks'] is False
            for c in pod.get('containers', []) + pod.get('initContainers', []):
                assert '@sha256:' in c['image']
                assert c['resources']['limits']
                assert c['securityContext']['allowPrivilegeEscalation'] is False
    assert 'aether-p3-demo' not in json.dumps(objects)
    assert 'SQL_PASSWORD' in json.dumps(objects)
