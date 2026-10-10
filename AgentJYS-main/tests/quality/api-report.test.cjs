const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
function load(){
  const p=path.resolve(__dirname,'../../scripts/quality/run_apifox.cjs');
  assert.ok(fs.existsSync(p),'API result normalizer required');
  return require(p);
}
test('empty assertions or missing execution cannot pass',()=>{
  const {summarize}=load();
  const report=summarize(['a','b'],{run:{executions:[{item:{name:'a'},response:{code:200},assertions:[]}],failures:[]}});
  assert.equal(report.status,'BLOCKED');
  assert.equal(report.instances.length,2);
});
test('real failed assertion is preserved without exposing response data',()=>{
  const {summarize}=load();
  const report=summarize(['a'],{run:{executions:[{item:{name:'a'},response:{code:200,stream:'secret'},assertions:[{assertion:'identity',error:{message:'secret'}}]}],failures:[]}});
  assert.equal(report.status,'FAIL');
  assert.ok(!JSON.stringify(report).includes('secret'));
});
test('run level errors and skipped assertions invalidate otherwise green results',()=>{
  const {summarize}=load();
  const execution={item:{name:'a'},response:{code:200},assertions:[{assertion:'identity'}]};
  assert.equal(summarize(['a'],{run:{executions:[execution],failures:[]}}).status,'PASS');
  assert.equal(summarize(['a'],{run:{executions:[execution],failures:[{error:{name:'Error'}}]}}).status,'BLOCKED');
  execution.assertions[0].skipped=true;
  assert.equal(summarize(['a'],{run:{executions:[execution],failures:[]}}).status,'BLOCKED');
});
test('Newman helper requests share one mutable execution but real reruns stay distinct',()=>{
  const {summarize}=load();
  const e={item:{name:'a'},response:{code:200},assertions:[{assertion:'identity'}]};
  assert.equal(summarize(['a'],{run:{executions:[e,e,e],failures:[]}}).status,'PASS');
  assert.equal(summarize(['a'],{run:{executions:[e,{...e}],failures:[]}}).status,'BLOCKED');
});
test('runner refuses a remote base URL before processing credentials',()=>{
  const {validateEnvironment}=load();
  assert.throws(()=>validateEnvironment({values:[{key:'base_url',value:'https://business.example'},{key:'quality_namespace',value:'aether-quality-20261009'}]}));
  validateEnvironment({values:[{key:'base_url',value:'http://127.0.0.1:14880'},{key:'quality_namespace',value:'aether-quality-20261009'}]});
});
