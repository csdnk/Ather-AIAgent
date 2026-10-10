const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const engine=fs.readFileSync(path.join(__dirname,'../../scripts/quality/apifox-business-step.js'),'utf8');
function post(action,body,state={},extra={}) {
 const values=new Map([['business_state',JSON.stringify({run:'r1',memory:{memory_id:'m1',version:1,scope:{tenant_id:'t1'}},...state})]]);
 const failures=[];
 const pm={variables:{get:k=>values.get(k),set:(k,v)=>values.set(k,v)},environment:{get:()=>undefined},
 response:{code:200,json:()=>body,headers:{get:()=>null}},
 test:(name,f)=>{try{f()}catch(e){failures.push(name)}},expect:x=>({to:{eql:y=>assert.deepEqual(x,y)}})};
 vm.runInNewContext(engine,{cfg:{action,...extra},phase:'post',pm,console:{log:()=>{}},setTimeout:()=>{}});
 return {failures,state:JSON.parse(values.get('business_state'))};
}
test('processing completion requires derived artifacts and all task success',()=>{
 const b={memory:{memory_id:'m1'},state:'completed',derived_memory_ids:[],tasks:[]};
 assert.ok(post('processing',b,{}, {final:true}).failures.length);
});
test('last original-job poll rejects still in progress and never invents completion',()=>{
 assert.ok(post('result',{code:'REQUEST_IN_PROGRESS'},{job:'j1'},{final:true,kind:'save'}).failures.length);
});
test('cross-session recall rejects alien tenant and missing expected fact',()=>{
 const b={outcome:'available',scope:{tenant_id:'t1'},token_budget:1024,tokens_used:4,coverage:{long_term:'complete'},groups:[{items:[{memory:{memory_id:'x',scope:{tenant_id:'t2'}},content:'unrelated',sources:[{source_id:'s1'}]}]}]};
 assert.ok(post('recall-result',b,{fact:'1.49 C',source:{source_id:'s1'},derived:['x']},{longterm:true}).failures.length);
});
test('task success with unknown effects is never accepted',()=>{
 assert.ok(post('task',{task_id:'j1',state:'succeeded',effect_status:'unknown',result_ref:{}},{job:'j1'}).failures.length);
});
test('native happy-path polls do not use skipRequest or hidden HTTP calls',()=>{
 assert.equal(engine.includes('pm.execution.skipRequest'),false);
 assert.equal(engine.includes('pm.sendRequest'),false);
});
const emptyPack={recall_id:'j1',outcome:'empty',scope:{tenant_id:'t1'},token_budget:1024,tokens_used:0,rendered_context:'',coverage:{working:'complete'},groups:[]};
test('empty context must not leak through rendered_context',()=>{
 assert.ok(post('recall-result',{...emptyPack,rendered_context:'secret'},{home:{tenant_id:'t1'},job:'j1'},{empty:true}).failures.length);
});
test('recall response must belong to the original durable job',()=>{
 assert.ok(post('recall-result',{...emptyPack,recall_id:'another-job'},{home:{tenant_id:'t1'},job:'j1'},{empty:true}).failures.length);
});
test('honest empty original-job ContextPack passes',()=>{
 assert.deepEqual(post('recall-result',emptyPack,{home:{tenant_id:'t1'},job:'j1'},{empty:true}).failures,[]);
});
test('a hot read proof for another object cannot confirm this action',()=>{
 const b={memory:{memory_id:'m1',version:1},actions:[{state:'succeeded',intent:{action_id:'act1',provider_mode:'real',content_hash:'hash',decision:{target_tier:'hot'}},feedback:{state:'succeeded',observation:{tier:'hot'},read_proof:{readable:true,provider_mode:'real',content_hash:'hash',memory:{memory_id:'wrong'}}}}]};
 assert.ok(post('placement',b,{}, {final:true}).failures.length);
});

test('live capability contract accepts the actual postgresql provider name',()=>{
 assert.deepEqual(post('capabilities',{metadata_storage:'postgresql',semantic_processing:'model',object_storage:'ceph',scheduling:'temporal_v1'}).failures,[]);
});

function liveLogin(overrides={}) {
 const values=new Map(), failures=[];let url='';
 const env={environment_kind:'live_existing_deployment',live_admission:'aether-p3-demo-700417ea',auth_base:'http://127.0.0.1:14890',aetherusera_username:'aetherqaabcdef012345a',aetherusera_password:'test-only',...overrides};
 const pm={environment:{get:k=>env[k]},variables:{get:k=>values.get(k),set:(k,v)=>values.set(k,v),replaceIn:()=> 'run1'},
 request:{url:{update:x=>{url=x}},body:{update:()=>{}},headers:{upsert:()=>{}}},test:(n,f)=>{try{f()}catch(e){failures.push(n)}},expect:x=>({to:{eql:y=>assert.deepEqual(x,y)}})};
 vm.runInNewContext(engine,{cfg:{key:'public-working',first:true,index:0,action:'login',path:'/admin-api/aether/identity/login',base:'auth_base',auth:false,data:{tenant_account:'aetherusera',text:'public',expected_fact:'public'}},phase:'pre',pm,console:{log:()=>{}}});
 return {url,failures};
}
test('live admission accepts the verified backend with a disposable account',()=>{
 assert.deepEqual(liveLogin(),{url:'http://127.0.0.1:14890/admin-api/aether/identity/login',failures:[]});
});
test('live admission rejects an existing business account before sending credentials',()=>{
 const r=liveLogin({aetherusera_username:'aetherusera'});assert.equal(r.url,'http://127.0.0.1:1/BLOCKED');assert.ok(r.failures.length);
});
test('live admission rejects another destination and the former temporary environment',()=>{
 for(const change of [{auth_base:'https://other.example'},{environment_kind:'isolated_acceptance'}]){
  const r=liveLogin(change);assert.equal(r.url,'http://127.0.0.1:1/BLOCKED');assert.ok(r.failures.length);
 }
});

test('failure evidence keeps Recall degradation without tokens or source text',()=>{
 const {safeDiagnostics}=require('../../scripts/quality/run_live_business.cjs');
 const response={json:()=>({recall_id:'r',outcome:'degraded',tokens_used:57,token_budget:1024,degradation_reasons:['rerank_DEPENDENCY_UNAVAILABLE'],rendered_context:'private',accessToken:'secret'})};
 assert.deepEqual(safeDiagnostics(response),{recall_id:'r',outcome:'degraded',tokens_used:57,token_budget:1024,degradation_reasons:['rerank_DEPENDENCY_UNAVAILABLE']});
 assert.deepEqual(safeDiagnostics({json:()=>({data:{accessToken:'secret'}})}),{});
});
