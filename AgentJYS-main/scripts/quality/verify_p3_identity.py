"""Real RuoYi verification using the installed P3 image, no storage/model substitutes.

Run in the owned private test namespace with identity config, service-client and
test-account Secrets mounted. Output is sanitized; no credentials are persisted.
This checks the identity adapter boundary, not a deployed P3 business journey.
"""
import json
import sys
import uuid
from pathlib import Path

def disabled_login_denied(http_status, result):
    return (http_status == 200 and result.get('code') == 1002000001
        and not isinstance(result.get('data'),dict))


def main():
    import httpx
    from aether_platform.auth.ruoyi import RuoyiIdentityVerifier
    from aether_platform.directory import AccessDeniedError
    config=json.loads(Path('/identity-config/identity.json').read_text())['ruoyi']
    assert config['base_url']=='http://backend.aether-quality-20261009.svc.cluster.local:48080'
    verifier=RuoyiIdentityVerifier(config,None)
    client=httpx.Client(base_url=config['base_url']+'/admin-api',timeout=15,trust_env=False)
    results=[];tokens=[];cleanup_details=[];observations={}
    def check(name,fn):
        try:
            fn();results.append({'case':name,'status':'PASS'})
        except Exception as exc:
            results.append({'case':name,'status':'FAIL','error_type':type(exc).__name__})
    def login(user):
        r=client.post('/aether/identity/login',json={'username':user,
            'password':Path('/test-accounts',user).read_text()}).json()
        assert r['code']==0
        token=r['data']['accessToken'];tokens.append(token);return token
    def denied(fn):
        try:fn()
        except AccessDeniedError:return
        raise AssertionError('expected explicit authorization denial')
    bindings={}
    try:
        for user,native,tenant in [('aetherusera','50004','501'),('aetherusera2','50005','501'),('aetheruserb','50006','502')]:
            def mapping(user=user,native=native,tenant=tenant):
                token=login(user);current,binding=verifier.inspect(token)
                assert str(current['user_id'])==native and str(current['tenant_id'])==tenant
                assert binding['business_user_id']=='ry_user_'+native
                assert binding['business_tenant_id']=='ry_tenant_'+tenant
                bindings[user]=binding
            check('P3-ID-MAP-'+user,mapping)
        def separation():
            assert bindings['aetherusera']['business_tenant_id']==bindings['aetherusera2']['business_tenant_id']
            assert bindings['aetherusera']['business_user_id']!=bindings['aetherusera2']['business_user_id']
            assert bindings['aetherusera']['business_tenant_id']!=bindings['aetheruserb']['business_tenant_id']
        check('P3-ID-TENANT-SEPARATION',separation)
        def unmapped():
            token=login('aetherplatform')
            current=verifier.remote('GET','/admin-api/aether/identity/self',headers={'Authorization':'Bearer '+token})
            assert str(current['user_id'])=='50001' and current['user_enabled'] is True
            denied(lambda:verifier.inspect(token))
        check('P3-ID-UNMAPPED-DENIED',unmapped)
        def disabled():
            response=client.post('/aether/identity/login',json={'username':'aetherdisabled',
                'password':Path('/test-accounts/aetherdisabled').read_text()})
            result=response.json()
            observations['disabled_login']={'http_status':response.status_code,'business_code':result.get('code')}
            if isinstance(result.get('data'),dict) and result['data'].get('accessToken'):
                tokens.append(result['data']['accessToken'])
            assert disabled_login_denied(response.status_code,result)
        check('P3-ID-DISABLED-DENIED',disabled)
        def revoked():
            token=login('aetherusera');verifier.inspect(token)
            result=client.post('/system/auth/logout',headers={'Authorization':'Bearer '+token}).json()
            assert result['code']==0
            denied(lambda:verifier.inspect(token))
        check('P3-ID-REVOKED-DENIED',revoked)
        def wrong_client():
            token=login('aetherusera');verifier.inspect(token)
            saved=verifier._secret
            try:
                verifier._secret='intentional-invalid-test-credential'
                denied(lambda:verifier.inspect(token))
            finally:verifier._secret=saved
        check('P3-ID-WRONG-SERVICE-SECRET-DENIED',wrong_client)
    finally:
        cleanup=True
        for token in tokens:
            detail={}
            try:
                response=client.post('/system/auth/logout',headers={'Authorization':'Bearer '+token}).json()
                # An already revoked token can return a nonzero business code.
                # Prove revocation through check-token; mapping refusal is insufficient.
                detail['logout_code']=response['code']
                denied(lambda:verifier.remote('POST','/admin-api/system/oauth2/check-token',
                    auth=httpx.BasicAuth(config['client_id'],verifier._secret),data={'token':token}))
                detail['revoked']=True
            except Exception as exc:
                cleanup=False;detail.update(revoked=False,error_type=type(exc).__name__)
            cleanup_details.append(detail)
        results.append({'case':'P3-ID-TOKEN-CLEANUP','status':'PASS' if cleanup else 'FAIL'})
        verifier.close();client.close()
    report={'run_id':str(uuid.uuid4()),'status':'PASS' if all(r['status']=='PASS' for r in results) else 'FAIL',
        'boundary':'real native identity verifier only; full P3 API and storage not exercised',
        'results':results,'mappings':bindings,'cleanup':cleanup_details,'observations':observations}
    print(json.dumps(report))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':sys.exit(main())
