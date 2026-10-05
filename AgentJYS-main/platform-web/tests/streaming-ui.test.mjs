import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

test('the actual chat component renders partial content before completion', {
  skip: !process.env.AETHER_WEB_DEPENDENCIES,
}, async()=>{
  const require=createRequire(path.join(process.env.AETHER_WEB_DEPENDENCIES,'package.json'));
  const {build}=require('esbuild');
  const {JSDOM}=require('jsdom');
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
  const built=await build({entryPoints:[path.join(root,'src/App.jsx')],bundle:true,write:false,
    format:'iife',loader:{'.css':'empty'},nodePaths:[path.join(process.env.AETHER_WEB_DEPENDENCIES,'node_modules')],
    define:{'process.env.NODE_ENV':'"production"'}});
  const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost:19010/',runScripts:'outside-only',pretendToBeVisual:true});
  let complete=false;
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  dom.window.fetch=async url=>({ok:true,json:async()=>url==='/auth/me'
    ?{user_id:'a',role:'user',display_name:'测试用户',csrf_token:'test'}
    :url==='/chat-api/conversations'
      ?{conversations:[{id:'c1',title:'首字验收'}]}
      :{messages:[{id:'u',role:'user',status:'complete',content:'你好'},
        {id:'a',role:'assistant',phase:'generating',status:complete?'complete':'pending',content:complete?'第一段，完整回答。':'第一段',memory_status:'not_connected'}]}});
  async function until(predicate){
    for(let i=0;i<100;i++){if(predicate())return;await new Promise(r=>setTimeout(r,20))}
    assert.fail('UI did not reach the expected render state');
  }
  try{
    dom.window.eval(built.outputFiles[0].text);
    await until(()=>dom.window.document.querySelector('.conversation-main'));
    dom.window.document.querySelector('.conversation-main').click();
    await until(()=>dom.window.document.querySelector('.assistant .message-content'));
    assert.equal(dom.window.document.querySelector('.assistant .message-content').textContent,'第一段');
    assert.match(dom.window.document.querySelector('.assistant .thinking').textContent,/正在生成/);
    complete=true;
    await until(()=>dom.window.document.querySelector('.assistant .message-content')?.textContent==='第一段，完整回答。');
    assert.equal(dom.window.document.querySelector('.assistant .thinking'),null);
  }finally{dom.window.close()}
});
