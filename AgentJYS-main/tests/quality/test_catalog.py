import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location('quality_catalog', ROOT / 'scripts/quality/catalog.py')
catalog_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog_module)


def catalog():
    return json.loads((ROOT / 'tests/catalog/cases.json').read_text(encoding='utf-8'))


def test_all_88_specs_preserved_without_execution_status():
    value = catalog()
    assert len(value['cases']) == 88
    assert catalog_module.validate_catalog(value, ROOT) == []
    assert all('status' not in row for row in value['cases'])


def test_rejects_missing_duplicate_empty_assertions_and_fake_entrypoint():
    original = catalog()
    mutations = [lambda v: v['cases'].pop(),
                 lambda v: v['cases'].append(copy.deepcopy(v['cases'][0])),
                 lambda v: v['cases'][0].update(expected=[]),
                 lambda v: v['cases'][0].update(automation=[{'path': 'not-present.py', 'coverage': 'full', 'dependency': 'real'}])]
    for mutate in mutations:
        value = copy.deepcopy(original)
        mutate(value)
        assert catalog_module.validate_catalog(value, ROOT)


def test_spec_cannot_contain_an_execution_status_or_unresolved_full_coverage():
    value = catalog()
    value['cases'][0]['status'] = 'PASS'
    assert catalog_module.validate_catalog(value, ROOT)
    value = catalog()
    value['cases'][0]['automation'] = [{'path': 'scripts/quality/catalog.py', 'coverage': 'full', 'dependency': 'real'}]
    value['cases'][0]['open_design_items'] = ['required boundary is unknown']
    assert catalog_module.validate_catalog(value, ROOT)


def test_unknown_paths_select_every_required_case_and_preserve_reason():
    result = catalog_module.select_cases(catalog(), ['new-unrecognized/component.ts'], 'commit')
    assert len(result['case_ids']) == 88
    assert result['reason'] == 'unknown_path_full_selection'
    assert len(result['catalog_sha256']) == 64


def test_business_change_always_includes_permissions_delete_and_durability():
    result = catalog_module.select_cases(catalog(), ['AgentJYS-main/src/aether/api.py'], 'commit')
    assert {'SEC-02', 'SEC-05', 'REM-02', 'REM-12', 'REL-01'} <= set(result['case_ids'])
    assert 'REC-01' in result['case_ids']


def test_dependency_change_broadens_and_docs_exception_is_explicit():
    assert len(catalog_module.select_cases(catalog(), ['AgentJYS-main/uv.lock'], 'commit')['case_ids']) == 88
    docs = catalog_module.select_cases(catalog(), ['docs/README.md'], 'commit')
    assert docs['case_ids'] == [] and docs['reason'] == 'documentation_only_no_product_gate'
    assert len(catalog_module.select_cases(catalog(), [], 'release')['case_ids']) == 88


def test_parent_traversal_never_becomes_documentation_only():
    for path in ('../docs/README.md', './/../docs/README.md', 'docs/../product.md'):
        assert len(catalog_module.select_cases(catalog(), [path], 'commit')['case_ids']) == 88


def test_catalog_render_keeps_all_ids_and_open_coverage():
    value = catalog()
    text = catalog_module.render_catalog(value)
    for row in value['cases']:
        assert f"## {row['id']} " in text
        assert row['preconditions'] in text
    assert text.count('## ') == 88
    selection = catalog_module.select_cases(value, ['AgentJYS-main/pnpm-lock.yaml'], 'commit')
    assert selection['reason'] == 'dependency_or_deployment_full_selection'
    assert len(selection['policy_sha256']) == 64
