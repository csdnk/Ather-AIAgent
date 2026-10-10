const {test}=require('node:test');
const assert=require('node:assert/strict');
const http=require('node:http');
const path=require('node:path');
const fs=require('node:fs');
const {execFileSync}=require('node:child_process');
const newman=require(path.join(process.env.QUALITY_NODE_MODULES,'newman'));
const out=fs.mkdtempSync(path.join(process.env.QUALITY_TEST_WORKSPACE,'api-protocol-'));
execFileSync('python',[path.resolve(__dirname,'../../scripts/quality/export_apifox.py'),'--output',out]);
const collection=JSON.parse(fs.readFileSync(path.join(out,'Aether.postman_collection.json')));

async function simulate(name,namespace){
  const requests=[];
  const server=http.createServer((req,res)=>{
    requests.push(req.url);
    res.setHeader('Content-Type','application/json');
    res.end(JSON.stringify(req.url.endsWith('/login')?{code:0,data:{userId:50004,accessToken:'synthetic-invalid-token'}}:
      req.url.endsWith('/logout')?{code:0,data:true}:{code:401}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{
    const item=collection.item.find(i=>i.name===name);
    const values={base_url:`http://127.0.0.1:${server.address().port}`,quality_namespace:namespace,
      aetherdisabled_password:'synthetic',aetherusera_password:'synthetic'};
    const summary=await new Promise((resolve,reject)=>newman.run({collection:{info:collection.info,item:[item]},
      environment:{values:Object.entries(values).map(([key,value])=>({key,value,enabled:true}))},
      reporters:[],timeout:10000,timeoutRequest:2000},(err,s)=>err?reject(err):resolve(s)));
    return {requests,summary};
  }finally{await new Promise(resolve=>server.close(resolve));}
}
test('wrong environment issues zero HTTP requests even for password-bearing cases',async()=>{
  const result=await simulate('SEC-01/disabled-user: login denied','business-environment');
  assert.deepEqual(result.requests,[]);
});
test('logout must not pass using a token that was never valid',async()=>{
  const result=await simulate('SEC-05/logout: original token revoked','aether-quality-20261009');
  assert.ok(!result.requests.some(p=>p.endsWith('/logout')));
  assert.ok(result.summary.run.failures.length>0 || !result.summary.run.executions.length);
});
