import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).parents[2] / 'scripts/quality/datasets.py'
    assert path.exists(), 'traceable real-data builder required'
    spec = importlib.util.spec_from_file_location('datasets', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_changed_source_bytes_are_rejected_before_parsing():
    with pytest.raises(ValueError, match='hash'):
        module().verify_bytes(b'changed snapshot', '0' * 64)


def test_build_real_rows_from_verified_sources_and_separate_synthetic_inputs(tmp_path, monkeypatch):
    # Synthetic source-format fixtures; real public hashes are checked by the CLI.
    import hashlib
    import json
    m = module()
    source = tmp_path
    fixtures = {
        'nasa': json.dumps({'parameters': {'T2M': {'units': 'C'}, 'PRECTOTCORR': {'units': 'mm/day'}}, 'properties': {'parameter': {'T2M': {'20240101': 1.49, '20240102': 2.22}, 'PRECTOTCORR': {'20240101': 0, '20240102': 5.5}}}}),
        'worldbank': json.dumps([{}, [{'date': str(y), 'value': 6418.1} for y in range(2020, 2024)]]),
        'rfc': '9.2.2.  Idempotent Methods\nPUT, DELETE, and safe request methods\n9.2.3.'}
    sources = {}
    for key, text in fixtures.items():
        data = text.encode('utf-8')
        (source / key).write_bytes(data)
        sources[key] = (key, hashlib.sha256(data).hexdigest(), 'https://example.org/' + key)
    monkeypatch.setattr(m, 'SOURCES', sources)
    rows = m.build(source)
    assert {r['tenant_account'] for r in rows} == {'aetherusera', 'aetheruserb'}
    real = [r for r in rows if r['dataset_kind'] == 'real_public_source']
    assert len(real) >= 6
    assert all(r['expected_fact'] and len(r['source_sha256']) == 64 for r in real)
    assert all(r['source_url'].startswith('https://') for r in real)
    assert all(r['expected_behavior'] == 'save_read_recall_same_source' for r in real)
    assert any('6418.1' in r['text'] and '6418.1' in r['expected_fact'] for r in real)
    synthetic = [r for r in rows if r['dataset_kind'] == 'synthetic_boundary']
    assert any(r['text'] == '' and r['expected_behavior'] == 'reject_empty_content' for r in synthetic)
    assert len({r['dataset_id'] for r in rows}) == len(rows)
