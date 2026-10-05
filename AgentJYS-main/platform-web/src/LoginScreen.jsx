import React, {useState} from 'react';
import {apiUrl} from './api-url.mjs';

export default function LoginScreen({Mark,onSuccess}) {
  const [username,setUsername]=useState(''),[password,setPassword]=useState('');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[visible,setVisible]=useState(false);
  async function submit(event){
    event.preventDefault();if(busy)return;setBusy(true);setError('');
    try{
      const response=await fetch(apiUrl('/auth/login'),{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:username.trim(),password})});
      const body=await response.json();
      if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:'登录未完成，请稍后重试。');
      setPassword('');await onSuccess();
    }catch(e){setError(e.message||'无法连接登录服务，请稍后重试。');setPassword('')}
    finally{setBusy(false)}
  }
  return <div className="login-page"><header className="brand"><Mark small/><strong>Aether</strong><span>AI 工作空间</span></header>
    <main className="login-content"><section className="login-story"><div className="eyebrow">想法，从这里展开</div><h1>从一个问题，<br/>开始。</h1><p>梳理思路、拆解任务、完成创作。<br/>把注意力留给重要的事情。</p><div className="login-example"><div><Mark small/><span>Aether 助手</span></div><p>今天，我们一起解决什么？</p><span className="example-chip">整理一个想法</span><span className="example-chip">制定行动计划</span></div></section>
    <section className="login-card"><div className="eyebrow">欢迎回来</div><h2>登录你的工作空间</h2><p>输入账号和密码，继续你的对话。</p>
      {new URLSearchParams(location.search).get('signed_out')==='1'&&<div className="logout-notice" role="status">已退出当前账号</div>}
      <form className="agent-login-form" onSubmit={submit}>
        <label htmlFor="agent-username">账号<input id="agent-username" name="username" autoComplete="username" placeholder="请输入账号" required maxLength={150} value={username} onChange={e=>setUsername(e.target.value)} disabled={busy}/></label>
        <label htmlFor="agent-password">密码<span className="password-input"><input id="agent-password" name="password" type={visible?'text':'password'} autoComplete="current-password" placeholder="请输入密码" required maxLength={1024} value={password} onChange={e=>setPassword(e.target.value)} disabled={busy}/><button type="button" className="password-toggle" aria-label={visible?'隐藏密码':'显示密码'} onClick={()=>setVisible(!visible)}>{visible?'隐藏':'显示'}</button></span></label>
        {error&&<div role="alert" className="error-banner">{error}</div>}
        <button className="primary login-button" disabled={busy||!username.trim()||!password}>{busy?'正在登录…':'登录并开始对话'}<span aria-hidden="true">→</span></button>
      </form><div className="account-help">还没有账号？请联系你的管理员开通。</div>
    </section></main><footer>Aether · 让思路更清晰<span>专属账号 / 会话历史 / 清晰来源</span></footer></div>
}
