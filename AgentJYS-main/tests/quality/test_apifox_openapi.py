import importlib.util
from pathlib import Path


def module():
    file = Path(__file__).resolve().parents[2] / 'scripts/quality/apifox_openapi.py'
    assert file.exists(), 'Apifox source-aware OpenAPI converter required'
    spec = importlib.util.spec_from_file_location('apifox_openapi', file)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_gateway_conversion_excludes_unrouted_native_endpoints_and_preserves_models():
    spec = {'openapi':'3.0.1','info':{'title':'native','version':'1'},
            'paths': {'/admin-api/system/auth/logout':{'post':{'responses':{}}},
                      '/app-api/login':{'post':{'responses':{}}},'/test':{'get':{'responses':{}}}},
            'components': {'schemas': {'Result': {'type':'object','required':['code']}}}}
    result = module().prepare(spec, 'ruoyi')
    assert set(result['paths']) == {'/system/auth/logout'}
    assert result['components']['schemas']['Result']['required'] == ['code']
    assert result['servers'][0]['url'].endswith('/ruoyi-api')
    assert '/admin-api/system/auth/logout' in spec['paths']


def test_p3_docs_keep_internal_transport_and_mark_side_effects():
    spec={'openapi':'3.1.0','info':{'title':'P3','version':'1'},'paths':{
        '/p3/remember':{'post':{'responses':{}}},'/p3/live':{'get':{'responses':{}}}}}
    result=module().prepare(spec,'p3')
    assert result['servers'][0]['url']=='http://127.0.0.1:14881'
    assert result['paths']['/p3/remember']['post']['x-aether-risk']=='write'
    assert result['paths']['/p3/live']['get']['x-aether-risk']=='read'


def test_agent_prefix_is_removed_only_for_observed_agent_mount():
    spec={'info':{'title':'agent','version':'1'},'paths':{
        '/agent/auth/me':{'get':{'responses':{}}},'/platform-ops/v1/commands':{'post':{'responses':{}}}}}
    result=module().prepare(spec,'platform')
    assert set(result['paths'])=={'/auth/me'}
    assert result['servers'][0]['url'].endswith('/ruoyi-agent')
