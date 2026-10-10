// Native HTTP steps only. Public data is frozen in cfg.data, never database seeded.
(() => {
  // Existing deployment is explicitly authorized; temporary environment stays closed.
  const live = pm.environment.get('environment_kind')==='live_existing_deployment';
  let s = JSON.parse(pm.variables.get('business_state') || '{}');
  const keep = () => pm.variables.set('business_state', JSON.stringify(s));
  function ok(name, condition) {
    pm.test(name, () => pm.expect(Boolean(condition)).to.eql(true));
    if (!condition) throw new Error(name);
  }
  const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
  const ref = r => r && s.memory && r.memory_id===s.memory.memory_id && r.version===s.memory.version;
  const op = () => cfg.operation + '_' + s.run;
  function saved(b) {
    ok('持久保存回执、原操作与来源完整',b.saved===true && b.operation_id===s.expectedOperation && b.memories?.length===1 && b.source?.content_hash?.match(/^[a-f0-9]{64}$/) && b.source.locator);
    if(s.completedOperation===s.expectedOperation) {ok('重复查询返回同一终态',ref(b.memories[0])&&same(b.source,s.source));return;}
    if (cfg.kind==='replay') ok('重试沿用同一记忆版本与来源',ref(b.memories[0]) && same(b.source,s.source));
    else if(cfg.kind==='correct') ok('更正沿用对象且版本递增',b.memories[0].memory_id===s.memory.memory_id && b.memories[0].version===s.memory.version+1);
    ok('实际记忆属于本轮用户租户会话',b.memories[0].scope.tenant_id===s.home.tenant_id && b.memories[0].scope.user_id===s.home.user_id && b.memories[0].scope.session_id===s.run);
    s.memory=b.memories[0];s.source=b.source;s.cleanup='required';s.completedOperation=s.expectedOperation;
  }
  function recall(b) {
    const items=(b.groups||[]).flatMap(g=>g.items||[]);
    const budget=cfg.budget||1024;
    ok('召回结果绑定原持久任务',b.recall_id===s.job);
    ok('ContextPack 完整且预算有效',b.scope?.tenant_id===s.home?.tenant_id && Number.isInteger(b.tokens_used) && b.tokens_used>=0 && b.tokens_used<=budget && b.token_budget===budget && b.outcome!=='degraded');
    const layer=cfg.longterm?'long_term':'working';
    ok('实际请求层覆盖完整',b.coverage?.[layer]==='complete');
    if(cfg.empty) ok('指定空范围/失效内容不再返回',b.outcome==='empty' && items.length===0 && b.rendered_context==='' && b.tokens_used===0);
    else if(cfg.excludedSource) ok('撤销来源及其派生内容均不再返回',items.every(i=>!i.sources.some(x=>x.source_id===s.source.source_id)) && !b.rendered_context.includes(s.fact));
    else if(!cfg.budget) {
      ok('返回正确事实、实际来源与当前有效版本',b.outcome==='available' && items.length>0 && items.some(i=>i.content.includes(s.fact) && i.sources.some(x=>x.source_id===s.source.source_id)) && items.every(i=>i.memory.scope.tenant_id===s.home.tenant_id && i.sources?.length && (cfg.longterm?s.derived.includes(i.memory.memory_id):ref(i.memory))));
    }
    const stable=JSON.stringify([b.recall_id,b.scope,b.outcome,b.coverage,b.groups,b.rendered_context,b.tokens_used,b.token_budget]);
    if(s.packJob===s.job)ok('同一原任务结果保持一致',s.pack===stable);
    s.packJob=s.job;s.pack=stable;s.recallId=b.recall_id;
  }
  try {
    if (phase==='pre') {
      pm.request.url.update('http://127.0.0.1:1/BLOCKED');
      if(cfg.first) {s={run:pm.variables.replaceIn('{{$guid}}'),started:Date.now(),tokens:{},requests:0,failed:false,done:-1,derived:[],data:cfg.data,fact:cfg.data.expected_fact,text:cfg.data.text};if(cfg.key==='correction')s.text=s.text.replace(s.fact,'999999 (deliberate incorrect fixture)');keep();}
      ok('BLOCKED: 必须选择已核验现有云端部署',live && pm.environment.get('live_admission')==='aether-p3-demo-700417ea');
      ok('BLOCKED: 先验证首条保存召回与清理闭环',cfg.key==='public-working');
      ok('BLOCKED: 前置步骤和执行顺序完整',cfg.action==='logout'||(!s.failed && s.done===cfg.index-1));
      ok('BLOCKED: 单场景最多180请求/10分钟',s.requests<180 && Date.now()-s.started<600000);
      let route=cfg.path,body=cfg.payload;
      const slot=cfg.slot||'A';
      const logical=slot==='D'?'diagnostic':slot==='B'?'aetheruserb':s.data.tenant_account;
      const account=pm.environment.get(logical+'_username');
      const base=pm.environment.get(cfg.base||'p3_base');
      ok('BLOCKED: 目标必须为已核验现有服务隧道',base===(cfg.base==='auth_base'?'http://127.0.0.1:14890':'http://127.0.0.1:14891'));
      ok('BLOCKED: 仅允许本轮专用业务账号',typeof account==='string' && /^aetherqa[a-f0-9]{12}[ab]$/.test(account));
      if(cfg.action==='login') {const password=pm.environment.get(logical+'_password');ok('BLOCKED: 本地账号密码已配置',password);body={username:account,password};}
      if(cfg.action==='save'||cfg.action==='replay') {
        s.input=s.input||{source:{kind:'text',external_id:s.run,external_version:'1',occurred_at:new Date().toISOString()},selection:{session_id:s.run},content:{kind:'text',text:s.text},trigger:'remember',importance_category:'explicit_constraint'};
        body=s.input;s.expectedOperation='save_'+s.run;
      }
      if(cfg.action==='correct') {
        // Deliberately corrupted input is labelled in the scenario; correction restores real source.
        s.text=s.data.text;s.fact=s.data.expected_fact;
        body={expected_version:s.memory.version,expected_object_revision:s.revision,content:s.text,source:{...s.input.source,external_version:'2'},reason:'restore frozen public source '+s.run};
        s.expectedOperation='correct_'+s.run;
      }
      if(cfg.action==='seed-error') {s.text=s.data.text.replace(s.fact,'999999 (deliberate incorrect fixture)');body={...s.input,content:{kind:'text',text:s.text}};}
      if(['body','excluded','cross-body'].includes(cfg.action)) body=s.memory;
      if(cfg.action==='source-body') body=s.source;
      if(cfg.action==='recall') {body={query:s.data.question,selection:cfg.longterm?{}:{session_id:cfg.emptySession?s.run+'_new':s.run},sources:cfg.longterm?'long_term':'working',token_budget:cfg.budget||1024};}
      if(cfg.action==='lookup') {route=route.replace('{operation_id}',s.expectedOperation);}
      if(cfg.action==='lifecycle') body={expected_version:s.memory.version,expected_object_revision:s.revision,target:cfg.target,reason:'business regression '+s.run};
      if(cfg.action==='retention') body={expected_version:s.memory.version,expected_object_revision:s.revision,enabled:true,completed:true,legal_hold:true,archive_after_idle_hours:24,reason:'completed research legal hold workflow '+s.run};
      if(cfg.action==='delete') body={expected_revision:s.revision,reason:'owned test cleanup '+s.run};
      if(cfg.action==='revoke') body={expected_revision:s.sourceRevision,reason:'withdraw public-material fixture '+s.run};
      if(cfg.action==='consolidate') body={session_id:s.run};
      for(const [key,value] of Object.entries({memory_id:s.memory?.memory_id,job_id:s.job,source_id:s.source?.source_id,recall_id:s.recallId,run:s.run})) if(route.includes('{'+key+'}')) {ok('BLOCKED: 原对象标识存在 '+key,typeof value==='string' && /^[A-Za-z0-9_-]{1,128}$/.test(value));route=route.replace('{'+key+'}',value);}
      if(body!==undefined) pm.request.body.update(JSON.stringify(body));
      pm.request.headers.upsert({key:'Content-Type',value:'application/json'});
      pm.request.headers.upsert({key:'X-Test-Run-ID',value:s.run});
      if(cfg.auth!==false) {ok('BLOCKED: 已通过正式登录',s.tokens[slot]);pm.request.headers.upsert({key:'Authorization',value:'Bearer '+s.tokens[slot]});}
      if(cfg.operation) pm.request.headers.upsert({key:'X-Operation-ID',value:op()});
      if(cfg.action==='recall') s.expectedOperation=op();
      s.requests++;keep();pm.request.url.update(base+route);return;
    }
    const b=pm.response.json(),code=pm.response.code,a=cfg.action;
    if(a==='login') {if(b.data?.accessToken)s.tokens[cfg.slot||'A']=b.data.accessToken;ok('正式账号登录成功',code===200&&b.code===0&&b.data?.accessToken);}
    else if(a==='identity') {ok('P3身份映射与本地登记一致',code===200&&b.scope?.tenant_id===pm.environment.get(s.data.tenant_account+'_expected_p3_tenant_id')&&b.scope?.user_id===pm.environment.get(s.data.tenant_account+'_expected_p3_user_id'));s.home=b.scope;}
    else if(a==='capabilities') ok('真实模型、持久调度和正式存储模式',code===200&&b.semantic_processing==='model'&&b.scheduling==='temporal_v1'&&b.metadata_storage==='postgresql'&&b.object_storage==='ceph');
    else if(['save','replay','correct','recall'].includes(a)) {
      ok('请求返回结果或可追踪的异步受理',code===200 || (code===400&&b.code==='REQUEST_IN_PROGRESS'&&pm.response.headers.get('X-P3-Job-ID')));
      // All paths use original-operation discovery next, including synchronous results.
    }
    else if(a==='lookup') {ok('按原操作ID找到持久任务与输入哈希',code===200&&b.state==='found'&&b.operation_id===s.expectedOperation&&b.job_id&&b.workflow_id&&b.input_hash);if(cfg.sameJob)ok('幂等重试不创建新任务',s.job===b.job_id);s.job=b.job_id;}
    else if(a==='result') {
      if(code===200&&!b.code) {if(cfg.kind==='recall')recall(b);else saved(b);}
      else {ok('原任务仍在执行而非失效失败',code===400&&b.code==='REQUEST_IN_PROGRESS');ok('观察窗口内原任务必须完成',!cfg.final);setTimeout(()=>{},3000);}
    }
    else if(a==='recall-result') recall(b);
    else if(a==='body') ok('完整正文与冻结材料逐字一致',code===200&&b.outcome==='read'&&b.content===s.text);
    else if(a==='source-body') ok('来源全文与冻结材料逐字一致',code===200&&b.is_complete===true&&b.content===s.text&&same(b.source,s.source)&&b.source_hash===s.source.content_hash);
    else if(a==='snapshot') {ok('取得真实对象版本/修订',code===200&&ref(b.ref)&&Number.isInteger(b.object_revision));s.revision=b.object_revision;}
    else if(a==='processing') {
      ok('加工状态属于原对象且未失败',code===200&&b.memory?.memory_id===s.memory.memory_id&&!['failed','completed_with_compression_failure','awaiting_extraction_provider'].includes(b.state));
      if(cfg.final)ok('长期加工完成、真实派生对象存在且任务成功',b.state==='completed'&&b.derived_memory_ids?.length>0&&b.tasks?.length>0&&b.tasks.every(t=>t.state==='succeeded'||t.state==='cancelled'));
      s.derived=b.derived_memory_ids||[];
      if(b.state!=='completed')setTimeout(()=>{},3000);
    }
    else if(a==='task') ok('原任务唯一成功且业务副作用已确认',code===200&&b.task_id===s.job&&b.state==='succeeded'&&b.effect_status==='confirmed'&&b.result_ref);
    else if(a==='placement') {
      ok('调度证据绑定原记忆',code===200&&ref(b.memory)&&Array.isArray(b.actions));
      const actions=b.actions.filter(x=>x.intent?.decision?.target_tier==='hot');
      const succeeded=actions.filter(x=>{
        const i=x.intent,f=x.feedback,p=f?.read_proof,o=f?.observation;
        return x.state==='succeeded'&&i.provider_mode==='real'&&f?.state==='succeeded'&&p?.provider_mode==='real'&&p.readable===true&&o?.readable===true&&o.tier==='hot'&&
          same(i.decision.memory,s.memory)&&same(p.memory,s.memory)&&same(o.memory,s.memory)&&
          typeof i.action_id==='string'&&f.action_id===i.action_id&&p.action_id===i.action_id&&
          typeof i.provider_instance_id==='string'&&[f.provider_instance_id,p.provider_instance_id,o.provider_instance_id].every(v=>v===i.provider_instance_id)&&
          o.representation_id===i.representation_id&&typeof i.representation_id==='string'&&Number.isInteger(o.epoch)&&o.epoch>=i.expected_epoch&&
          p.content_hash===i.content_hash&&o.content_hash===i.content_hash&&/^[a-f0-9]{64}$/.test(i.content_hash||'');
      });
      ok('无重复调度动作ID',new Set(b.actions.map(x=>x.intent?.action_id)).size===b.actions.length);
      if(cfg.final)ok('真实升温动作成功且存在热层回读证据',succeeded.length>0);
      if(!succeeded.length)setTimeout(()=>{},3000);
    }
    else if(a==='consolidate') ok('合并任务有可追踪ID',code===200&&Array.isArray(b.task_ids));
    else if(a==='lifecycle') {ok('生命周期状态与原对象一致',code===200&&ref(b.ref)&&b.status===cfg.target);s.revision=b.object_revision;}
    else if(a==='retention') ok('法律保留配置已写入',code===200&&b.legal_hold===true);
    else if(a==='retention-read') ok('法律保留配置可回读',code===200&&b.policy?.legal_hold===true);
    else if(a==='source-meta') {ok('读取来源有效性及实际修订',code===200&&b.source_id===s.source.source_id&&b.valid===true&&Number.isInteger(b.revision));s.sourceRevision=b.revision;}
    else if(a==='revoke'||a==='delete') {ok('删除/撤销确认阻断且沿用操作',code===200&&b.blocked===true&&b.operation_id===cfg.operation+'_'+s.run);s.cleanup=b.cleanup_state;}
    else if(a==='excluded') ok('正文被正确排除且无泄露',code===200&&b.outcome==='excluded'&&b.content===null&&b.reason_code===cfg.reason);
    else if(a==='cross-body'||a==='denied') ok('精确拒绝越权且不返回正文',code===403&&b.code==='FORBIDDEN'&&b.content==null);
    else if(a==='invalidated') ok('历史召回结果撤销后无法重取',code===410&&b.code==='RESULT_INVALIDATED');
    else if(a==='logout') ok('本轮身份已注销',code===200&&b.code===0);
    else throw new Error('unrecognized business action '+a);
    s.done=cfg.index;keep();
    if(cfg.last)console.log('AETHER_BUSINESS '+JSON.stringify({run_id:s.run,scenario:cfg.key,status:s.failed?'FAIL':'PASS',dataset_id:s.data.dataset_id,source_sha256:s.data.source_sha256,memory_id:s.memory?.memory_id,original_job:s.job,cleanup:s.cleanup,requests:s.requests}));
  } catch(e) {
    s.failed=true;keep();
    if(phase==='pre')pm.request.url.update('http://127.0.0.1:1/BLOCKED');
    pm.test('业务场景 '+e.message,()=>{throw new Error(e.message);});
    console.log('AETHER_BUSINESS_FAILURE '+JSON.stringify({run_id:s.run,scenario:cfg.key,step:cfg.index,reason:e.message,memory_id:s.memory?.memory_id,job_id:s.job,cleanup:s.cleanup||'none'}));
    // URL remains a closed loopback port on any precondition failure, even in web runners.
  }
})();
