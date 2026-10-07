import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

test('chat uploads PDF bytes before sending references and preserves the turn across an unknown response', {skip:!process.env.AETHER_WEB_DEPENDENCIES},async()=>{
 const require=createRequire(path.join(process.env.AETHER_WEB_DEPENDENCIES,'package.json'));
 const {build}=require('esbuild'),{JSDOM}=require('jsdom');
 const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
 const built=await build({entryPoints:[path.join(root,'src/App.jsx')],bundle:true,write:false,format:'iife',loader:{'.css':'empty'},nodePaths:[path.join(process.env.AETHER_WEB_DEPENDENCIES,'node_modules')],define:{'process.env.NODE_ENV':'"production"'}});
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost:19010/',runScripts:'outside-only',pretendToBeVisual:true});
 const uploads=[],turns=[];let persisted=false;
 const original={name:'报告.pdf',size:3,arrayBuffer:async()=>new Uint8Array([97,98,99]).buffer};
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 dom.window.fetch=async(url,options={})=>{
  let body={};
  if(url==='/auth/me')body={user_id:'a',role:'user',display_name:'测试用户',csrf_token:'test'};
  else if(url==='/chat-api/conversations')body={conversations:[{id:'c1',title:'附件验收'}]};
  else if(url==='/memory-api/documents'){uploads.push(options);body={result:{saved:true,source:{},memories:[]}}}
  else if(url.endsWith('/messages')){turns.push(JSON.parse(options.body));if(turns.length===1)throw new Error('connection lost');persisted=true;body={}}
  else body={messages:persisted?[{id:'u',turn_id:turns[0].turn_id,role:'user',status:'complete',content:'请阅读并概括附件内容。',attachments:turns[0].attachments}]:[]};
  return {ok:true,json:async()=>body};
 };
 async function until(predicate){for(let i=0;i<100;i++){if(predicate())return;await new Promise(r=>setTimeout(r,20))}assert.fail('UI did not reach expected state: '+dom.window.document.body.textContent)}
 try{
  dom.window.eval(built.outputFiles[0].text);await until(()=>dom.window.document.querySelector('.conversation-main'));
  dom.window.document.querySelector('.conversation-main').click();
  const picker=dom.window.document.querySelector('input[type=file]');Object.defineProperty(picker,'files',{value:[original],configurable:true});picker.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  await until(()=>dom.window.document.querySelector('.attachment-chip'));
  dom.window.document.querySelector('.composer').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await until(()=>turns.length===1&&dom.window.document.querySelector('.error-banner'));
  assert.equal(uploads.length,1);assert.equal(uploads[0].body,original);assert.equal(uploads[0].headers['Content-Type'],'application/pdf');
  assert.equal(turns[0].content,'请阅读并概括附件内容。');assert.deepEqual(turns[0].attachments,[{upload_id:uploads[0].headers['X-Upload-ID'],name:'报告.pdf'}]);
  assert.ok(dom.window.document.querySelector('.composer .attachment-chip'));
  const stored=dom.window.sessionStorage.getItem('aether-chat-drafts:a');assert.ok(stored);assert.ok(!stored.includes('arrayBuffer'));
  [...dom.window.document.querySelectorAll('.error-banner button')].find(b=>b.textContent==='重试发送原消息').click();
  await until(()=>dom.window.document.querySelector('.message.user .attachment-chip'));
  assert.equal(uploads.length,1);assert.deepEqual(turns[1],turns[0]);
  assert.equal(dom.window.document.querySelector('.composer .attachment-chip'),null);
 }finally{dom.window.close()}
});

async function fixture({storage,onUpload}={}){
 const require=createRequire(path.join(process.env.AETHER_WEB_DEPENDENCIES,'package.json'));
 const {build}=require('esbuild'),{JSDOM}=require('jsdom');
 const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
 const built=await build({entryPoints:[path.join(root,'src/App.jsx')],bundle:true,write:false,format:'iife',loader:{'.css':'empty'},nodePaths:[path.join(process.env.AETHER_WEB_DEPENDENCIES,'node_modules')],define:{'process.env.NODE_ENV':'"production"'}});
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost:19010/',runScripts:'outside-only',pretendToBeVisual:true}),uploads=[],turns=[];
 if(storage)dom.window.sessionStorage.setItem('aether-chat-drafts:a',storage);
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 dom.window.fetch=async(url,options={})=>{
  let body={};
  if(url==='/auth/me')body={user_id:'a',role:'user',display_name:'测试用户',csrf_token:'test'};
  else if(url==='/chat-api/conversations')body={conversations:[{id:'c1',title:'对话一'},{id:'c2',title:'对话二'}]};
  else if(url==='/memory-api/documents'){uploads.push(options);if(onUpload)return onUpload(options);body={result:{saved:true,source:{},memories:[]}}}
  else if(url.endsWith('/messages')){turns.push({url,...JSON.parse(options.body)});body={}}
  else body={messages:[]};return {ok:true,json:async()=>body};
 };
 const document=dom.window.document;
 async function until(predicate){for(let i=0;i<100;i++){if(predicate())return;await new Promise(r=>setTimeout(r,20))}assert.fail('UI did not reach expected state: '+document.body.textContent)}
 async function select(content='abc'){const picker=document.querySelector('input[type=file]');Object.defineProperty(picker,'files',{value:[{name:'报告.pdf',size:content.length,arrayBuffer:async()=>new TextEncoder().encode(content).buffer}],configurable:true});picker.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await new Promise(r=>setTimeout(r,30))}
 const submit=()=>document.querySelector('.composer').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
 dom.window.eval(built.outputFiles[0].text);await until(()=>document.querySelector('.conversation-main'));document.querySelector('.conversation-main').click();await new Promise(r=>setTimeout(r,30));
 return {dom,document,uploads,turns,until,select,submit};
}

test('refresh after unknown upload requires identical bytes and resumes the original upload and turn IDs',{skip:!process.env.AETHER_WEB_DEPENDENCIES},async()=>{
 const first=await fixture({onUpload:async()=>{throw new Error('network lost')}});let stored,uploadId,turnId;
 try{await first.select();first.submit();await first.until(()=>first.uploads.length===1&&first.document.querySelector('.error-banner'));
  stored=first.dom.window.sessionStorage.getItem('aether-chat-drafts:a');const draft=JSON.parse(stored).find(([id])=>id==='c1')[1];uploadId=draft.attachments[0].upload_id;turnId=draft.delivery.turnId;
  assert.equal(draft.attachments[0].sha256,'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');assert.equal(draft.attachments[0].file,undefined);
 }finally{first.dom.window.close()}
 const next=await fixture({storage:stored});
 try{
  assert.match(next.document.querySelector('.attachment-chip').textContent,/重新选择/);
  next.document.querySelector('.attachment-chip button').click();await next.select('xyz');assert.match(next.document.querySelector('.error-banner').textContent,/原文件/);assert.equal(next.uploads.length,0);
  next.document.querySelector('.attachment-chip button').click();await next.select();next.submit();await next.until(()=>next.turns.length===1);
  assert.equal(next.uploads[0].headers['X-Upload-ID'],uploadId);assert.equal(next.turns[0].turn_id,turnId);assert.equal(next.turns[0].attachments[0].upload_id,uploadId);
 }finally{next.dom.window.close()}
});

test('late upload results stay in the original conversation draft',{skip:!process.env.AETHER_WEB_DEPENDENCIES},async()=>{
 let finish;const f=await fixture({onUpload:()=>new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({result:{saved:true,source:{},memories:[]}})})})});
 try{await f.select();f.submit();await f.until(()=>finish);f.document.querySelectorAll('.conversation-main')[1].click();await new Promise(r=>setTimeout(r,30));finish();await new Promise(r=>setTimeout(r,80));
 assert.equal(f.turns.length,0);assert.equal(f.document.querySelector('.composer .attachment-chip'),null);assert.equal(f.document.querySelector('textarea').value,'');
 f.document.querySelector('.conversation-main').click();await f.until(()=>f.document.querySelector('.attachment-chip'));assert.match(f.document.querySelector('.attachment-chip').textContent,/已上传/);
 f.submit();await f.until(()=>f.turns.length===1);assert.equal(f.turns[0].url,'/chat-api/conversations/c1/messages');assert.equal(f.uploads.length,1);
 }finally{f.dom.window.close()}
});

test('a definitive invalid-document response allows replacing the rejected file',{skip:!process.env.AETHER_WEB_DEPENDENCIES},async()=>{
 const f=await fixture({onUpload:async()=>({ok:false,status:422,json:async()=>({detail:'文档无法解析'})})});
 try{await f.select();f.submit();await f.until(()=>f.document.querySelector('.error-banner'));
  const remove=f.document.querySelector('[aria-label="移除 报告.pdf"]');assert.equal(remove.disabled,false);remove.click();await f.until(()=>!f.document.querySelector('.attachment-chip'));assert.equal(f.turns.length,0);
 }finally{f.dom.window.close()}
});
