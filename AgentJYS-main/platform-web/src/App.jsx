import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import MemoryCenter from './MemoryCenter.jsx';
import LoginScreen from './LoginScreen.jsx';
import {apiUrl} from './api-url.mjs';
import {submission, pollDelay, nextUpdate, replyPhase} from './delivery.mjs';
import {readAttachment, composeMessage} from './attachments.mjs';

const paths={attach:'m21 11-9 9a6 6 0 0 1-8-8L14 2a4 4 0 0 1 6 6L10 18a2 2 0 0 1-3-3l9-9',plus:'M12 5v14M5 12h14',send:'m6 12 6-6 6 6M12 6v13',search:'m21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',chat:'M21 11a9 9 0 0 1-9 9H4l-3 2V11a10 10 0 0 1 20 0Z',arrow:'m9 5 7 7-7 7',exit:'M9 4H4v16h5M10 12h11m-4-4 4 4-4 4',check:'m5 12 4 4L19 6',close:'m6 6 12 12M6 18 18 6',menu:'M4 6h16M4 12h16M4 18h16',book:'M12 6c-3-3-7-3-10-2v15c3-1 7-1 10 2 3-3 7-3 10-2V4c-3-1-7-1-10 2Zm0 0v15',edit:'m4 15 11-11 5 5L9 20H4v-5Z',trash:'M3 6h18M9 3h6M6 6l1 15h10l1-15M10 10v7m4-7v7',grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z'};
function Icon({name,size=19}) {return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]||paths.chat}/></svg>}
function Mark({small=false}) {return <span className={'mark '+(small?'small':'')} aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="m16 3 4.1 8.9L29 16l-8.9 4.1L16 29l-4.1-8.9L3 16l8.9-4.1L16 3Z" stroke="currentColor" strokeWidth="2"/><path d="m16 11 5 5-5 5-5-5 5-5Z" fill="currentColor"/></svg></span>}
async function apiRequest(url, options={}) {
  const response=await fetch(apiUrl(url),{credentials:'same-origin',...options,headers:{'Content-Type':'application/json',...options.headers}});
  let body={};try{body=await response.json()}catch{}
  if(!response.ok){const error=new Error(response.status===401?'登录已过期，请重新登录。':response.status===403?'当前账号没有此操作权限。':body.detail||'操作未完成，请稍后重试。');error.status=response.status;throw error}
  return body;
}
function Inline({text}){return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part,i)=>part.startsWith('**')?<strong key={i}>{part.slice(2,-2)}</strong>:part.startsWith('`')?<code key={i}>{part.slice(1,-1)}</code>:part)}
function Content({text}) {return <div className="message-content">{text.split(/(```[\s\S]*?```)/g).map((part,i)=>part.startsWith('```')?<pre key={i}><code>{part.replace(/^```[^\n]*\n?/,'').replace(/```$/,'')}</code></pre>:<div key={i}><Inline text={part}/></div>)}</div>}
function App(){
  const [profile,setProfile]=useState(undefined);
  useEffect(()=>{apiRequest('/auth/me').then(setProfile).catch(()=>setProfile(null))},[]);
  if(profile===undefined)return <div className="loading-screen"><Mark/><span>正在打开你的工作空间…</span></div>;
  if(!profile)return <LoginScreen Mark={Mark} onSuccess={async()=>setProfile(await apiRequest('/auth/me'))}/>;
  return <Workspace key={profile.user_id} profile={profile} onSessionLost={()=>setProfile(null)}/>;
}
function Workspace({profile,onSessionLost}){
  const lifetime=useRef({active:true,controller:new AbortController()});
  useEffect(()=>()=>{lifetime.current.active=false;lifetime.current.controller.abort();attachmentGeneration.current++},[]);
  async function request(url,options={}){
    if(!lifetime.current.active)throw new DOMException('Workspace closed','AbortError');
    const signal=options.signal?AbortSignal.any([options.signal,lifetime.current.controller.signal]):lifetime.current.controller.signal;
    try{return await apiRequest(url,{...options,signal})}
    catch(e){if(e.status===401&&lifetime.current.active)onSessionLost();throw e}
  }
  const [conversations,setConversations]=useState([]),[active,setActive]=useState(null),[messages,setMessages]=useState([]),[input,setInput]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[query,setQuery]=useState(''),[sources,setSources]=useState(false),[mobile,setMobile]=useState(false),[rename,setRename]=useState(null),[newTitle,setNewTitle]=useState(''),[copied,setCopied]=useState(null);
  const bottom=useRef(null),textarea=useRef(null),outstanding=useRef(new Map());
  const [memoryView,setMemoryView]=useState(false);
  const [reloadTick,setReloadTick]=useState(0),[loggingOut,setLoggingOut]=useState(false);
  const [attachments,setAttachments]=useState([]),[reading,setReading]=useState(false);
  const fileInput=useRef(null),attachmentGeneration=useRef(0);
  const pending=messages.some(m=>m.status==='pending');
  const authenticated=profile&&profile.role==='user';
  const handleError=e=>{if(lifetime.current.active)setError(e.message)};
  const csrf=()=>({'x-csrf-token':profile?.csrf_token});
  const loadList=async()=>setConversations((await request('/chat-api/conversations')).conversations);
  useEffect(()=>{if(authenticated)loadList().catch(handleError)},[profile?.user_id]);
  useEffect(()=>{
    if(!active||!authenticated){setMessages([]);return}
    let stopped=false,timer,failures=0;
    const update=async()=>{try{const data=await request(`/chat-api/conversations/${active}`);if(stopped)return;failures=0;setMessages(data.messages);const pendingDelivery=outstanding.current.get(active);if(pendingDelivery&&data.messages.some(m=>m.turn_id===pendingDelivery.turnId)){outstanding.current.delete(active);setInput(value=>value.trim()===pendingDelivery.content?'':value)}const delay=nextUpdate(data.messages);if(delay!==null)timer=setTimeout(update,delay)}catch(e){if(!stopped){handleError(e);const delay=pollDelay(e,++failures);if(delay!==null)timer=setTimeout(update,delay)}}};
    update();return()=>{stopped=true;clearTimeout(timer)};
  },[active,profile?.user_id,busy,reloadTick]);
  useEffect(()=>{bottom.current?.scrollIntoView({behavior:'smooth',block:'end'})},[messages.length,messages.at(-1)?.content]);
  async function selectFiles(event){
    const files=Array.from(event.target.files||[]);event.target.value='';
    const generation=attachmentGeneration.current;setReading(true);setError('');
    try{if(attachments.length+files.length>3)throw new Error('每次最多添加 3 个文件。');
      const added=await Promise.all(files.map(readAttachment));
      if(generation!==attachmentGeneration.current)return;
      composeMessage(input,[...attachments,...added]);setAttachments(value=>[...value,...added]);
    }catch(e){if(generation===attachmentGeneration.current)handleError(e)}finally{setReading(false)}
  }
  function clearAttachments(){attachmentGeneration.current++;setAttachments([])}
  async function send(text,turnId=null){
    if(reading||busy||pending)return;
    if(text===undefined){try{text=composeMessage(input,attachments)}catch(e){handleError(e);return}
      if(!text)return;setInput(text);clearAttachments();
    }
    if(!text.trim()||busy||pending)return;setBusy(true);setError('');
    try{let id=active;if(!id){const created=await request('/chat-api/conversations',{method:'POST',headers:csrf()});id=created.id;setActive(id)}
      const delivery=submission(outstanding.current.get(id),id,text.trim(),turnId,()=>crypto.randomUUID());outstanding.current.set(id,delivery);
      await request(`/chat-api/conversations/${id}/messages`,{method:'POST',headers:csrf(),body:JSON.stringify({content:delivery.content,turn_id:delivery.turnId})});outstanding.current.delete(id);setInput('');await loadList();setMobile(false)
    }catch(e){handleError(e)}finally{setBusy(false)}
  }
  async function logout(){
    if(loggingOut)return;setLoggingOut(true);setError('');
    try{const result=await request('/auth/logout',{method:'POST',headers:csrf(),signal:AbortSignal.timeout(15000)});
      if(!result.logged_out)throw new Error('退出尚未确认，请重试。');
      outstanding.current.clear();clearAttachments();
      window.location.replace('/?signed_out=1');
    }catch(e){setLoggingOut(false);setError(e.name==='TimeoutError'?'退出尚未确认，请检查连接后重试。':e.message)}
  }
  async function archive(id){try{await request(`/chat-api/conversations/${id}`,{method:'DELETE',headers:csrf()});outstanding.current.delete(id);if(active===id)setActive(null);await loadList()}catch(e){handleError(e)}}
  async function saveTitle(){try{await request(`/chat-api/conversations/${rename}`,{method:'PATCH',headers:csrf(),body:JSON.stringify({title:newTitle})});setRename(null);await loadList()}catch(e){handleError(e)}}
  if(loggingOut)return <div className="loading-screen" role="status"><Mark/><div><strong>正在退出登录…</strong><p>正在清除登录状态，请稍候。</p></div></div>;
  const selected=conversations.find(c=>c.id===active);
  const suggestions=[['梳理思路','帮我梳理一个想法，先通过几个问题了解我的目标。'],['制定计划','帮我把今天的工作拆解成一份清晰的行动计划。'],['写作助手','我想写一份项目说明，请先帮我搭建结构。']];
  return <div className="workspace"><aside className={'sidebar '+(mobile?'open':'')}><a className="brand" href="/"><Mark small/><strong>Aether</strong></a><button className="new-chat" onClick={()=>{setMemoryView(false);setActive(null);setError('');setInput('');clearAttachments();setMobile(false)}}><Icon name="plus"/>新建对话<span>＋</span></button><button className="memory-nav" onClick={()=>{setMemoryView(true);setMobile(false)}}><Icon name="book"/> 我的记忆</button><label className="search"><Icon name="search" size={16}/><input aria-label="搜索会话" placeholder="搜索对话" value={query} onChange={e=>setQuery(e.target.value)}/></label><div className="sidebar-label">你的对话</div><nav className="conversation-list" aria-label="会话列表">{conversations.filter(c=>c.title.toLowerCase().includes(query.toLowerCase())).map(c=><div key={c.id} className={'conversation '+(active===c.id?'selected':'')}><button className="conversation-main" onClick={()=>{setMemoryView(false);setActive(c.id);setMobile(false);setError('');clearAttachments()}}><Icon name="chat" size={16}/><span>{c.title}</span></button><button className="conversation-action" title="重命名对话" aria-label={`重命名 ${c.title}`} onClick={()=>{setRename(c.id);setNewTitle(c.title)}}><Icon name="edit" size={14}/></button><button className="conversation-action" title="从列表移除" aria-label={`归档 ${c.title}`} onClick={()=>archive(c.id)}><Icon name="trash" size={14}/></button></div>)}{conversations.length===0&&<p className="empty-list">新的想法，会从这里开始。</p>}</nav><div className="sidebar-bottom"><div className="private-note"><Icon name="check" size={15}/> 你的对话仅自己可见</div><div className="account"><span className="avatar">{profile.display_name?.[0]||'我'}</span><div><strong>{profile.display_name||profile.user_id}</strong><small>个人工作空间</small></div><button className="logout-button" aria-label="退出登录" title="退出当前账号" onClick={logout}><Icon name="exit" size={17}/><span>退出登录</span></button></div></div></aside>{mobile&&<button aria-label="关闭导航" className="mobile-backdrop" onClick={()=>setMobile(false)}/>}
    <main className="chat-main">{memoryView?<MemoryCenter userId={profile.user_id} request={request} csrf={csrf} sessionId={active} onBack={()=>setMemoryView(false)}/>:<><header className="chat-header"><button className="icon-button mobile-menu" aria-label="打开导航" onClick={()=>setMobile(true)}><Icon name="menu"/></button><div className="chat-title"><strong>{selected?.title||'Aether 助手'}</strong><span><i/> 已登录</span></div><button className={'quiet '+(sources?'active':'')} onClick={()=>setSources(!sources)}><Icon name="book" size={17}/> 来源与记忆</button></header>
    <div className="chat-scroll">{!messages.length?<div className="welcome"><Mark/><div className="eyebrow">你的专属 AI 助手</div><h1>你好，{(profile.display_name||'朋友').replace('（测试）','')}。<br/><span>今天，我们一起解决什么？</span></h1><p>从一个问题、一段文字，或一个还不完整的想法开始。</p><div className="suggestions">{suggestions.map(([title,prompt],i)=><button key={title} onClick={()=>{setInput(prompt);textarea.current?.focus()}}><span className={'suggestion-icon s'+i}><Icon name={['chat','check','edit'][i]}/></span><strong>{title}</strong><span>{['把零散的想法整理清楚','将目标拆解为具体行动','找到更清晰的表达'][i]}</span><Icon name="arrow" size={14}/></button>)}</div></div>:<div className="messages" aria-live="polite">{messages.map(m=><article key={m.id} className={'message '+m.role}><div className="message-avatar">{m.role==='assistant'?<Mark small/>:<span>{profile.display_name?.[0]||'我'}</span>}</div><div className="message-body"><div className="message-author">{m.role==='assistant'?'Aether':profile.display_name||'你'}</div>{m.status==='pending'?<>{m.content&&<Content text={m.content}/>}<div className="thinking"><span/><span/><span/><small>{replyPhase(m)}</small></div></>:m.status==='failed'?<div className="reply-error">{m.content&&<Content text={m.content}/>}<p>{m.error}</p><button className="quiet" onClick={()=>send(messages.find(x=>x.turn_id===m.turn_id&&x.role==='user')?.content,m.turn_id)}>重试本次回复</button></div>:<Content text={m.content}/>} {m.role==='assistant'&&m.status==='complete'&&<div className="message-tools"><button onClick={async()=>{try{await navigator.clipboard.writeText(m.content);setCopied(m.id);setTimeout(()=>setCopied(null),1500)}catch{setError('复制未完成，请直接选择文字复制。')}}}>{copied===m.id?'已复制':'复制回复'}</button><span>{m.memory_evidence?`P3 ${m.sources?.length?"已召回 "+m.sources.length+" 条记忆":"未命中历史记忆"} · ${m.memory_evidence.saved?"本轮已保存":m.memory_status==='save_failed'?"保存失败，回答已保留":"记忆正在后台保存"}`:"基于当前会话"}</span></div>}</div></article>)}<div ref={bottom}/></div>}</div>
    <div className="composer-area">{error&&<div className="error-banner" role="alert">{error}{outstanding.current.has(active)&&<button onClick={()=>{const delivery=outstanding.current.get(active);send(delivery.content,delivery.turnId)}}>重试发送原消息</button>}<button onClick={()=>setReloadTick(value=>value+1)}>刷新对话</button><button aria-label="关闭提示" onClick={()=>setError('')}><Icon name="close" size={14}/></button></div>}<form className="composer" onSubmit={e=>{e.preventDefault();send()}}>{attachments.length>0&&<div className="attachment-list" aria-label="待发送附件">{attachments.map((file,index)=><div className="attachment-chip" key={index}><Icon name="attach" size={17}/><div><strong>{file.name}</strong><small>{Math.max(1,Math.ceil(file.size/1024))} KB · 已读取，待发送</small></div><button type="button" aria-label={`移除 ${file.name}`} disabled={busy||reading} onClick={()=>setAttachments(value=>value.filter((_,i)=>i!==index))}><Icon name="close" size={15}/></button></div>)}</div>}<textarea ref={textarea} aria-label="发送消息" placeholder="向 Aether 提问…" value={input} maxLength={12000} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send()}}}/><div className="composer-bottom"><div className="composer-actions"><input hidden ref={fileInput} type="file" multiple accept=".txt,.md,.csv,text/plain,text/markdown,text/csv" aria-label="选择附件" onChange={selectFiles}/><button type="button" className="attach-button" aria-label="添加文件" title="添加 TXT、Markdown、CSV；最多 3 个，每个 100 KB" disabled={busy||pending||reading||attachments.length>=3} onClick={()=>fileInput.current?.click()}><Icon name="attach" size={19}/><span>{reading?'读取中…':'添加文件'}</span></button><span className="context-label"><i/> 当前会话上下文</span></div><button className="send-button" aria-label="发送" type="submit" disabled={(!input.trim()&&!attachments.length)||busy||pending||reading}><Icon name="send" size={21}/></button></div><p className="attachment-help">文本附件随消息发送；PDF / Word 请在「我的记忆」中导入。</p></form><p className="composer-note">Enter 发送 · Shift + Enter 换行<span>AI 的回答可能存在偏差，请核实重要信息。</span></p></div></>}</main>
    {sources&&!memoryView&&<aside className="sources-panel"><header><strong>来源与记忆</strong><button className="icon-button" aria-label="关闭来源面板" onClick={()=>setSources(false)}><Icon name="close"/></button></header><span className="source-symbol"><Icon name="book" size={30}/></span>{(()=>{const last=[...messages].reverse().find(m=>m.role==='assistant');const proof=last?.memory_evidence;return proof?<><h3>本轮已调用 P3</h3><p>{proof.outcome==='degraded'?'本次检索不完整，以下仅为已核验的部分参考记忆。':proof.outcome==='empty'?'本次没有召回相关历史记忆。':`召回了 ${last.sources?.length||0} 条参考记忆。`}</p><div className="source-status">{proof.saved?'本轮消息已保存到 P3':proof.status==='save_failed'?'保存失败，回答已保留':'记忆正在后台保存'} · {proof.status==='ready'?'可检索':'后台处理中'}</div>{last.sources?.map((item,i)=><div className="source-card" key={i}><strong>来源 {i+1}</strong><p>{item.content}</p><small>版本 {item.memory.version}</small></div>)}{proof.source_excerpts?.map((item,i)=><div className="source-card" key={"original"+i}><strong>来源原文 {i+1}</strong><p>{item.excerpt}</p><small>{item.truncated?"原文片段，未读取全文":"已读取此来源全文"}</small></div>)}<p className="muted">来源是生成本轮回复时的记录。更正或删除后，以记忆服务重新核验的结果为准。</p><button className="quiet" onClick={async()=>{try{await request('/memory-api/recalls/'+proof.recall_id);setError('来源核验通过。')}catch(e){setError(e.message)}}}>重新核验来源</button></>:<><h3>当前会话上下文</h3><p>这条历史回复没有 P3 调用回执。新消息的保存和召回状态将在完成后显示。</p></>})()}<button className="primary" onClick={()=>{setMemoryView(true);setSources(false)}}>打开我的记忆</button></aside>}
    {rename&&<div className="modal-backdrop"><form className="dialog" role="dialog" aria-modal="true" aria-label="重命名对话" onSubmit={e=>{e.preventDefault();saveTitle()}}><h2>重命名对话</h2><label>对话名称<input autoFocus value={newTitle} maxLength={80} onChange={e=>setNewTitle(e.target.value)}/></label><div><button type="button" className="quiet" onClick={()=>setRename(null)}>取消</button><button className="primary" disabled={!newTitle.trim()}>保存名称</button></div></form></div>}
  </div>
}
createRoot(document.getElementById('root')).render(<App/>);
