// Run the importable collection using Newman, without writing credentials or
// raw responses to reports. Native Apifox execution is a separate acceptance.
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');

function validateEnvironment(environment) {
  const values=Object.fromEntries((environment?.values||[]).filter(v=>v.enabled!==false).map(v=>[v.key,v.value]));
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(values.base_url||'') || values.quality_namespace!=='aether-quality-20261009') {
    throw new Error('BLOCKED: only the owned loopback test environment is permitted');
  }
}

function summarize(expected, summary) {
  // Newman 6.2.1 appends the same execution object for pm.sendRequest callbacks.
  // Deduplicate object references only: distinct actual executions remain errors.
  const executions=[...new Set(summary?.run?.executions || [])];
  const instances=expected.map(name=>{
    const matches=executions.filter(e=>e.item?.name===name);
    const e=matches[0];
    const assertions=e?.assertions || [];
    const failed=assertions.filter(a=>a.error).map(a=>a.assertion);
    const incomplete=matches.length!==1 || !e?.response || !assertions.length || assertions.some(a=>a.skipped);
    return {name,status:failed.length?'FAIL':incomplete?'BLOCKED':'PASS',
      last_http_status:e?.response?.code || null,assertions:assertions.length,failed_assertions:failed};
  });
  const unexpected=executions.some(e=>!expected.includes(e.item?.name));
  const runErrors=(summary?.run?.failures || []).length;
  let status=instances.some(i=>i.status==='FAIL')?'FAIL':instances.some(i=>i.status==='BLOCKED')?'BLOCKED':'PASS';
  if (status==='PASS' && (!expected.length || unexpected || runErrors)) status='BLOCKED';
  return {status,instances,run_errors:runErrors,unexpected_execution:unexpected,
    counts:Object.fromEntries(['PASS','FAIL','BLOCKED','NOT_RUN','N/A'].map(s=>[s,instances.filter(i=>i.status===s).length]))};
}

async function main() {
  const [collectionPath,output,testSourceSha,serviceImageDigest]=process.argv.slice(2);
  if (!collectionPath || !output || !/^[a-f0-9]{40}$/.test(testSourceSha||'') || !/^sha256:[a-f0-9]{64}$/.test(serviceImageDigest||'')) {
    throw new Error('Usage: node run_apifox.cjs COLLECTION OUTPUT TEST_SOURCE_SHA SERVICE_IMAGE_DIGEST; environment JSON on stdin; QUALITY_NODE_MODULES required');
  }
  fs.mkdirSync(output,{recursive:false});
  const newman=require(path.join(process.env.QUALITY_NODE_MODULES || '', 'newman'));
  let input='';
  for await (const part of process.stdin) input+=part;
  const environment=JSON.parse(input);
  input='';
  validateEnvironment(environment);
  const collection=JSON.parse(fs.readFileSync(collectionPath,'utf8'));
  const testSourceDirty=execFileSync('git',['status','--porcelain'],{cwd:path.resolve(__dirname,'../../..'),encoding:'utf8'}).trim().length>0;
  const toolDigests=Object.fromEntries(['run_apifox.cjs','export_apifox.py','apifox-assertions.js'].map(name=>[
    name,crypto.createHash('sha256').update(fs.readFileSync(path.join(__dirname,name))).digest('hex')]));
  const expected=collection.item.map(x=>x.name);
  const startedAt=new Date().toISOString();
  newman.run({collection,environment,reporters:[],iterationCount:1,timeout:180000,
    timeoutRequest:15000,timeoutScript:45000,ignoreRedirects:true},(err,summary)=>{
    const result=summarize(expected,summary);
    if(err && result.status==='PASS')result.status='BLOCKED';
    Object.assign(result,{run_id:crypto.randomUUID(),started_at:startedAt,completed_at:new Date().toISOString(),
      test_source_sha:testSourceSha,service_image_digest:serviceImageDigest,
      test_source_dirty:testSourceDirty,tool_sha256:toolDigests,
      service_source_sha:null,release_gate:'BLOCKED',executor:'newman-6.2.1',
      native_apifox_status:'NOT_RUN',
      scope:'9 independent API scenarios; partial SEC-01/05/06 coverage; no whole-case acceptance',
      collection_sha256:crypto.createHash('sha256').update(fs.readFileSync(collectionPath)).digest('hex')});
    const json=JSON.stringify(result,null,2)+'\n';
    fs.writeFileSync(path.join(output,'summary.json'),json,{flag:'wx'});
    const lines=[`# Aether API test: ${result.status}`,'',`Run: ${result.run_id}`,
      `Executor: Newman (native Apifox NOT_RUN)`,`Test source baseline: ${testSourceSha}; dirty=${testSourceDirty}; exact tool hashes in summary.json`,
      `Service image: ${serviceImageDigest}`,`Release gate: BLOCKED (full environment and source mapping pending)`,'',
      ...result.instances.map(i=>`- ${i.status} | ${i.name} | assertions=${i.assertions} | failed=${i.failed_assertions.join(', ')||'none'}`)];
    fs.writeFileSync(path.join(output,'agent-report.md'),lines.join('\n')+'\n',{flag:'wx'});
    fs.writeFileSync(path.join(output,'evidence-manifest.json'),JSON.stringify({
      'summary.json':crypto.createHash('sha256').update(json).digest('hex'),
      'agent-report.md':crypto.createHash('sha256').update(fs.readFileSync(path.join(output,'agent-report.md'))).digest('hex')},null,2),{flag:'wx'});
    console.log(JSON.stringify({status:result.status,counts:result.counts,run_errors:result.run_errors}));
    process.exitCode=result.status==='PASS'?0:2;
  });
}
module.exports={summarize,validateEnvironment};
if(require.main===module)main().catch(()=>{console.error('Runner setup failed; private details suppressed');process.exitCode=2;});
