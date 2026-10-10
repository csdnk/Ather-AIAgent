"""Bounded API regression on existing services, with an owned-data cleanup journal.

Never creates/restarts Kubernetes workloads. Credentials enter from a private
local file and are passed to the local runner over stdin, never copied to reports.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

AUTH='http://127.0.0.1:14890'
P3='http://127.0.0.1:14891'

def owns(ref,user,tenant,run):
    scope=ref.get('scope',{})
    return bool(ref.get('memory_id') and scope.get('user_id')==user and
                scope.get('tenant_id')==tenant and scope.get('session_id')==run)

def owned_account(name,run):
    return bool(re.fullmatch(r'[a-f0-9]{12}',run) and name in {f'aetherqa{run}a',f'aetherqa{run}b'})

def account_recovery_state(matches,submitted):
    if not matches:return 'retained_for_reconciliation' if submitted else 'not_created_verified'
    return 'found' if len(matches)==1 else 'retained_for_reconciliation'

def cleanup_complete(receipt):
    return receipt.get('cleanup_state')=='completed' and receipt.get('remaining_targets')==[]

def processing_cleanup_tasks(processing,user,tenant,run):
    if not owns(processing.get('memory',{}),user,tenant,run):raise ValueError('foreign processing evidence')
    return {t['task_id']:t['state']for t in processing.get('tasks',[])if t.get('kind')=='remember.cleanup'}

def restoration_status(logical,states):
    if not logical or any(s in ['failed','cancelled']for s in states.values()):return 'FAIL'
    return 'PASS' if states and all(s=='succeeded'for s in states.values())else 'BLOCKED'

def read_checkpoint(path,started):
    if not started:return {}
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,dict)or not value.get('run'):raise ValueError('missing run identity')
        if not value.get('submitted') and not value.get('owned_memories'):
            raise ValueError('possibly stale pre-write checkpoint; reconcile before deleting identity')
        return value
    except (OSError,ValueError)as e:raise ValueError('business checkpoint missing or invalid; retain identity')from e

def overall(state):
    if state.get('result')=='FAIL' or state.get('cleanup',{}).get('status')=='FAIL':return 'FAIL'
    if state.get('result')!='PASS' or state.get('cleanup',{}).get('status')!='PASS':return 'BLOCKED'
    if any(a.get('cleanup')not in ['deleted_and_absence_verified','not_created_verified']for a in state['accounts']):return 'BLOCKED'
    return 'PASS'

def write(path,value):
    temp=path.with_suffix('.new');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)

class API:
    def __init__(self):
        import httpx
        self.http=httpx.Client(timeout=35,trust_env=False,follow_redirects=False)
        self.tokens=[]
    def auth(self,method,path,token=None,body=None,visit_tenant=None):
        headers={'Authorization':'Bearer '+token}if token else {}
        if visit_tenant is not None:headers['visit-tenant-id']=str(visit_tenant)
        r=self.http.request(method,AUTH+'/admin-api'+path,headers=headers,json=body)
        b=r.json()
        if r.status_code!=200 or b.get('code')!=0:raise RuntimeError(f'identity API {path.split("?")[0]} HTTP {r.status_code} code {b.get("code")}')
        return b.get('data')
    def login(self,row):
        token=self.auth('POST','/aether/identity/login',body={k:row[k]for k in ['username','password']})['accessToken']
        self.tokens.append(token);return token
    def p3(self,method,path,token,body=None,operation=None):
        h={'Authorization':'Bearer '+token}
        if operation:h['X-Operation-ID']=operation
        r=self.http.request(method,P3+path,headers=h,json=body)
        return r.status_code,r.json()
    def close(self):
        for token in self.tokens:
            try:self.auth('POST','/system/auth/logout',token)
            except Exception:pass
        self.http.close()

def cleanup(api,token,checkpoint,user,tenant,journal):
    """Only delete sources captured from this run's save/correction receipts.

    Logical exclusion and physical cleanup are reported independently. Accounts
    remain available if cleanup is unresolved; do not disable running-task owners.
    """
    previous=json.loads(journal.read_text(encoding='utf-8'))if journal.exists()else{}
    result={'status':'PASS','restoration_scope':'business visibility and provider cleanup under product retention policy','logical_restored':True,'physical_restored':True,'sources':[],'tasks':[]}
    run=checkpoint.get('run');refs=checkpoint.get('owned_memories',[])
    if any(not owns(r,user,tenant,run)for r in refs):raise RuntimeError('cleanup refused: foreign object')
    if checkpoint.get('submitted') and not refs:
        operation=checkpoint.get('expectedOperation','')
        if operation=='save_'+str(run):
            code,lookup=api.p3('GET','/p3/operation-requests/'+operation+'?kind=remember.save',token)
            if code==200 and lookup.get('state')=='found' and lookup.get('operation_id')==operation:
                for _ in range(20):
                    code,saved=api.p3('GET','/p3/operations/'+lookup['job_id']+'/result',token)
                    if code==200 and saved.get('saved')is True:
                        recovered=saved.get('memories',[])
                        if len(recovered)!=1 or not owns(recovered[0],user,tenant,run)or saved.get('operation_id')!=operation:
                            raise RuntimeError('cleanup refused: recovered receipt identity mismatch')
                        refs.extend(recovered);source=saved['source']
                        checkpoint.setdefault('owned_sources',[]).append(source)
                        checkpoint.setdefault('source_bindings',[]).append({'source':source,'memory':recovered[0]})
                        break
                    if code!=400 or saved.get('code')!='REQUEST_IN_PROGRESS':break
                    time.sleep(3)
        if not refs:
            result.update(status='BLOCKED',logical_restored=False,physical_restored=False,reason='submitted write has no confirmed receipt after original-operation reconciliation')
            write(journal,result);return result
    task_ids=set(checkpoint.get('cleanup_tasks',[]))
    for source in checkpoint.get('owned_sources',[]):
        sid=source['source_id'];code,meta=api.p3('GET','/p3/sources/'+sid,token)
        bindings=checkpoint.get('source_bindings',[])
        bound=any(b.get('source')==source and owns(b.get('memory',{}),user,tenant,run)for b in bindings)
        if code!=200 or meta.get('source_id')!=sid or not bound:
            raise RuntimeError('cleanup refused: source identity does not match original session')
        op='cleanup_'+run+'_'+sid[:12]
        old=next((r for r in previous.get('sources',[])if r['source_id']==sid and r.get('http')==200),None)
        if old:code,receipt=old['http'],old['receipt']
        else:code,receipt=api.p3('POST','/p3/sources/'+sid+'/delete',token,{'expected_revision':meta['revision'],'reason':'owned live regression '+run},op)
        row={'source_id':sid,'http':code,'receipt':receipt};result['sources'].append(row);write(journal,result)
        if code!=200 or receipt.get('blocked')is not True:
            result.update(status='FAIL',logical_restored=False,physical_restored=False);continue
        task_ids.update(receipt.get('task_ids',[]))
        if not cleanup_complete(receipt):result['physical_restored']=False
        _,after=api.p3('GET','/p3/sources/'+sid,token)
        if after.get('valid')is not False:result.update(status='FAIL',logical_restored=False)
    for ref in refs:
        code,b=api.p3('POST','/p3/remember/body',token,ref)
        if code!=200 or b.get('outcome')!='excluded' or b.get('content')is not None:
            result.update(status='FAIL',logical_restored=False)
    end=time.monotonic()+120
    states={t:'unknown'for t in task_ids};pending=set(task_ids)
    baseline_logical=result['logical_restored']
    while pending and time.monotonic()<end:
        final_objects_deleted=True
        for ref in refs:
            code,b=api.p3('GET','/p3/remember/'+ref['memory_id']+'/processing',token)
            if code!=200:final_objects_deleted=False;continue
            found=processing_cleanup_tasks(b,user,tenant,run)
            if b.get('memory_status')!='deleted':final_objects_deleted=False
            for task in states:
                if task in found:states[task]=found[task]
            result['source_retention']=b.get('source_retention')
            result['physical_restored']=result['physical_restored'] and b.get('physical_erasure')is True
            for mid in b.get('derived_memory_ids',[]):
                code,derived=api.p3('GET','/p3/remember/'+mid,token)
                gone=code==410 and derived.get('code')=='MEMORY_GONE'
                deleted=code==200 and owns(derived.get('ref',{}),user,tenant,run) and derived.get('status')=='deleted'
                if not (gone or deleted):final_objects_deleted=False
        result['logical_restored']=baseline_logical and final_objects_deleted
        pending={t for t,s in states.items()if s not in ['succeeded','failed','cancelled']}
        result['tasks']=[{'task_id':t,'state':s}for t,s in states.items()]
        write(journal,result)
        if pending:time.sleep(3)
    if task_ids:result['status']=restoration_status(result['logical_restored'],states)
    if pending:result.update(status='BLOCKED',pending_tasks=sorted(pending))
    # PASS here means the documented business restoration scope. Physical bytes,
    # tombstones and audit retention remain separately reported, never erased.
    write(journal,result);return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--credentials',required=True);p.add_argument('--native',required=True);p.add_argument('--output',required=True);p.add_argument('--scene-id',type=int,default=8835598);p.add_argument('--allow-existing-model',action='store_true');p.add_argument('--prepare-only',action='store_true');a=p.parse_args()
    if not a.allow_existing_model and not a.prepare_only:raise SystemExit('BLOCKED: explicit existing-model authorization is required')
    if a.scene_id!=8835598:raise SystemExit('BLOCKED: only the first save/read/Recall scene is admitted until live cleanup is verified')
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    rows=json.loads(Path(a.credentials).read_text(encoding='utf-8-sig'))['users']
    accounts={int(r['id']):r for r in rows};run=uuid.uuid4().hex[:12];api=API()
    state={'run_id':run,'deployment':'aether-p3-demo','scene_id':a.scene_id,'accounts':[],'result':'NOT_RUN'};write(out/'journal.json',state)
    tokens={};env={'environment_kind':'live_existing_deployment','live_admission':'aether-p3-demo-700417ea','auth_base':AUTH,'p3_base':P3}
    try:
        cleanup_admin=api.login(accounts[50001])
        authority=api.auth('GET','/aether/identity/self',cleanup_admin)
        if 'system:user:delete'not in authority.get('permissions',[]):raise RuntimeError('existing cleanup administrator lacks delete permission')
        for slot,admin_id,seed_id,logical in [('a',50002,50004,'aetherusera'),('b',50003,50006,'aetheruserb')]:
            admin=api.login(accounts[admin_id]);name=f'aetherqa{run}{slot}'
            tenant_id=api.auth('GET','/aether/identity/self',admin)['tenant_id']
            roles=api.auth('GET','/system/role/page?pageNo=1&pageSize=100',admin)['list'];ordinary=[r for r in roles if r['code']=='aether_user'];assert len(ordinary)==1
            record={'username':name,'admin_id':admin_id,'seed_id':seed_id,'tenant_id':tenant_id,'create_submitted':True};state['accounts'].append(record);write(out/'journal.json',state)
            uid=api.auth('POST','/system/user/create',admin,{'username':name,'nickname':'API regression '+run,'password':accounts[seed_id]['password'],'postIds':[]});record['id']=uid;write(out/'journal.json',state)
            api.auth('POST','/system/permission/assign-user-role',admin,{'userId':uid,'roleIds':[ordinary[0]['id']]})
            token=api.login({'username':name,'password':accounts[seed_id]['password']});tokens[slot]=token
            code,identity=api.p3('GET','/p3/auth/me',token);assert code==200 and identity['scope']['user_id']=='ry_user_'+str(uid)
            code,caps=api.p3('GET','/p3/capabilities',token);assert code==200 and caps['object_storage']=='ceph' and caps['metadata_storage']=='postgresql'
            record['scope']=identity['scope'];write(out/'journal.json',state)
            env.update({logical+'_username':name,logical+'_password':accounts[seed_id]['password'],logical+'_expected_p3_user_id':identity['scope']['user_id'],logical+'_expected_p3_tenant_id':identity['scope']['tenant_id']})
        if a.prepare_only:
            state.update(result='PASS',scope='identity provisioning and cleanup only; no business write or model invocation')
        else:
            state['business_started']=True;write(out/'journal.json',state)
            payload={'native':str(Path(a.native).resolve()),'scene_id':a.scene_id,'environment':env,'output':str(out.resolve())}
            r=subprocess.run(['node',str(Path(__file__).with_name('run_live_business.cjs'))],input=json.dumps(payload),text=True,capture_output=True,timeout=650)
            state['runner_exit']=r.returncode
            state['result']=json.loads((out/'result.json').read_text(encoding='utf-8'))['status'] if (out/'result.json').exists() else 'BLOCKED'
    except Exception as e:
        state.update(result='BLOCKED',error_type=type(e).__name__)
    finally:
        if not state.get('business_started'):
            state['cleanup']={'status':'PASS','logical_restored':True,'physical_restored':True,'reason':'no business runner started'}
        try:checkpoint=read_checkpoint(out/'checkpoint.json',state.get('business_started',False))
        except ValueError:checkpoint=None;state['cleanup']={'status':'BLOCKED','reason':'missing_or_invalid_checkpoint'}
        if tokens.get('a') and checkpoint is not None:
            home=state['accounts'][0].get('scope',{})
            try:state['cleanup']=cleanup(api,tokens['a'],checkpoint,home.get('user_id'),home.get('tenant_id'),out/'cleanup.json')
            except Exception as e:state['cleanup']={'status':'BLOCKED','error_type':type(e).__name__}
        for account in state['accounts']:
            if not owned_account(account['username'],run):
                account['cleanup']='BLOCKED_owner_mismatch';continue
            if not account.get('id'):
                try:
                    admin=api.login(accounts[account['admin_id']])
                    page=api.auth('GET','/system/user/page?pageNo=1&pageSize=100&username='+account['username'],admin)
                    matches=[r for r in page['list']if r['username']==account['username']]
                    recovery=account_recovery_state(matches,account.get('create_submitted',False))
                    if recovery!='found':account['cleanup']=recovery;continue
                    if len(matches)!=1:raise RuntimeError('ambiguous account recovery')
                    account['id']=matches[0]['id'];write(out/'journal.json',state)
                except Exception:account['cleanup']='retained_for_reconciliation';continue
            if account['username'].endswith('a') and state.get('cleanup',{}).get('status')!='PASS':
                account['cleanup']='retained_for_reconciliation';continue
            try:
                admin=api.login(accounts[50001]);tenant=account['tenant_id']
                current=api.auth('GET','/system/user/get?id='+str(account['id']),admin,visit_tenant=tenant)
                assert current['username']==account['username']
                api.auth('DELETE','/system/user/delete?id='+str(account['id']),admin,visit_tenant=tenant)
                remaining=api.auth('GET','/system/user/page?pageNo=1&pageSize=100&username='+account['username'],admin,visit_tenant=tenant)
                assert not any(r['username']==account['username']for r in remaining['list'])
                account['cleanup']='deleted_and_absence_verified'
            except Exception as e:account['cleanup']='BLOCKED_'+type(e).__name__
        state['overall_status']=overall(state);write(out/'journal.json',state);api.close()
    print(json.dumps({'run_id':run,'result':state['result'],'cleanup':state.get('cleanup',{}).get('status'),'overall_status':state['overall_status'],'report':str(out/'journal.json')}))
    if state['overall_status']!='PASS':raise SystemExit(2)

if __name__=='__main__':main()
