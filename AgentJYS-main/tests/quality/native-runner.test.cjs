const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const http=require('node:http');
const runner=require('../../scripts/quality/run_native.cjs');

function scene(){return {id:100,name:'test journey',steps:[{name:'first',type:'http',disable:false,httpApiCase:{
  method:'get',path:'http://127.0.0.1:1/BLOCKED',requestBody:{type:'none'},
  parameters:{header:[],query:[]},
  preProcessors:[{enable:true,type:'customScript',data:"pm.request.url.update(pm.environment.get('base')+'/first'); pm.variables.set('seen','yes');"}],
  postProcessors:[{enable:true,type:'customScript',data:"pm.test('real response',()=>pm.response.to.have.status(200));"}]
}},{name:'second',type:'http',disable:false,httpApiCase:{method:'get',path:'http://127.0.0.1:1/BLOCKED',requestBody:{type:'none'},parameters:{header:[],query:[]},
preProcessors:[{enable:true,type:'customScript',data:"pm.test('state preserved',()=>pm.expect(pm.variables.get('seen')).to.eql('yes')); pm.request.url.update(pm.environment.get('base')+'/second');"}],
postProcessors:[{enable:true,type:'customScript',data:"pm.test('response valid',()=>pm.response.to.have.status(200));"}]
}}]};}

test('native conversion preserves two visible requests and per-step scripts',()=>{
 const c=runner.buildCollection(scene());
 assert.equal(c.item.length,2);
 assert.equal(c.item[0].event[0].script.exec.join('\n'),scene().steps[0].httpApiCase.preProcessors[0].data);
 assert.equal(c.item[1].request.method,'GET');
});

test('zero native steps and unsupported steps cannot silently pass',()=>{
 assert.throws(()=>runner.buildCollection({...scene(),steps:[]}),/BLOCKED/);
 const s=scene();s.steps[1].type='condition';assert.throws(()=>runner.buildCollection(s),/BLOCKED/);
});

test('expected steps with absent responses or zero assertions are blocked',()=>{
 const expected=runner.buildCollection(scene()).item.map(i=>i.name);
 const result=runner.summarize(expected,{run:{executions:[{item:{name:expected[0]},response:{code:200},assertions:[]}],failures:[]}});
 assert.equal(result.status,'BLOCKED');assert.equal(result.steps.length,2);
});

test('transport and admission errors are blocked; real assertion failures fail',()=>{
 const name='one';
 const admission={item:{name},assertions:[{assertion:'BLOCKED: credential missing',error:{message:'do not include secret'}}]};
 assert.equal(runner.summarize([name],{run:{executions:[admission],failures:[]}}).status,'BLOCKED');
 const failure={item:{name},response:{code:200},assertions:[{assertion:'contract mismatch',error:{message:'raw secret value'}}]};
 const result=runner.summarize([name],{run:{executions:[failure],failures:[]}});
 assert.equal(result.status,'FAIL');assert.ok(!JSON.stringify(result).includes('raw secret value'));
});

test('wrong profile or missing credentials is blocked before requests',()=>{
 const v={environment_kind:'quality_identity',auth_base:'http://127.0.0.1:14880',max_requests:'40',quality_namespace:'aether-quality-20261009'};
 assert.ok(runner.preflight('identity',v).length);
 assert.ok(runner.preflight('public',v).length);
 assert.ok(runner.preflight('memory',v).length);
 assert.ok(runner.preflight('performance',v).length);
});

test('identity preflight uses the same base_url as native identity scripts',()=>{
 const v={environment_kind:'quality_identity',base_url:'http://127.0.0.1:14880',max_requests:'40',quality_namespace:'aether-quality-20261009',aetherusera_password:'synthetic',aetherusera2_password:'synthetic',aetheruserb_password:'synthetic'};
 assert.deepEqual(runner.preflight('identity',v),[]);
});
test('later runner crash preserves earlier failure and marks request counts incomplete',async()=>{
 const a=scene(), b={...scene(),id:101,name:'second journey'};
 const native={apiTestCaseCollection:[{items:[a,b]}],testSuiteCollection:[{items:[{name:'Aether_每次部署_公网冒烟',items:[{type:'STATIC_TEST_SCENARIO',testScenarios:[{id:100},{id:101}]}]}]}]};
 let calls=0;
 const fake={run(options,callback){
  if(calls++)throw Error('synthetic startup crash with secret');
  callback(null,{run:{executions:options.collection.item.map(item=>({item,response:{code:200},assertions:[{assertion:'contract',error:{message:'mismatch'}}]})),failures:[]}});
 }};
 const result=await runner.execute(native,'public',{environment_kind:'public_readonly',max_requests:'40',public_origin:'https://aether-p3-demo-c50c3827.southeastasia.cloudapp.azure.com'},fake);
 assert.equal(result.status,'FAIL');assert.equal(result.scenarios.length,2);
 assert.equal(result.scenarios[0].status,'FAIL');assert.equal(result.scenarios[1].status,'BLOCKED');
 assert.equal(result.requests,2);assert.equal(result.request_count_complete,false);
 assert.ok(!JSON.stringify(result).includes('synthetic startup crash with secret'));
});

test('exact native scripts execute ordered real local HTTP and retain no response bodies',async()=>{
 const requests=[];
 const server=http.createServer((req,res)=>{requests.push(req.url);res.setHeader('Content-Type','application/json');res.end('{"secret":"response-secret"}');});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{
  const newman=require(path.join(process.env.QUALITY_NODE_MODULES,'newman'));
  const result=await runner.runOne(scene(),{base:`http://127.0.0.1:${server.address().port}`},newman);
  assert.deepEqual(requests,['/first','/second']);assert.equal(result.status,'PASS');
  assert.ok(!JSON.stringify(result).includes('response-secret'));
 }finally{await new Promise(resolve=>server.close(resolve));}
});
