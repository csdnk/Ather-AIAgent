// Compatibility executor of SAVED Apifox component scenes, not a native UI report.
const fs=require('node:fs'), path=require('node:path'), crypto=require('node:crypto');
const {buildCollection}=require('./run_native.cjs');
function* walk(nodes){for(const n of nodes){yield n;yield* walk(n.items||[]);yield* walk(n.children||[]);}}
async function main(){
 const [nativeFile,profile,configFile,out]=process.argv.slice(2);
 if(!['embedding','compression'].includes(profile))throw Error('Unknown bounded component profile');
 const config=JSON.parse(fs.readFileSync(configFile,'utf8'));
 const token=fs.readFileSync(config.token_file,'utf8').trim();
 const native=JSON.parse(fs.readFileSync(nativeFile,'utf8'));
 const candidates=[...walk(native.apiTestCaseCollection)].filter(s=>s.name?.includes(` ${profile} 提交_幂等_轮询_指标报告`));
 if(candidates.length!==1||candidates[0].steps?.length!==44)throw Error('Expected one verified 44-step scene');
 const scene=candidates[0],collection=buildCollection(scene);
 const values={benchmark_base:'http://127.0.0.1:14884',benchmark_token:token,benchmark_assert_release:'false'};
 fs.mkdirSync(out,{recursive:false});
 const newman=require(path.join(process.env.QUALITY_NODE_MODULES,'newman'));
 const result=await new Promise(resolve=>newman.run({collection,environment:{values:Object.entries(values).map(([key,value])=>({key,value,enabled:true}))},
   reporters:[],iterationCount:1,timeout:850000,timeoutRequest:25000,timeoutScript:5000,ignoreRedirects:true,insecure:false},(err,summary)=>{
   const executions=summary?.run?.executions||[];
   const last=executions.find(e=>e.item?.name===collection.item.at(-1).name);
   let job;try{job=JSON.parse(last.response.stream.toString());}catch{}
   const failures=(summary?.run?.failures||[]).map(f=>({test:f.error?.test||f.error?.name,message:f.error?.message,item:f.source?.name}));
   const status=job?.status==='BLOCKED'?'BLOCKED':!err&&job?.status==='PASS'&&!failures.length?'PASS':'FAIL';
   resolve({status,executor:'Newman compatibility; saved Apifox steps',native_apifox_status:'NOT_RUN',scene_id:scene.id,
     run_id:crypto.randomUUID(),job_id:job?.job_id,component_run_id:job?.report?.run_id,
     requests:executions.filter(e=>e.response).length,assertions:executions.reduce((n,e)=>n+(e.assertions||[]).length,0),failures,
     report:job?.report||null,release_gate:'BLOCKED',native_sha256:crypto.createHash('sha256').update(fs.readFileSync(nativeFile)).digest('hex')});
 }));
 fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(result,null,2));
 fs.writeFileSync(path.join(out,'agent-report.md'),`# ${profile}: ${result.status}\n\nNewman兼容执行已保存Apifox场景；不代表Apifox桌面原生报告。\n\n请求 ${result.requests}；断言 ${result.assertions}；job_id ${result.job_id}；计算 run_id ${result.component_run_id||'无'}。\n\n发布门禁 BLOCKED。\n`);
 console.log(JSON.stringify({status:result.status,requests:result.requests,assertions:result.assertions,job_id:result.job_id,component_run_id:result.component_run_id}));
 process.exitCode=result.status==='PASS'?0:2;
}
main().catch(e=>{console.error('Component adapter setup/execution failed: '+e.message);process.exitCode=2;});
