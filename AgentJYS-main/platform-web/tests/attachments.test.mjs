import test from 'node:test';
import assert from 'node:assert/strict';
import * as attachments from '../src/attachments.mjs';
const file=(name,content)=>({name,size:new TextEncoder().encode(content).length,arrayBuffer:async()=>new TextEncoder().encode(content).buffer});
test('stages original PDF bytes with a stable ID and SHA256 without text injection',async()=>{
 const original=file('计划.pdf','abc'); const item=await attachments.readAttachment(original,()=> 'upload-1');
 assert.equal(item.file,original);assert.equal(item.upload_id,'upload-1');
 assert.equal(item.sha256,'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
 assert.equal(item.media,'application/pdf'); assert.equal(item.text,undefined);
 assert.equal(attachments.composeMessage('什么时候交付？',[item]),'什么时候交付？');
});
test('supports five document types, rejects empty, oversized and unsupported files',async()=>{
 for(const ext of ['txt','md','csv','pdf','docx'])await attachments.readAttachment(file('a.'+ext,'abc'),()=> 'id');
 await assert.rejects(attachments.readAttachment(file('a.exe','abc')),/TXT/);
 await assert.rejects(attachments.readAttachment(file('a.txt','')),/为空/);
 await assert.rejects(attachments.readAttachment({...file('a.pdf','a'),size:5242881}),/5 MB/);
 assert.equal(attachments.composeMessage('x'.repeat(12000),[{}]).length,12000);
 assert.throws(()=>attachments.composeMessage('x'.repeat(12001),[]),/12000/);
 assert.throws(()=>attachments.composeMessage('ok',[{},{},{},{}]),/3/);
 assert.equal(attachments.composeMessage('',[{}]),'请阅读并概括附件内容。');
});
test('reselection binds exact bytes and resumes saved metadata without storing file content',async()=>{
 assert.equal(typeof attachments.restoreAttachment,'function');
 const original=await attachments.readAttachment(file('a.pdf','abc'),()=> 'upload-1');
 const metadata=attachments.attachmentMetadata({...original,result:{saved:true},status:'uploaded'});
 assert.equal(metadata.file,undefined);assert.equal(metadata.text,undefined);
 const resumed=await attachments.restoreAttachment(file('a.pdf','abc'),metadata);
 assert.equal(resumed.upload_id,'upload-1');assert.equal(resumed.status,'uploaded');
 await assert.rejects(attachments.restoreAttachment(file('a.pdf','xyz'),metadata),/原文件/);
});
test('upload posts raw bytes and reuses ID after unknown result; saved items are not uploaded again',async()=>{
 assert.equal(typeof attachments.uploadAttachment,'function');
 const staged=await attachments.readAttachment(file('a.docx','abc'),()=> 'upload-1');let calls=0;
 const request=async(url,options)=>{calls++;assert.equal(url,'/memory-api/documents');assert.equal(options.body,staged.file);assert.equal(options.headers['X-Upload-ID'],'upload-1');assert.equal(options.headers['x-csrf-token'],'csrf');assert.equal(options.headers['Content-Type'],'application/vnd.openxmlformats-officedocument.wordprocessingml.document');if(calls===1)throw new Error('connection lost');return {result:{saved:true,source:{},memories:[]}}};
 await assert.rejects(attachments.uploadAttachment(staged,request,{'x-csrf-token':'csrf'}),/connection lost/);
 const saved=await attachments.uploadAttachment(staged,request,{'x-csrf-token':'csrf'});
 assert.equal(saved.status,'uploaded');await attachments.uploadAttachment(saved,request,{});assert.equal(calls,2);
 await assert.rejects(attachments.uploadAttachment({...staged,file:null},request,{}),/重新选择/);
 await assert.rejects(attachments.uploadAttachment(staged,async()=>({result:{saved:false}}),{}),/确认/);
});

test('HTTP fallback hashes multi-block document bytes identically to SHA256',async()=>{
 const {createHash}=await import('node:crypto');const descriptor=Object.getOwnPropertyDescriptor(globalThis,'crypto');
 try{Object.defineProperty(globalThis,'crypto',{configurable:true,value:{}});
  for(const length of [3,55,56,63,64,65,100000]){const content='a'.repeat(length);const item=await attachments.readAttachment(file('a.pdf',content),()=> 'upload');assert.equal(item.sha256,createHash('sha256').update(content).digest('hex'))}
 }finally{Object.defineProperty(globalThis,'crypto',descriptor)}
});
