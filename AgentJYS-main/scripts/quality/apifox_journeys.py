"""Native multi-step API journeys, built from the current user-owned export."""
import argparse
import copy
import importlib.util
import json
import uuid
from pathlib import Path

HERE = Path(__file__).parent
ENGINE = (HERE / 'apifox-journey-step.js').read_text(encoding='utf-8')
spec = importlib.util.spec_from_file_location('apifox_native', HERE / 'apifox_native.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def definitions():
    flows = []
    def stage(action, name, path, method='get', base='base_url', **kw):
        return dict(action=action, name=name, path=path, method=method, base=base, **kw)
    def identity(account, uid, tid, slot='A', full=False):
        base='auth_base' if full else 'base_url'
        values={} if full else dict(account=account,user_id=str(uid),tenant_id=str(tid))
        return [stage('login','登录并获取测试身份','/admin-api/aether/identity/login','post',base,slot=slot,**values),
                stage('identity','读取当前身份并核验用户与租户','/admin-api/aether/identity/self',base=base,auth=True,slot=slot,**values)]
    def logout(slot='A',full=False):
        base='auth_base' if full else 'base_url'
        return [stage('logout','注销本轮令牌','/admin-api/system/auth/logout','post',base,auth=True,slot=slot),
                stage('denied','原令牌再次读取必须被拒绝','/admin-api/aether/identity/self',base=base,auth=True,slot=slot,httpCode=200,businessCode=401)]
    def add(key,name,environment,stages,**kw):
        flows.append(dict(key=key,name=name,environment=environment,stages=stages,**kw))
    for account,uid,tid,label in [('aetherusera',50004,501,'租户A用户A'),('aetherusera2',50005,501,'租户A用户A2'),('aetheruserb',50006,502,'租户B用户B')]:
        seq=identity(account,uid,tid)
        seq.append(stage('denied','普通业务用户读取运维任务必须被拒绝','/admin-api/aether/ops/tasks',auth=True,httpCode=200,businessCode=403))
        add(account,'SEC-01 '+label+' 登录_身份_权限_退出_失效','quality_identity',seq+logout())
    seq=identity('aetherusera',50004,501)+identity('aetheruserb',50006,502,'B')
    seq+= [stage('identity','切回A令牌仍属于租户A','/admin-api/aether/identity/self',auth=True,user_id='50004',tenant_id='501')]
    seq+=logout('B')+logout('A')
    add('tenant-switch','SEC-01 双租户交替登录与令牌身份隔离','quality_identity',seq)
    seq=identity('aetherusera',50004,501)
    invalid_users=[('缺少用户名',{}),('用户名为null',{'username':None}),('空用户名',{'username':''}),
                   ('用户名少于4字符',{'username':'abc'}),('用户名超过30字符',{'username':'a'*31}),
                   ('用户名含中文',{'username':'测试用户'}),('用户名含符号',{'username':'user!'})]
    invalid_passwords=[('缺少密码',{}),('密码为null',{'password':None}),('空密码',{'password':''}),
                       ('密码少于4字符',{'password':'abc'}),('密码超过16字符',{'password':'a'*17})]
    payloads=[(name,dict(password='Boundary9',**fields)) for name,fields in invalid_users]
    payloads += [(name,dict(username='aetherusera',**fields)) for name,fields in invalid_passwords]
    for name,payload in payloads:
        seq.append(stage('denied',name+'：拒绝且不签发令牌','/admin-api/aether/identity/login','post',
                         payload=payload,httpCode=200,businessCode=400))
    seq.append(stage('identity','输入失败后原身份仍有效','/admin-api/aether/identity/self',auth=True,user_id='50004',tenant_id='501'))
    add('identity-input-boundaries','CT-03/SEC-01 登录输入边界与身份持续有效','quality_identity',seq+logout())
    seq=identity('aetherusera',50004,501)
    seq.append(stage('identity','伪造用户租户角色声明不能改变身份','/admin-api/aether/identity/self',
                     auth=True,user_id='50004',tenant_id='501',assertSameRoles=True,headers={'X-User-Id':'50006','X-Tenant-Id':'502','X-Role':'aether_platform_admin'}))
    seq.append(stage('denied','无效Bearer令牌必须被拒绝','/admin-api/aether/identity/self',
                     auth=True,headers={'Authorization':'Bearer invalid-aether-test-token'},httpCode=200,businessCode=401))
    seq.append(stage('identity','无效令牌请求后原令牌仍有效','/admin-api/aether/identity/self',auth=True,user_id='50004',tenant_id='501'))
    add('identity-forged-claims','SEC-01/CT-02 伪造身份声明与无效令牌','quality_identity',seq+logout())
    add('public-chain','DEP-01 公网部署联合冒烟','public_readonly',[
        stage('denied','管理API匿名身份拒绝','/ruoyi-api/aether/identity/self',base='public_origin',httpCode=200,businessCode=401),
        stage('http401','Agent匿名身份拒绝','/ruoyi-agent/auth/me',base='public_origin'),
        stage('http401','Agent匿名会话列表拒绝','/ruoyi-agent/chat-api/conversations',base='public_origin')])
    health=[stage('health','P3进程存活','/p3/live',base='p3_base',field='liveness',value='alive'),
            stage('health','P3依赖就绪','/p3/readyz',base='p3_base',field='readiness',value='ready'),
            stage('denied','P3匿名身份拒绝','/p3/auth/me',base='p3_base',httpCode=401,businessCode='UNAUTHENTICATED')]
    add('internal-chain','DEP-01/SEC-01 P3健康与认证联合检查','private_readonly',health)
    def business(action,name,path,method='post',**kw):
        return stage(action,name,path,method,base='p3_base',auth=True,**kw)
    def operation(action,name,path):
        seq=[business(action,name,path)]
        seq += [business('poll',name+'：查询原任务 '+str(i)+'/3','/p3/operations/{job_id}/result','get',kind=action,finalPoll=i==3) for i in range(1,4)]
        return seq
    for corrected in [False,True]:
        seq=identity(None,None,None,full=True)
        seq.append(business('p3identity','核验P3业务身份映射','/p3/auth/me','get'))
        seq+=operation('save','创建独立测试记忆','/p3/remember')
        seq.append(business('body','回读正文并校验来源','/p3/remember/body'))
        seq+=operation('replay','相同操作ID重试必须幂等','/p3/remember')
        if corrected:
            seq.append(business('snapshot','读取更正前实际版本','/p3/remember/{memory_id}','get'))
            seq+=operation('correct','将保留期限由17天更正为23天','/p3/remember/{memory_id}/correct')
            seq.append(business('body','核对当前版本与更正后的正文','/p3/remember/body'))
        seq+=operation('recall','按本轮会话召回并核对当前版本','/p3/recall')
        seq.append(business('snapshot','读取删除前实际修订号','/p3/remember/{memory_id}','get'))
        seq.append(business('delete','删除本轮记忆','/p3/remember/{memory_id}/delete'))
        seq.append(business('deleted','验证删除后正文被排除','/p3/remember/body'))
        seq+=operation('recall_deleted','验证删除后再次召回为空','/p3/recall')
        seq+=logout(full=True)
        add('memory-correct' if corrected else 'memory-lifecycle',
            'REM-01/02/07/12 REC-01 记忆创建_更正_召回_删除' if corrected else 'REM-01/02/12 REC-01 记忆保存_幂等_召回_删除',
            'isolated_acceptance',seq,full=True)
    real=copy.deepcopy(next(x for x in flows if x['key']=='memory-lifecycle'))
    real.update(key='memory-real-data',name='REM-01/02/12 REC-01 真实公开数据_逐行保存_幂等_召回_删除',dataset=True)
    flows.append(real)
    for key in ['internal-chain','aetherusera','memory-lifecycle']:
        f=copy.deepcopy(next(x for x in flows if x['key']==key))
        f['key']='perf-'+key; f['name']='PERF 探索基线 '+f['name']; f['performance']=True
        if key=='internal-chain': f['name']=f['name'].replace('P3健康','P3内部健康')
        flows.append(f)
    for flow in flows:
        for i,s in enumerate(flow['stages']):
            s.update(flow=flow['key'],index=i,first=i==0,last=i==len(flow['stages'])-1,
                     environment=flow['environment'],full=flow.get('full',False),dataset=flow.get('dataset',False),performance=flow.get('performance',False),publicOrigin=native.ORIGIN)
    return flows


def build(source, docs=None):
    package=copy.deepcopy(source)
    wanted={'Aether_云端管理API','Aether_记忆核心P3','Aether_Agent工作流','Aether_身份回归'}
    if docs: wanted.update({'Aether_测试与验收入口','Aether_性能与容量'})
    package['moduleSettings']=[m for m in source['moduleSettings'] if m['name'] in wanted]
    modules={m['name']:int(m['id']) for m in package['moduleSettings']}
    mids=set(modules.values())
    for key in ['apiCollection','schemaCollection','securitySchemeCollection','responseCollection']:
        package[key]=[copy.deepcopy(f) for f in source[key] if int(f.get('moduleId',0)) in mids]
    # No stale environments, credentials, sample modules, docs, or incidental data.
    for key in ['environments','docCollection','socketCollection','customEndpointCollection','webSocketCollection','socketIOCollection','mcpClientCollection','requestCollection','databaseConnections','globalVariables','commonScripts','testCaseReferences']:
        package[key]=[]
    old={s['name']:s for root in source['apiTestCaseCollection'] for g in [root,*root.get('children',[])] for s in g.get('items',[])}
    # Preserve IDs when correcting initial labels against the authoritative specs.
    for name,value in list(old.items()):
        corrected=name.replace('SEC-01/05/06 ','SEC-01 ').replace('REM-01/03/08 REC-01 ','REM-01/02/07/12 REC-01 ').replace('REM-01/08 REC-01 ','REM-01/02/12 REC-01 ')
        old.setdefault(corrected,value)
    apis={(int(f['moduleId']),a['api']['method'],a['api']['path']):a['api'] for f in package['apiCollection'] for a in native.flatten([f])}
    groups=[dict(name='05_多步骤功能与API端到端',children=[],items=[]),dict(name='06_性能场景_等待资源准入',children=[],items=[])]
    counter=910000001
    envs={next((v.get('value') for v in e.get('variables',[]) if v['name']=='environment_kind'),None):int(e['id']) for e in source['environments']}
    for flow in definitions():
        steps=[]
        for cfg in flow['stages']:
            is_p3=cfg['path'].startswith('/p3/')
            is_agent=cfg['path'].startswith('/ruoyi-agent/')
            module=modules['Aether_记忆核心P3' if is_p3 else 'Aether_Agent工作流' if is_agent else 'Aether_云端管理API']
            path=cfg['path'].removeprefix('/ruoyi-agent') if is_agent else cfg['path'].removeprefix('/admin-api').removeprefix('/ruoyi-api') if not is_p3 else cfg['path']
            api=apis[(module,cfg['method'],'/aether/ops/{resource}' if path=='/aether/ops/tasks' else path)]
            header='const cfg='+json.dumps(cfg,ensure_ascii=False)+';\n'
            step=native.step(cfg['name'],cfg['method'],path,module,header+"const phase='pre';\n"+ENGINE,header+"const phase='post';\n"+ENGINE,{} if cfg['method']=='post' else None,api['id'])
            step['httpApiCase']['id']=counter; counter+=1; steps.append(step)
        description='API 多步骤业务场景；每步为原生HTTP请求，变量只在本次运行传递。观察窗口60秒，每个异步操作最多3次原任务查询；同步完成时显式跳过查询。使用1线程1轮功能验证；请关闭遇错即停止，以执行末尾令牌注销。停止整次运行仍须按对象记录人工对账。'
        if flow.get('full'): description+=' G0阻塞：源码准入开关关闭。缺少完整隔离P3/Temporal/存储及供应商费用硬限制，不允许仅改环境变量启动写入。'
        if flow.get('performance'): description+=' 性能配置为能力探索；源码准入开关关闭，未承诺SLA。原生闭环并发不能替代固定到达速率容量验收。'
        s=native.scenario(flow['name'],steps,description)
        s['options']=dict(environmentId=envs[flow['environment']],useDataSetId=-1,iterationCount=1,
                          threadCount=1,runnerId=0,onError='ignore',delayItem=250 if flow.get('full') else 0,
                          saveReportDetail='none',saveVariables=False,readGlobalCookie=False,saveGlobalCookie=False)
        if flow.get('dataset'):
            data_sets=[d for root in source.get('apiTestDataCollection',[]) for d in root.get('items',[])
                       if d.get('name')=='Aether_真实公开数据_两租户_20261010']
            if len(data_sets)!=1:
                raise ValueError('Import and verify the real public dataset before building its journey')
            s['options'].update(useDataSetId=int(data_sets[0]['id']),iterationCount=14)
            s['description'] += ' 使用真实公开数据集14行，逐行选择账号及标准事实；账号密码与P3身份映射须在本地环境登记。此场景是Working API生命周期回归，不代表跨会话或长期语义效果通过。'
        if flow.get('performance'):
            template=next((x['performanceTestOptions'] for x in old.values()
                           if x['name'].startswith('PERF ') and x.get('performanceTestOptions')),None)
            if template:
                # These fields are from a desktop-saved native export, not guessed.
                s['performanceTestOptions']={**copy.deepcopy(template),
                    'environmentId':envs[flow['environment']], 'virtualUsers':1,
                    'durationMins':1,'rampDurationMins':0,'useDataSetId':-1,
                    'dataDistributionProfile':'ordered'}
        if flow['name'] in old: s['id']=old[flow['name']]['id']
        groups[1 if flow.get('performance') else 0]['items'].append(s)
    for root in source['apiTestCaseCollection']:
        for oldgroup in root.get('children',[]):
            for group in groups:
                if oldgroup['name']==group['name']:group['id']=oldgroup['id']
    package['apiTestCaseCollection']=[dict(id=0,name='Root',children=groups,items=[])]
    for root in source['apiTestCaseCollection']:
        for oldgroup in root.get('children',[]):
            prefix=oldgroup['name'][:3]
            if prefix not in ['01_','02_','03_']: continue
            legacy=copy.deepcopy(oldgroup)
            kind={'01_':'public_readonly','02_':'quality_identity','03_':'private_readonly'}[prefix]
            for scene in legacy.get('items',[]):
                scene['options']={**scene.get('options',{}),'environmentId':envs[kind],
                    'iterationCount':1,'threadCount':1,'onError':'ignore','saveReportDetail':'none',
                    'saveVariables':False,'readGlobalCookie':False,'saveGlobalCookie':False}
            package['apiTestCaseCollection'][0]['children'].append(legacy)
    existing_suites={s['name']:s for r in source.get('testSuiteCollection',[]) for s in r.get('items',[])}
    definitions_by_key={f['key']:f for f in definitions()}
    suite_specs=[('Aether_每次部署_公网冒烟','public_readonly',['public-chain']),
                 ('Aether_每次部署_身份与权限','quality_identity',['aetherusera','aetherusera2','aetheruserb','tenant-switch','identity-input-boundaries','identity-forged-claims']),
                 ('Aether_每次部署_内部健康','private_readonly',['internal-chain']),
                 ('Aether_业务验收_完整记忆链路','isolated_acceptance',['memory-lifecycle','memory-correct','memory-real-data']),
                 ('Aether_性能探索_健康基线','private_readonly',['perf-internal-chain']),
                 ('Aether_性能探索_身份旅程','quality_identity',['perf-aetherusera']),
                 ('Aether_性能探索_记忆旅程','isolated_acceptance',['perf-memory-lifecycle'])]
    suites=[]
    # A suite can reference scenarios only after the first native import assigns IDs.
    for name,environment,keys in suite_specs:
        if not all(definitions_by_key[k]['name'] in old for k in keys): continue
        suite=dict(name=name,description='原生多步骤回归入口；运行条件与断言见各场景。性能和完整业务准入未满足时必须BLOCKED。',
                   priority=1,ordering=10,folderId=0,tags=[],
                   items=[dict(id=str(uuid.uuid5(uuid.NAMESPACE_URL,name)),name='场景用例',type='STATIC_TEST_SCENARIO',
                               testScenarios=[dict(id=old[definitions_by_key[k]['name']]['id'],options={}) for k in keys],options={})],
                   options=dict(runMode='serial',runnerId=0,**({'environmentId':envs[environment]} if environment else {})))
        if name in existing_suites: suite['id']=existing_suites[name]['id']
        suites.append(suite)
    package['testSuiteCollection']=[dict(id=0,name='Root',children=[],items=suites)]
    if docs:
        olddocs={d['name']:d for f in source.get('docCollection',[]) for d in f.get('items',[])}
        def document(name,content,module):
            value=dict(name=name,sidebarTitle='',content=content,type='',tags=[],visibility='INHERITED',moduleId=module)
            if name in olddocs:value['id']=olddocs[name]['id']
            return value
        admin=modules['Aether_云端管理API']
        documents=[document(p.stem,p.read_text(encoding='utf-8'),admin) for p in sorted(docs.glob('0[0-6]_*.md'))]
        package['docCollection']=[dict(name='根目录',moduleId=admin,children=[],items=documents)]
        for name,filename,module_name in [('Aether_部署后怎么测试','00_每次部署后的测试入口.md','Aether_测试与验收入口'),
                                          ('Aether_性能运行与验收边界','04_性能与完整工程门禁.md','Aether_性能与容量')]:
            module=modules[module_name]
            package['docCollection'].append(dict(name='根目录',moduleId=module,children=[],items=[document(name,(docs/filename).read_text(encoding='utf-8'),module)]))
    return package


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--docs',type=Path)
    args=parser.parse_args()
    package=build(json.loads(args.source.read_text(encoding='utf-8-sig')),args.docs)
    args.output.write_text(json.dumps(package,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'scenarios':len(definitions()),'native_http_steps':sum(len(x['stages']) for x in definitions())}))
