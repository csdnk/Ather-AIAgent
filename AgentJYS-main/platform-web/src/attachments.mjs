const mediaTypes={txt:'text/plain',md:'text/markdown',csv:'text/csv',pdf:'application/pdf',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'};
export function newIdentifier(){
 if(globalThis.crypto.randomUUID)return globalThis.crypto.randomUUID();
 const bytes=globalThis.crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
 const hex=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
// SHA-256 fallback also works on HTTP demo origins where SubtleCrypto is unavailable.
async function digest(buffer){
 if(globalThis.crypto?.subtle)return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),b=>b.toString(16).padStart(2,'0')).join('');
 const k=[],h=[];for(let n=2;k.length<64;n++){let prime=true;for(let d=2;d*d<=n;d++)if(n%d===0){prime=false;break}if(prime){if(h.length<8)h.push((Math.sqrt(n)%1*4294967296)|0);k.push((Math.cbrt(n)%1*4294967296)|0)}}
 const input=new Uint8Array(buffer),size=Math.ceil((input.length+9)/64)*64,data=new Uint8Array(size);data.set(input);data[input.length]=128;
 const view=new DataView(data.buffer);view.setUint32(size-8,Math.floor(input.length/536870912));view.setUint32(size-4,input.length*8);
 const rotate=(x,n)=>(x>>>n)|(x<<(32-n)),w=new Int32Array(64);
 for(let offset=0;offset<size;offset+=64){
  for(let i=0;i<16;i++)w[i]=view.getInt32(offset+i*4);
  for(let i=16;i<64;i++){const x=w[i-15],y=w[i-2];w[i]=(w[i-16]+(rotate(x,7)^rotate(x,18)^(x>>>3))+w[i-7]+(rotate(y,17)^rotate(y,19)^(y>>>10)))|0}
  let [a,b,c,d,e,f,g,j]=h;
  for(let i=0;i<64;i++){const t1=(j+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+k[i]+w[i])|0,t2=((rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c)))|0;j=g;g=f;f=e;e=(d+t1)|0;d=c;c=b;b=a;a=(t1+t2)|0}
  [a,b,c,d,e,f,g,j].forEach((value,i)=>h[i]=(h[i]+value)|0);
 }
 return h.map(value=>(value>>>0).toString(16).padStart(8,'0')).join('');
}
export async function readAttachment(file,uuid=newIdentifier){
 const media=mediaTypes[file.name.split('.').pop().toLowerCase()];
 if(!media)throw new Error('支持 TXT、Markdown、CSV、PDF 和 DOCX 文件。');
 if(file.size>5*1024*1024)throw new Error('每个文件不能超过 5 MB。');
 if(!file.size)throw new Error('文件内容为空。');
 const sha256=await digest(await file.arrayBuffer());
 return {file,name:file.name,size:file.size,media,sha256,upload_id:uuid(),status:'ready'};
}
export function attachmentMetadata(item){
 const {name,size,media,sha256,upload_id}=item;
 return {name,size,media,sha256,upload_id,status:item.status==='uploaded'?'uploaded':'reselect'};
}
export async function restoreAttachment(file,metadata){
 const item=await readAttachment(file,()=>metadata.upload_id);
 if(item.name!==metadata.name||item.size!==metadata.size||item.sha256!==metadata.sha256)throw new Error('请选择上次附件的原文件，内容必须一致。');
 return {...item,status:metadata.status==='uploaded'?'uploaded':'ready'};
}
export async function uploadAttachment(item,request,csrf){
 if(item.status==='uploaded')return item;
 if(!item.file)throw new Error(`请重新选择原文件「${item.name}」后继续。`);
 const data=await request('/memory-api/documents',{method:'POST',headers:{...csrf,'Content-Type':item.media,'X-Upload-ID':item.upload_id},body:item.file});
 if(data.result?.saved!==true)throw new Error('文档保存尚未确认，请重试原文件。');
 return {...item,status:'uploaded',result:data.result};
}
export function composeMessage(input,attachments=[]){
 if(attachments.length>3)throw new Error('每次最多添加 3 个文件。');
 const content=input.trim()||(attachments.length?'请阅读并概括附件内容。':'');
 if(content.length>12000)throw new Error('消息不能超过 12000 字符，请减少内容后发送。');
 return content;
}
