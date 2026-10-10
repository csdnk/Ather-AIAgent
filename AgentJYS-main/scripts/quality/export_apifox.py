"""Export independent identity regression scenarios using Postman Collection 2.1.

Import into Apifox, then verify native scenario execution separately. This is
partial coverage of SEC-01/05/06, not full security or memory acceptance.
"""
import argparse
import json
from pathlib import Path

ASSERTIONS = Path(__file__).with_name('apifox-assertions.js').read_text(encoding='utf-8')
GUARD = r'''
pm.request.url.update('http://127.0.0.1:1/BLOCKED');
function qualityBlock(message) {
  if (pm.execution && typeof pm.execution.skipRequest === 'function') pm.execution.skipRequest();
  throw new Error('BLOCKED: '+message);
}
const base = pm.environment.get('base_url');
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base || '') || pm.environment.get('quality_namespace') !== 'aether-quality-20261009') {
  qualityBlock('select the owned local port-forward environment');
}
pm.variables.set('quality_token', '');
pm.variables.set('run_id', pm.variables.replaceIn('{{$guid}}'));
'''


def event(kind, script):
    return {'listen': kind, 'script': {'type': 'text/javascript', 'exec': script.splitlines()}}


def login_setup(account, user_id, verify_before_logout=False):
    activate = "pm.request.url.update(base+pm.variables.get('quality_target_path'));"
    if verify_before_logout:
        activate = f'''
  pm.sendRequest({{url:base+'/admin-api/aether/identity/self',method:'GET',
    header:{{Authorization:'Bearer '+pm.variables.get('quality_token')}}}},(error,identity)=>{{
    let valid = false;
    pm.test('SEC-05: original token valid before logout',()=>{{
      qualityRequire(!error,'Initial identity unavailable');
      qualityIdentity(identity.code,identity.json(),'{user_id}','501');
      valid = true;
    }});
    if (!valid) qualityBlock('original token not proven valid');
    pm.request.url.update(base+pm.variables.get('quality_target_path'));
  }});
'''
    return ASSERTIONS + GUARD + f'''
const password = pm.environment.get('{account}_password');
if (!password) qualityBlock('missing local account password');
pm.sendRequest({{url:base+'/admin-api/aether/identity/login',method:'POST',
  header:{{'Content-Type':'application/json'}},
  body:{{mode:'raw',raw:JSON.stringify({{username:'{account}',password}})}}}}, (err,res)=>{{
  if (err) qualityBlock('login transport unavailable');
  try {{qualityLogin(res.code,res.json(),'{user_id}');}}
  catch (error) {{qualityBlock('login did not establish the expected identity');}}
  pm.variables.set('quality_token',res.json().data.accessToken);
  {activate}
}});
'''


def cleanup():
    return '''
const issued = pm.variables.get('quality_token');
if (issued) pm.sendRequest({url:pm.environment.get('base_url')+'/admin-api/system/auth/logout',
  method:'POST',header:{Authorization:'Bearer '+issued}}, (err,res)=>{
  pm.test('CLEANUP: logout issued test token',()=>{
    qualityRequire(!err && res.code===200 && res.json().code===0,'Token cleanup failed');
  });
});
'''


def item(name, path, script, pre=GUARD, method='GET', body=None, auth=True, deferred=False):
    headers = [{'key': 'Content-Type', 'value': 'application/json'},
               {'key': 'X-Test-Run-ID', 'value': '{{run_id}}'}]
    if auth:
        headers.append({'key': 'Authorization', 'value': 'Bearer {{quality_token}}'})
    request = {'method': method, 'header': headers, 'url': 'http://127.0.0.1:1'+path,
               'auth': {'type': 'noauth'},
               'description': 'Target: '+path+'. Default URL intentionally blocked; prerequisite script activates the owned local endpoint. Partial coverage only.'}
    if body is not None:
        request['body'] = {'mode': 'raw', 'raw': json.dumps(body)}
    pre = "pm.variables.set('quality_target_path',"+json.dumps(path)+');\n'+pre
    if not deferred:
        pre += "\npm.request.url.update(base+pm.variables.get('quality_target_path'));"
    return {'name': name, 'request': request,
            'event': [event('prerequest', pre), event('test', ASSERTIONS+'\n'+script)]}


def collection():
    items = []
    for account, uid, tenant in [('aetherusera',50004,501),('aetherusera2',50005,501),('aetheruserb',50006,502)]:
        items.append(item(f'SEC-01/{account}: trusted user and tenant', '/admin-api/aether/identity/self',
            f"pm.test('SEC-01: exact user, tenant and business role',()=>qualityIdentity(pm.response.code,pm.response.json(),'{uid}','{tenant}'));"+cleanup(),
            login_setup(account,uid), deferred=True))
    items.append(item('SEC-01/anonymous: identity denied', '/admin-api/aether/identity/self',
        "pm.test('SEC-01: missing credential rejected',()=>qualityDenied(pm.response.code,pm.response.json(),401));", auth=False))
    items.append(item('SEC-01/bad-password: login denied','/admin-api/aether/identity/login',
        "pm.test('SEC-01: incorrect password rejected',()=>qualityDenied(pm.response.code,pm.response.json(),1002000000));",
        method='POST', body={'username':'aetherusera','password':'not-the-password'}, auth=False))
    items.append(item('SEC-01/disabled-user: login denied','/admin-api/aether/identity/login',
        "pm.test('SEC-01: disabled user rejected',()=>qualityDenied(pm.response.code,pm.response.json(),1002000001));",
        pre=GUARD+"\nif (!pm.environment.get('aetherdisabled_password')) qualityBlock('missing disabled-account password');",
        method='POST', body={'username':'aetherdisabled','password':'{{aetherdisabled_password}}'}, auth=False))
    items.append(item('SEC-01/disabled-tenant: login denied','/admin-api/aether/identity/login',
        "pm.test('SEC-01: disabled tenant rejected',()=>qualityDenied(pm.response.code,pm.response.json(),401));",
        pre=GUARD+"\nif (!pm.environment.get('aetheruserc_password')) qualityBlock('missing disabled-tenant password');",
        method='POST', body={'username':'aetheruserc','password':'{{aetheruserc_password}}'}, auth=False))
    items.append(item('SEC-06/business-user: operations denied','/admin-api/aether/ops/tasks',
        "pm.test('SEC-06: business user cannot read operations',()=>qualityDenied(pm.response.code,pm.response.json(),403));"+cleanup(),
        login_setup('aetherusera',50004), deferred=True))
    items.append(item('SEC-05/logout: original token revoked','/admin-api/system/auth/logout', '''
pm.test('SEC-05: logout acknowledged',()=>qualityRequire(pm.response.code===200 && pm.response.json().code===0,'Logout failed'));
pm.sendRequest({url:pm.environment.get('base_url')+'/admin-api/aether/identity/self',
  method:'GET',header:{Authorization:'Bearer '+pm.variables.get('quality_token')}},(err,res)=>{
  pm.test('SEC-05: original token rejected after logout',()=>{
    qualityRequire(!err,'Revocation read unavailable');
    qualityDenied(res.code,res.json(),401);
  });
});
''', login_setup('aetherusera',50004,verify_before_logout=True), method='POST', deferred=True))
    return {'info': {'name': 'Aether - independent identity regression',
        'schema': 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json',
        'description': 'Import into Apifox project aether. 9 independent API scenarios, partial coverage of SEC-01/05/06. Local private test backend only. Do not run against business deployments.'}, 'item': items}


def environment():
    values = {'base_url': 'http://127.0.0.1:14880', 'quality_namespace': 'aether-quality-20261009'}
    values.update({name+'_password': '' for name in ['aetherusera','aetherusera2','aetheruserb','aetherdisabled','aetheruserc']})
    return {'name': 'Aether quality - local values required', '_postman_variable_scope': 'environment',
        'values': [{'key': k, 'value': v, 'enabled': True, 'type': 'secret' if k.endswith('_password') else 'default'} for k,v in values.items()]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in [('Aether.postman_collection.json',collection()), ('Aether.postman_environment.json',environment())]:
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Exported 9 independent API scenarios; native Apifox execution still needs verification.')
