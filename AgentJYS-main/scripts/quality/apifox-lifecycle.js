// Native Apifox post-response script. No credential/body logging.
const env = name => pm.environment.get(name);
const check = (name, value) => {
  pm.test(name, () => pm.expect(Boolean(value)).to.eql(true));
  if (!value) throw new Error(name);
};
const run = pm.variables.get('run_id');
let count = 1;
let token;
let memory;
let home;
const limit = Math.min(80, Number(env('max_requests')));
const started = Date.now();
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function request(base, path, method = 'GET', body, operation, cleanup=false) {
  // One request is reserved for logout; it remains available after business timeout.
  if (count >= limit-(cleanup?0:1) || (!cleanup && Date.now() - started > 25000)) throw new Error('BLOCKED: request/time budget exhausted; preserve original IDs');
  count++;
  const header = {'Content-Type':'application/json', Authorization:'Bearer '+token, 'X-Test-Run-ID':run};
  if (operation) header['X-Operation-ID']=operation;
  return new Promise((resolve,reject) => pm.sendRequest({url:base+path, method, header,
    ...(body===undefined?{}:{body:{mode:'raw',raw:JSON.stringify(body)}})},(error,response)=>{
      if(error) reject(new Error('transport failure; preserve original operation ID'));
      else resolve(response);
    }));
}
async function result(response, operation) {
  const attempts=Math.min(20,Number(env('max_poll_attempts'))||20);
  let job;
  for(let i=0;i<=attempts;i++) {
    const body=response.json();
    if(response.code===200 && !body.code) return body;
    if(body.code!=='REQUEST_IN_PROGRESS') throw new Error('operation failed: '+String(body.code||response.code));
    const nextJob=body.job_id || response.headers.get('X-P3-Job-ID');
    if(!/^[A-Za-z0-9_-]{1,128}$/.test(nextJob||'')) throw new Error('pending result lacks valid original job ID');
    if(job && nextJob!==job) throw new Error('original job ID changed');
    job=nextJob;
    pm.variables.set('original_job_id',job);
    if(i===attempts) break;
    await sleep(250);
    response=await request(env('p3_base'),'/p3/operations/'+job+'/result');
  }
  throw new Error('FAIL: original operation '+operation+' incomplete within observation bound');
}
function sameScope(scope) {
  return scope && ['tenant_id','application_id','user_id','agent_id'].every(k=>scope[k]===home[k]) && scope.session_id===run && scope.task_id==null;
}
function sameMemory(ref) {
  return ref && sameScope(ref.scope) && ref.memory_id===memory.memory_id && ref.version===memory.version;
}
function sameSource(a,b) {
  return a && b && ['source_id','source_version','content_hash','locator'].every(k=>a[k]===b[k]);
}
async function journey() {
  const login=pm.response.json();
  check('正式登录获得预期用户',pm.response.code===200 && login.code===0 && String(login.data.userId)===String(env('expected_user_id')));
  token=login.data.accessToken;
  check('获得非空凭据',typeof token==='string' && token.length>0);
  const self=await request(env('auth_base'),'/admin-api/aether/identity/self');
  const identity=self.json();
  check('身份与测试租户一致',self.code===200 && identity.code===0 && String(identity.data.tenant_id)===String(env('expected_tenant_id')) && String(identity.data.user_id)===String(env('expected_user_id')) && identity.data.user_enabled===true && identity.data.tenant_enabled===true);
  const p3self=await request(env('p3_base'),'/p3/auth/me');
  const p3identity=p3self.json(); home=p3identity.scope;
  check('P3映射到预期业务身份',p3self.code===200 && home && home.user_id===env('expected_p3_user_id') && home.tenant_id===env('expected_p3_tenant_id'));
  const text='Aether synthetic regression '+run+'. The retention window is exactly 17 days, not 70 days.';
  const source={kind:'conversation',external_id:run,external_version:'1',occurred_at:new Date().toISOString()};
  const input={source,selection:{session_id:run},content:{kind:'text',text}};
  const op='save_'+run;
  pm.variables.set('original_operation_id',op);
  const receipt=await result(await request(env('p3_base'),'/p3/remember','POST',input,op),op);
  check('保存完成回执',receipt.saved===true && receipt.operation_id===op && receipt.memories.length===1);
  memory=receipt.memories[0]; pm.variables.set('cleanup_memory_ref',JSON.stringify(memory));
  check('新记忆归属当前身份与本次会话',sameScope(memory.scope));
  const body=await request(env('p3_base'),'/p3/remember/body','POST',memory);
  check('权威正文完整且引用来源一致',body.code===200 && body.json().outcome==='read' && body.json().content===text && sameMemory(body.json().memory) && body.json().sources.length>0 && body.json().sources.every(s=>sameSource(s,receipt.source)));
  const repeated=await result(await request(env('p3_base'),'/p3/remember','POST',input,op),op);
  check('同操作ID重试不产生新记忆',repeated.operation_id===op && repeated.memories.length===1 && sameMemory(repeated.memories[0]) && sameSource(repeated.source,receipt.source));
  const recallOp='recall_'+run;
  const pack=await result(await request(env('p3_base'),'/p3/recall','POST',{
    query:'What is the retention window for '+run+'?',selection:{session_id:run},sources:'working',token_budget:1024
  },recallOp),recallOp);
  const items=(pack.groups||[]).flatMap(g=>g.items||[]);
  const found=items.find(i=>i.memory && i.memory.memory_id===memory.memory_id);
  check('Recall包含正确版本与来源',pack.outcome==='available' && sameScope(pack.scope) && found && items.every(i=>sameMemory(i.memory) && Array.isArray(i.sources) && i.sources.length>0 && i.sources.every(s=>sameSource(s,receipt.source))) && found.content.includes('17'));
  check('Recall预算有效',pack.tokens_used<=1024 && pack.token_budget===1024);
  const snapshot=await request(env('p3_base'),'/p3/remember/'+memory.memory_id);
  check('删除前取得实际对象版本',snapshot.code===200 && sameMemory(snapshot.json().ref) && Number.isInteger(snapshot.json().object_revision));
  const deletion=await request(env('p3_base'),'/p3/remember/'+memory.memory_id+'/delete','POST',{
    expected_revision:snapshot.json().object_revision,reason:'test cleanup '+run
  },'delete_'+run);
  check('删除返回禁止访问回执',deletion.code===200 && deletion.json().blocked===true && deletion.json().operation_id==='delete_'+run);
  const deleted=await request(env('p3_base'),'/p3/remember/body','POST',memory);
  const denied=deleted.json();
  check('删除后因删除状态排除正文',deleted.code===200 && sameMemory(denied.memory) && denied.outcome==='excluded' && denied.reason_code==='deleted' && denied.content===null);
  // Physical cleanup is a separate asynchronous assertion. Keep the object handle.
  pm.variables.set('cleanup_state',deletion.json().cleanup_state);
}
journey().catch(error=>pm.test('记忆旅程必须完整完成',()=>{throw new Error(error.message);})).finally(async()=>{
  if(token) {
    try {
      const logout=await request(env('auth_base'),'/admin-api/system/auth/logout','POST',undefined,undefined,true);
      check('清理登录令牌',logout.code===200 && logout.json().code===0);
    } catch(error) { pm.test('令牌清理失败',()=>{throw new Error(error.message);}); }
  }
  console.log('AETHER_EVIDENCE '+JSON.stringify({run_id:run,operation_id:pm.variables.get('original_operation_id'),job_id:pm.variables.get('original_job_id'),memory_id:memory && memory.memory_id,cleanup_state:pm.variables.get('cleanup_state')||'requires_reconciliation',requests:count}));
});
