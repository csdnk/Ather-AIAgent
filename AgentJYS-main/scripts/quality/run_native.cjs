// Execute the saved native HTTP steps through Newman for a second execution
// channel. This is explicitly a compatibility run, not an Apifox desktop report.
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const ORIGIN='https://aether-p3-demo-c50c3827.southeastasia.cloudapp.azure.com';
const SUITES={public:'Aether_每次部署_公网冒烟',identity:'Aether_每次部署_身份与权限',internal:'Aether_每次部署_内部健康'};
const KINDS={public:'public_readonly',identity:'quality_identity',internal:'private_readonly'};
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');

function scripts(processors){
 return (processors||[]).filter(p=>p.enable!==false).map(p=>{
  if(p.type!=='customScript'||typeof p.data!=='string')throw Error('BLOCKED: unsupported native processor');
  return p.data;
 }).join('\n');
}

function buildCollection(scene){
 if(!scene.steps?.length)throw Error('BLOCKED: native scenario has no steps');
 if((scene.preProcessors||[]).length||(scene.postProcessors||[]).length)throw Error('BLOCKED: scenario-level processors need an adapter');
 const item=scene.steps.map((step,index)=>{
  if(step.type!=='http'||step.disable||!step.httpApiCase)throw Error('BLOCKED: unsupported or disabled native step');
  const c=step.httpApiCase;
  if(c.auth?.type && c.auth.type!=='noauth')throw Error('BLOCKED: native auth component needs an adapter');
  if((c.parameters?.query||[]).length||(c.parameters?.cookie||[]).length)throw Error('BLOCKED: native query/cookie parameters need an adapter');
  if(!['none','application/json'].includes(c.requestBody?.type||'none'))throw Error('BLOCKED: unsupported native body type');
  const pre=scripts(c.preProcessors),post=scripts(c.postProcessors);
  if(!pre||!post)throw Error('BLOCKED: missing native guard or assertions');
  return {name:`${scene.id}/${index+1} ${step.name}`,
   request:{method:c.method.toUpperCase(),url:c.path,auth:{type:'noauth'},
    header:(c.parameters?.header||[]).filter(h=>h.enable!==false).map(h=>({key:h.name||h.key,value:h.value||''})),
    ...(c.requestBody?.type==='application/json'?{body:{mode:'raw',raw:c.requestBody.data||'{}',options:{raw:{language:'json'}}}}:{})},
   event:[{listen:'prerequest',script:{type:'text/javascript',exec:[pre]}},{listen:'test',script:{type:'text/javascript',exec:[post]}}]};
 });
 return {info:{name:scene.name,schema:'https://schema.getpostman.com/json/collection/v2.1.0/collection.json'},item};
}

function summarize(expected,summary){
 const executions=[...new Set(summary?.run?.executions||[])];
 const steps=expected.map(name=>{
  const matches=executions.filter(e=>e.item?.name===name),e=matches[0],a=e?.assertions||[];
  const failed=a.filter(x=>x.error);
  const admission=failed.some(x=>(x.assertion||'').startsWith('BLOCKED:')||(x.error?.message||'').startsWith('BLOCKED:'));
  // A missing response is a channel/runtime block, not proof of a product defect.
  const status=matches.length!==1||!e?.response||!a.length||a.some(x=>x.skipped)?'BLOCKED':
   failed.length?(admission?'BLOCKED':'FAIL'):'PASS';
  return {name,status,http_status:e?.response?.code||null,http_ms:e?.response?.responseTime??null,
   assertions:a.length,failed_assertions:failed.map(x=>x.assertion)};
 });
 const unexpected=executions.some(e=>!expected.includes(e.item?.name));
 const runErrors=summary?.run?.failures?.length||0;
 let status=steps.some(s=>s.status==='FAIL')?'FAIL':!steps.length||steps.some(s=>s.status==='BLOCKED')||unexpected?'BLOCKED':'PASS';
 if(status==='PASS'&&runErrors)status='BLOCKED';
 return {status,steps,run_errors:runErrors,unexpected_execution:unexpected,
  requests:steps.filter(s=>s.http_status!==null).length,assertions:steps.reduce((n,s)=>n+s.assertions,0)};
}

function preflight(profile,v){
 const issues=[];
 if(!KINDS[profile])return ['本执行器仅开放公网冒烟、内部只读和隔离身份；完整业务/性能尚未准入'];
 if(v.environment_kind!==KINDS[profile])issues.push('环境类型与套件不匹配');
 const budget=Number(v.max_requests);
 if(!Number.isInteger(budget)||budget<5||budget>80)issues.push('每场景请求上限须为5至80');
 if(profile==='public'&&v.public_origin!==ORIGIN)issues.push('公网地址不是已核验演示域名');
 if(profile==='internal'&&v.p3_base!=='http://127.0.0.1:14881')issues.push('内部P3必须使用已配置本机转发14881');
 if(profile==='identity'){
  if(v.base_url!=='http://127.0.0.1:14880'||v.quality_namespace!=='aether-quality-20261009')issues.push('隔离身份目标或环境归属不匹配');
  for(const account of ['aetherusera','aetherusera2','aetheruserb'])if(!v[account+'_password'])issues.push(account+'缺少密码本地值');
 }
 return issues;
}

async function runOne(scene,values,newman){
 const collection=buildCollection(scene);
 return new Promise(resolve=>newman.run({collection,
  environment:{values:Object.entries(values).map(([key,value])=>({key,value,enabled:true}))},
  reporters:[],iterationCount:1,timeout:90000,timeoutRequest:10000,timeoutScript:10000,
  ignoreRedirects:true,insecure:false},(err,summary)=>{
   const result=summarize(collection.item.map(i=>i.name),summary);
   if(err&&result.status==='PASS')result.status='BLOCKED';
   resolve({...result,scenario_id:scene.id,scenario_name:scene.name});
  }));
}

function flatten(groups){return (groups||[]).flatMap(g=>[...(g.items||[]),...flatten(g.children)]);}

async function execute(native,profile,values,newman){
 const missing=preflight(profile,values);
 if(missing.length)return {status:'BLOCKED',preflight_issues:missing,scenarios:[],requests:0};
 const suite=flatten(native.testSuiteCollection).find(s=>s.name===SUITES[profile]);
 if(!suite)throw Error('BLOCKED: native suite is missing');
 const scenes=flatten(native.apiTestCaseCollection);
 const refs=suite.items.flatMap(i=>{
  if(i.type!=='STATIC_TEST_SCENARIO')throw Error('BLOCKED: unsupported suite item');
  return i.testScenarios;
 });
 if(!refs.length||new Set(refs.map(r=>r.id)).size!==refs.length)throw Error('BLOCKED: empty or duplicate suite references');
 const selected=refs.map(r=>{const s=scenes.find(s=>s.id===r.id);if(!s)throw Error('BLOCKED: unresolved native scenario');return s;});
 selected.forEach(buildCollection); // Validate every step before the first request.
 const scenarios=[];
 let interrupted=false;
 for(const s of selected){
  if(interrupted){
   scenarios.push({...summarize(buildCollection(s).item.map(i=>i.name)),scenario_id:s.id,scenario_name:s.name,
    request_count_complete:true,execution_note:'NOT_STARTED_AFTER_RUNNER_ERROR'});
   continue;
  }
  try{scenarios.push(await runOne(s,values,newman));}
  catch{
   scenarios.push({...summarize(buildCollection(s).item.map(i=>i.name)),scenario_id:s.id,scenario_name:s.name,
    request_count_complete:false,execution_note:'RUNNER_INTERRUPTED_PARTIAL_REQUEST_COUNT_UNKNOWN'});
   interrupted=true;
  }
 }
 const status=scenarios.some(s=>s.status==='FAIL')?'FAIL':scenarios.some(s=>s.status==='BLOCKED')?'BLOCKED':'PASS';
 return {status,preflight_issues:[],scenarios,requests:scenarios.reduce((n,s)=>n+s.requests,0),
  request_count_complete:!interrupted,
  assertions:scenarios.reduce((n,s)=>n+s.assertions,0)};
}

async function main(){
 const [nativePath,profile,output]=process.argv.slice(2);
 if(!nativePath||!profile||!output)throw Error('Usage: node run_native.cjs NATIVE_JSON public|identity|internal OUTPUT; environment values JSON on stdin');
 fs.mkdirSync(output,{recursive:false});
 let input='';for await(const part of process.stdin)input+=part;
 const values=JSON.parse(input);input='';
 const bytes=fs.readFileSync(nativePath),native=JSON.parse(bytes);
 const started=new Date().toISOString();
 let result;
 try{result=await execute(native,profile,values,require(path.join(process.env.QUALITY_NODE_MODULES||'','newman')));}
 catch(e){result={status:'BLOCKED',preflight_issues:[String(e.message).startsWith('BLOCKED:')?e.message:'执行器初始化或运行异常，未记录敏感详情'],scenarios:[],requests:0};}
 Object.assign(result,{run_id:crypto.randomUUID(),started_at:started,completed_at:new Date().toISOString(),profile,
  executor:'Newman compatibility execution of exported native HTTP steps',native_apifox_status:'NOT_RUN',
  candidate_sha:values.candidate_sha||null,image_digest:values.image_digest||null,
  release_gate:'BLOCKED',native_export_sha256:hash(bytes),runner_sha256:hash(fs.readFileSync(__filename))});
 fs.writeFileSync(path.join(output,'summary.json'),JSON.stringify(result,null,2)+'\n',{flag:'wx'});
 const lines=[`# ${profile}: ${result.status}`,'',`Run: ${result.run_id}`,
  '执行器：Newman兼容执行；直接复用原生步骤脚本，不代表Apifox桌面版复测。',
  `已确认收到响应的请求 ${result.requests}；计数${result.request_count_complete===false?'不完整，异常场景可能有在途请求':'见逐步记录'}；断言 ${result.assertions||0}；发布门禁 BLOCKED。`,
  ...result.preflight_issues.map(x=>`- BLOCKED: ${x}`),
  ...result.scenarios.flatMap(s=>[`\n## ${s.status} ${s.scenario_name}`,...s.steps.map(t=>`- ${t.status} | ${t.name} | HTTP ${t.http_status??'未收到响应'} | assertions=${t.assertions} | ${t.failed_assertions.join('; ')}`)])];
 fs.writeFileSync(path.join(output,'agent-report.md'),lines.join('\n')+'\n',{flag:'wx'});
 console.log(JSON.stringify({status:result.status,requests:result.requests,assertions:result.assertions||0,run_id:result.run_id}));
 process.exitCode=result.status==='PASS'?0:2;
}
module.exports={buildCollection,summarize,preflight,runOne,execute};
if(require.main===module)main().catch(()=>{console.error('BLOCKED: setup failed; no credentials or raw responses logged');process.exitCode=2;});
