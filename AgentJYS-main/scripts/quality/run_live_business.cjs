// Compatibility execution of actual native steps. This does not claim a native
// Apifox report; the same exported scripts are imported into the project.
const fs=require('node:fs'),path=require('node:path');
const {buildCollection,summarize}=require('./run_native.cjs');
function flat(groups){return groups.flatMap(g=>[...(g.items||[]),...flat(g.children||[])]);}
function safeDiagnostics(response){
 try {
  const b=response.json();if(!b.recall_id)return {};
  return Object.fromEntries(['recall_id','outcome','tokens_used','token_budget','degradation_reasons'].filter(k=>k in b).map(k=>[k,b[k]]));
 } catch {return {};}
}
async function main(){
 const input=JSON.parse(fs.readFileSync(0,'utf8'));
 const native=JSON.parse(fs.readFileSync(input.native,'utf8'));
 const scene=flat(native.apiTestCaseCollection).find(s=>s.id===input.scene_id);
 if(!scene)throw Error('missing scene');
 const collection=buildCollection(scene);
 const checkpointFile=path.join(input.output,'checkpoint.json');
 let checkpoint={owned_memories:[],owned_sources:[],source_bindings:[],cleanup_tasks:[]};
 const capture=`\n{const q=JSON.parse(pm.variables.get('business_state')||'{}');let cleanupTasks=[];if(phase==='post'&&['delete','revoke'].includes(cfg.action)){try{cleanupTasks=pm.response.json().task_ids||[];}catch(e){}}console.log('OWNED_CHECKPOINT '+JSON.stringify({run:q.run,memory:q.memory,source:q.source,job:q.job,expectedOperation:q.expectedOperation,submitted:q.requests>3,home:q.home,cleanupTasks}));}`;
 for(const item of collection.item)for(const e of item.event)e.script.exec.push(capture);
 const newman=require(path.join(process.env.QUALITY_NODE_MODULES,'newman'));
 await new Promise(resolve=>{
  const runner=newman.run({collection,environment:{values:Object.entries(input.environment).map(([key,value])=>({key,value,enabled:true}))},reporters:[],iterationCount:1,bail:true,timeout:600000,timeoutRequest:35000,timeoutScript:10000,ignoreRedirects:true,insecure:false},(err,summary)=>{
   const result=summarize(collection.item.map(i=>i.name),summary);
   result.diagnostics=(summary?.run?.executions||[]).flatMap(e=>{
    const details=safeDiagnostics(e.response);return Object.keys(details).length?[{step:e.item.name,...details}]:[];
   });
   if(err)result.status='BLOCKED';fs.writeFileSync(path.join(input.output,'result.json'),JSON.stringify(result,null,2));resolve();
  });
  runner.on('console',(err,args)=>{
   for(const msg of args.messages||[]){
    if(typeof msg!=='string'||!msg.startsWith('OWNED_CHECKPOINT '))continue;
    const q=JSON.parse(msg.slice(17));
    if(q.memory&&!checkpoint.owned_memories.some(r=>r.memory_id===q.memory.memory_id&&r.version===q.memory.version))checkpoint.owned_memories.push(q.memory);
    if(q.source&&q.memory&&!checkpoint.owned_sources.some(r=>r.source_id===q.source.source_id)){
     checkpoint.owned_sources.push(q.source);checkpoint.source_bindings.push({source:q.source,memory:q.memory});
    }
    checkpoint.cleanup_tasks=[...new Set([...checkpoint.cleanup_tasks,...q.cleanupTasks])];
    checkpoint={...checkpoint,...q};fs.writeFileSync(checkpointFile+'.new',JSON.stringify(checkpoint,null,2));fs.renameSync(checkpointFile+'.new',checkpointFile);
   }
  });
 });
}
module.exports={safeDiagnostics};
if(require.main===module)main().catch(()=>{console.error('live runner stopped; inspect owned checkpoint');process.exitCode=2;});
