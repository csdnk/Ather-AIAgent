import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / 'scripts/quality/backend.py'
    assert path.exists(), 'owned backend bootstrap required'
    spec = importlib.util.spec_from_file_location('quality_backend', path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_schema_bootstrap_refuses_nonempty_or_foreign_database():
    for name, count in [('ruoyi_quality', 1), ('production', 0)]:
        with pytest.raises(ValueError):
            module().verify_empty_database(name, count)
    module().verify_empty_database('ruoyi_quality', 0)


def test_config_uses_only_local_dependencies_and_real_auth():
    config = module().configuration()
    spring = config['spring']
    db = spring['datasource']['dynamic']['datasource']['master']
    assert db['url'].startswith('jdbc:mysql://mysql:3306/ruoyi_quality?')
    assert db['password'] == '${QUALITY_MYSQL_PASSWORD}'
    assert spring['data']['redis']['host'] == 'redis'
    assert spring['quartz']['auto-startup'] is False
    assert config['yudao']['security']['mock-enable'] is False
    assert config['aether']['ops']['base-url'] == 'http://platform:8090'


def test_generated_bootstrap_password_is_accepted_by_native_login_contract():
    passwords = {module().account_password() for _ in range(8)}
    assert len(passwords) == 8
    assert all(4 <= len(p) <= 16 for p in passwords)
