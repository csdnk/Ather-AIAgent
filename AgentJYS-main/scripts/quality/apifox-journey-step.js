// Embedded in each native HTTP step. cfg and phase are supplied by the generator.
// All business HTTP calls are native steps; never issue hidden HTTP subrequests.
(() => {
  const FULL_STACK_ADMITTED = false;
  const PERFORMANCE_ADMITTED = false;
  const key = 'aether_journey_' + cfg.flow;
  const env = k => pm.environment.get(k);
  let s = JSON.parse(pm.variables.get(key) || '{}');
  const persist = () => pm.variables.set(key, JSON.stringify(s));
  function check(name, value, blocked=false) {
    if (!value) { s.failed = true; persist(); }
    pm.test((blocked && !value?'BLOCKED: ':'') + name, () => pm.expect(Boolean(value)).to.eql(true));
    if (!value) throw new Error((blocked?'BLOCKED: ':'FAIL: ') + name);
  }
  function skip(name) {
    check(name, true); s.done = cfg.index; persist();
    if (!pm.execution || !pm.execution.skipRequest) throw new Error('BLOCKED: skipRequest unavailable');
    pm.execution.skipRequest();
  }
  const scope = r => r && s.home && ['tenant_id','application_id','user_id','agent_id'].every(k=>r[k]===s.home[k]) && r.session_id===s.run && r.task_id==null;
  const memory = r => r && s.memory && scope(r.scope) && r.memory_id===s.memory.memory_id && r.version===s.memory.version;
  const source = (a,b) => a && b && ['source_id','source_version','content_hash','locator'].every(k=>a[k]===b[k]);
  const sources = a => Array.isArray(a) && a.length>0 && a.every(x=>source(x,s.source));
  const operation = name => name + '_' + s.run;
  function receipt(b, kind) {
    if (kind==='recall' || kind==='recall_deleted') {
      const items=(b.groups||[]).flatMap(g=>g.items||[]);
      check('ContextPack 范围与预算正确',scope(b.scope) && Number.isInteger(b.tokens_used) && b.tokens_used>=0 && b.tokens_used<=1024 && b.token_budget===1024);
      if (kind==='recall_deleted') check('删除后召回为空且覆盖完整',b.outcome==='empty' && items.length===0 && b.coverage && b.coverage.working==='complete');
      else check('召回只含当前版本与真实来源',b.outcome==='available' && items.length>0 && items.every(i=>memory(i.memory) && sources(i.sources)) && items.some(i=>i.content.includes(String(s.fact))));
    } else {
      check('保存回执完整且沿用原操作',b.saved===true && b.operation_id===operation(kind==='replay'?'save':kind) && Array.isArray(b.memories) && b.memories.length===1 && scope(b.memories[0].scope));
      check('来源字段完整',b.source && typeof b.source.source_id==='string' && Number.isInteger(b.source.source_version) && /^[a-f0-9]{64}$/i.test(b.source.content_hash||'') && typeof b.source.locator==='string' && b.source.locator.length>0);
      if (kind==='replay') check('重试不产生新版本或来源',memory(b.memories[0]) && source(b.source,s.source));
      if (kind==='correct') check('更正只递增同一对象版本',b.memories[0].memory_id===s.memory.memory_id && b.memories[0].version===s.memory.version+1);
      if (kind!=='replay') { s.memory=b.memories[0]; s.source=b.source; }
      s.cleanup='requires_reconciliation';
    }
    s.completed[kind]=true;
    s.timings[kind+'_complete_ms']=Date.now()-s.started;
  }
  function operationResult(b, code, kind, final) {
    if (code===200 && !b.code) receipt(b,kind);
    else {
      check('异步受理必须带可追踪原任务',code===400 && b.code==='REQUEST_IN_PROGRESS');
      const job=b.job_id || pm.response.headers.get('X-P3-Job-ID');
      check('原任务身份不可改变',/^[A-Za-z0-9_-]{1,128}$/.test(job||'') && (!s.jobs[kind] || s.jobs[kind]===job));
      s.jobs[kind]=job;
      check('业务必须在本轮观察窗口完成',!final);
    }
  }
  function expected(name) { return cfg.dataset ? env(s.account+'_expected_'+name) : cfg[name] || env('expected_'+name); }
  try {
    if (phase==='pre') {
      pm.request.url.update('http://127.0.0.1:1/BLOCKED');
      if (cfg.first) {
        s={run:pm.variables.replaceIn('{{$guid}}'),started:Date.now(),done:-1,requests:0,tokens:{},jobs:{},completed:{},timings:{},failed:false};
        s.fact=17; s.text='Aether synthetic regression '+s.run+'. The retention window is exactly 17 days, not 70 days.';
        if (cfg.dataset) {
          const data={};
          for (const name of ['dataset_id','dataset_kind','tenant_account','text','question','expected_fact','source_url','source_sha256','expected_behavior']) data[name]=pm.variables.get(name);
          check('真实数据必须逐行提供来源、正文、问题与标准事实',data.dataset_kind==='real_public_source' && data.expected_behavior==='save_read_recall_same_source' && ['dataset_id','text','question','expected_fact'].every(k=>typeof data[k]==='string' && data[k].trim().length>0) && /^https:\/\//.test(data.source_url||'') && /^[a-f0-9]{64}$/.test(data.source_sha256||''),true);
          check('真实数据只允许登记的测试账号',['aetherusera','aetheruserb'].includes(data.tenant_account),true);
          s.account=data.tenant_account; s.text=data.text; s.fact=data.expected_fact; s.question=data.question;
          s.dataset={id:data.dataset_id,source_url:data.source_url,source_sha256:data.source_sha256};
        }
        s.input={source:{kind:'conversation',external_id:s.run,external_version:'1',occurred_at:new Date().toISOString()},selection:{session_id:s.run},content:{kind:'text',text:s.text}};
        persist();
      }
      check('请选择本场景的正确环境',env('environment_kind')===cfg.environment,true);
      if (cfg.full) check('G0 完整隔离及供应商费用硬限额尚未接入',FULL_STACK_ADMITTED,true);
      if (cfg.performance) check('性能负载硬限制与独立资源尚未接入',PERFORMANCE_ADMITTED,true);
      check('必须从本场景第一步开始',typeof s.run==='string' && s.run.length>0,true);
      const cleanup=cfg.action==='logout';
      if (!cleanup) check('前序步骤必须成功且按顺序执行',!s.failed && s.done===cfg.index-1,true);
      const base=env(cfg.base);
      check('使用已核验目标',cfg.environment==='public_readonly' ? base===cfg.publicOrigin : /^http:\/\/127\.0\.0\.1:\d+$/.test(base||''),true);
      const limit=Number(env('max_requests'));
      check('请求预算须为 5 至 80 的整数',Number.isInteger(limit) && limit>=5 && limit<=80,true);
      const reserve=Object.keys(s.tokens).filter(slot=>!(s.loggedOut||[]).includes(slot)).length;
      check('请求预算耗尽，保留对象和原任务',s.requests<(cleanup?limit:limit-reserve),true);
      if (!cleanup) check('业务观察超时，保留原任务',Date.now()-s.started<=60000,true);
      const slot=cfg.slot||'A';
      if (cleanup && !s.tokens[slot]) { skip('本步骤未取得令牌，无需注销'); return; }
      let route=cfg.path, body=cfg.payload;
      if (cfg.action==='poll') {
        if (s.completed[cfg.kind]) { skip('原操作已完成，无需重复查询'); return; }
        check('必须查询已记录的原任务',!!s.jobs[cfg.kind],true);
        route=route.replace('{job_id}',s.jobs[cfg.kind]);
      }
      if (route.includes('{memory_id}')) { check('需要本轮真实记忆对象',s.memory && /^[A-Za-z0-9_-]{1,128}$/.test(s.memory.memory_id),true); route=route.replace('{memory_id}',s.memory.memory_id); }
      if (cfg.action==='login') {
        const username=cfg.dataset ? s.account : cfg.account||env('test_username');
        const password=env(cfg.dataset ? s.account+'_password' : cfg.account ? cfg.account+'_password':'test_password');
        check('需要本地测试账号密码',!!username && !!password,true); body={username,password};
      }
      if (cfg.action==='save' || cfg.action==='replay') body=s.input;
      if (['body','deleted'].includes(cfg.action)) body=s.memory;
      if (cfg.action==='correct') {
        check('更正前必须取得实际版本',Number.isInteger(s.revision) && s.revision>0,true);
        s.fact=23; s.text='Aether synthetic regression '+s.run+'. The retention window is exactly 23 days, not 17 days.';
        body={expected_version:s.memory.version,expected_object_revision:s.revision,content:s.text,source:{...s.input.source,external_version:'2'},reason:'isolated regression '+s.run};
      }
      if (['recall','recall_deleted'].includes(cfg.action)) body={query:cfg.dataset ? s.question : 'What is the retention window for '+s.run+'?',selection:{session_id:s.run},sources:'working',token_budget:1024};
      if (cfg.action==='delete') { check('删除使用实际对象修订号',Number.isInteger(s.revision) && s.revision>0,true); body={expected_revision:s.revision,reason:'test cleanup '+s.run}; }
      if (body!==undefined) pm.request.body.update(JSON.stringify(body));
      pm.request.headers.upsert({key:'Content-Type',value:'application/json'});
      pm.request.headers.upsert({key:'X-Test-Run-ID',value:s.run});
      if (cfg.auth) { check('需要本步骤对应身份令牌',!!s.tokens[slot],true); pm.request.headers.upsert({key:'Authorization',value:'Bearer '+s.tokens[slot]}); }
      for (const [key,value] of Object.entries(cfg.headers||{})) pm.request.headers.upsert({key,value});
      if (['save','replay','correct','recall','recall_deleted','delete'].includes(cfg.action)) pm.request.headers.upsert({key:'X-Operation-ID',value:operation(cfg.action==='replay'?'save':cfg.action)});
      s.requests++; persist(); pm.request.url.update(base+route);
      return;
    }
    check('响应属于当前已执行步骤',!!s.run && s.requests>0,true);
    const code=pm.response.code, b=pm.response.json(), action=cfg.action, slot=cfg.slot||'A';
    if (action==='login') {
      // Preserve any issued token for the visible cleanup step, even if identity is wrong.
      if (b.data && typeof b.data.accessToken==='string') s.tokens[slot]=b.data.accessToken;
      check('登录为预期用户且返回令牌',code===200 && b.code===0 && b.data && String(b.data.userId)===String(expected('user_id')) && !!s.tokens[slot]);
    } else if (action==='identity') {
      check('用户和租户身份正确且有效',code===200 && b.code===0 && b.data && String(b.data.user_id)===String(expected('user_id')) && String(b.data.tenant_id)===String(expected('tenant_id')) && b.data.user_enabled===true && b.data.tenant_enabled===true);
      s.roles=s.roles||{};
      if (cfg.assertSameRoles) check('伪造声明不能改变实际角色集合',Array.isArray(b.data.role_codes) && Array.isArray(s.roles[slot]) && JSON.stringify([...b.data.role_codes].sort())===JSON.stringify([...s.roles[slot]].sort()));
      else if (Array.isArray(b.data.role_codes)) s.roles[slot]=b.data.role_codes;
    } else if (action==='p3identity') {
      check('P3 业务身份映射正确',code===200 && b.scope && typeof expected('p3_user_id')==='string' && typeof expected('p3_tenant_id')==='string' && b.scope.user_id===expected('p3_user_id') && b.scope.tenant_id===expected('p3_tenant_id')); s.home=b.scope;
    } else if (action==='denied') check('精确拒绝访问且不泄露数据',code===cfg.httpCode && b.code===cfg.businessCode && b.data==null && !b.accessToken);
    else if (action==='http401') check('匿名 HTTP 401 且不返回令牌',code===401 && !b.accessToken);
    else if (action==='health') check('健康状态符合契约',code===200 && b[cfg.field]===cfg.value);
    else if (['save','replay','correct','recall','recall_deleted'].includes(action)) operationResult(b,code,action,false);
    else if (action==='poll') operationResult(b,code,cfg.kind,cfg.finalPoll);
    else if (action==='body') check('正文、版本、身份与来源一致',code===200 && b.outcome==='read' && b.content===s.text && memory(b.memory) && sources(b.sources));
    else if (action==='snapshot') { check('取得当前实际对象修订',code===200 && memory(b.ref) && Number.isInteger(b.object_revision) && b.object_revision>0); s.revision=b.object_revision; }
    else if (action==='delete') { check('删除原操作已阻断访问',code===200 && b.blocked===true && b.operation_id===operation('delete')); s.cleanup=b.cleanup_state||'unknown'; }
    else if (action==='deleted') check('删除后正文因 deleted 被排除',code===200 && memory(b.memory) && b.outcome==='excluded' && b.reason_code==='deleted' && b.content===null);
    else if (action==='logout') { check('注销获得服务确认',code===200 && b.code===0); s.loggedOut=[...(s.loggedOut||[]),slot]; }
    else throw new Error('unrecognized stage');
    s.done=cfg.index; s.timings['step_'+cfg.index]=pm.response.responseTime;
    if (cfg.last) {
      check('整条场景没有前序失败',!s.failed);
      console.log('AETHER_JOURNEY '+JSON.stringify({run_id:s.run,scenario:cfg.flow,dataset:s.dataset,requests:s.requests,elapsed_ms:Date.now()-s.started,memory_id:s.memory&&s.memory.memory_id,jobs:s.jobs,cleanup:s.cleanup||'none',timings:s.timings}));
      s.tokens={};
    }
    persist();
  } catch(e) {
    s.failed=true; persist();
    console.log('AETHER_JOURNEY_FAILURE '+JSON.stringify({run_id:s.run,scenario:cfg.flow,dataset:s.dataset,account:s.account,step:cfg.index,memory_id:s.memory&&s.memory.memory_id,jobs:s.jobs,cleanup:s.cleanup||'none',requests:s.requests}));
    if (phase==='pre') { pm.request.url.update('http://127.0.0.1:1/BLOCKED'); if(pm.execution && pm.execution.skipRequest) pm.execution.skipRequest(); }
    pm.test('本步骤必须满足业务契约',()=>{throw new Error(e.message);});
    throw e;
  }
})();
