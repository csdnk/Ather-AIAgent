import React,{useEffect,useRef,useState} from 'react';

const names={working:'工作记忆',episodic:'经历记忆',semantic:'知识记忆',active:'有效',archived:'已归档',superseded:'已更正',deleted:'已删除',expired:'已过期',pending:'待处理',building:'处理中',ready:'可检索',failed:'失败',stale:'待更新',saved:'已保存',processing:'处理中'};
const actions=[['save','保存记忆'],['recall','检索记忆'],['consolidate','整理为长期记忆'],['distill','提炼知识'],['correct','更正内容'],['archive','归档'],['activate','恢复使用'],['delete','删除记忆'],['reprocess','重新处理'],['reindex','重建索引'],['retention','保留策略'],['reflection','自动反思'],['source_revoke','撤销来源'],['source_delete','删除来源']];
export default function MemoryCenter({request,csrf,sessionId,onBack,userId}){
  const [items,setItems]=useState([]),[cursor,setCursor]=useState(null),[selected,setSelected]=useState(null),[action,setAction]=useState('save'),[text,setText]=useState(''),[reason,setReason]=useState('用户主动维护'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState(null),[connected,setConnected]=useState(null),[useSession,setUseSession]=useState(false),[enabled,setEnabled]=useState(true),[hours,setHours]=useState(168),[tasks,setTasks]=useState([]),[reflection,setReflection]=useState(null);
  const pendingKey='aether-p3-pending:'+userId;
  const uploadKey='aether-p3-upload:'+userId;
  const priorUpload=(()=>{try{return JSON.parse(sessionStorage.getItem(uploadKey)||'null')}catch{return null}})();
  const priorId=sessionStorage.getItem(pendingKey);
  const pending=useRef(priorId?{body:{command_id:priorId},signature:null}:null),upload=useRef(null),fileRetry=useRef(priorUpload);
  function clearUpload(){fileRetry.current=null;sessionStorage.removeItem(uploadKey)}
  function clearPending(){pending.current=null;sessionStorage.removeItem(pendingKey)}
  async function resume(){setBusy(true);setError('');try{const data=await request('/memory-api/commands/'+pending.current.body.command_id+'/retry',{method:'POST',headers:csrf()});clearPending();setResult(data.result);captureTasks(data.result);await load()}catch(e){if([400,401,403,404,409,410,422].includes(e.status))clearPending();setError(e.message)}finally{setBusy(false)}}
  const [extraPolicy,setExtraPolicy]=useState({}),[sourceIndex,setSourceIndex]=useState(0),[chosen,setChosen]=useState([]),[rangeStart,setRangeStart]=useState(0),[rangeEnd,setRangeEnd]=useState(''),[category,setCategory]=useState('fact'),[recallSource,setRecallSource]=useState('long_term');
  async function load(more=false){try{const data=await request('/memory-api/memories'+(more&&cursor?'?cursor='+encodeURIComponent(cursor):''));setItems(old=>more?[...old,...data.items]:data.items);setCursor(data.next_cursor)}catch(e){setError(e.message)}}
  useEffect(()=>{request('/memory-api/status').then(data=>setConnected(data.connected)).catch(e=>{setConnected(false);setError(e.message)});load()},[]);
  async function select(item){setBusy(true);setError('');try{const detail=await request('/memory-api/memories/'+item.ref.memory_id);setSelected(detail);setText(detail.content);setResult(null)}catch(e){setError(e.message)}finally{setBusy(false)}}
  function captureTasks(value){setTasks(value?.task_ids|| (value?.task_id?[value.task_id]:[]))}
  async function submit(){
    if(busy)return;
    const needsMemory=!['save','recall','consolidate','reflection'].includes(action);
    if(needsMemory&&!selected){setError('请先从列表选择一条记忆。');return}
    if(['delete','source_delete','source_revoke'].includes(action)&&!window.confirm('确定执行'+actions.find(x=>x[0]===action)[1]+'？这会影响之后的记忆检索。'))return;
    const versions=selected?{expected_version:selected.ref.version,expected_object_revision:selected.object_revision}:{};
    let params={};
    if(['save','recall'].includes(action))params={text,...(action==='recall'?{sources:recallSource}:{category})};
    if(action==='correct')params={...versions,content:text,reason};
    if(['archive','activate'].includes(action))params={...versions,reason};
    if(action==='delete')params={expected_revision:selected.object_revision,reason};
    if(['source_delete','source_revoke'].includes(action))params={expected_revision:selected.source_metadata?.[sourceIndex]?.revision,source_index:Number(sourceIndex),reason};
    if(action==='retention')params={...versions,enabled,archive_after_idle_hours:Number(hours),...extraPolicy,reason};
    if(action==='reflection')params={enabled,expected_revision:reflection?.revision||0,...extraPolicy,reason};
    const body={action,params,...(action==='distill'&&chosen.length?{memory_ids:chosen}:{}),...(needsMemory?{memory_id:selected.ref.memory_id}:{}),...(useSession&&sessionId?{session_id:sessionId}:{})};
    const signature=JSON.stringify(body);
    if(pending.current&&pending.current.signature!==signature){setError('上次操作尚未确认，请恢复原参数重试，或在操作记录中核对结果后再继续。');return}
    if(!pending.current){pending.current={signature,body:{...body,command_id:crypto.randomUUID()}};sessionStorage.setItem(pendingKey,pending.current.body.command_id)}
    setBusy(true);setError('');
    try{const data=await request('/memory-api/commands',{method:'POST',headers:csrf(),body:JSON.stringify(pending.current.body)});clearPending();setResult(data.result);captureTasks(data.result);await load();if(selected&&action!=='delete'){try{setSelected(await request('/memory-api/memories/'+selected.ref.memory_id))}catch{setSelected(null)}}}
    catch(e){if([400,401,403,404,409,410,422].includes(e.status))clearPending();setError(e.message)}finally{setBusy(false)}
  }
  async function read(view){if(!selected)return;setBusy(true);setError('');try{const data=await request('/memory-api/memories/'+selected.ref.memory_id+'/'+view+(['range','source'].includes(view)?'?start='+Number(rangeStart)+(rangeEnd!==''?'&end='+Number(rangeEnd):''):''));setResult(data);captureTasks(data)}catch(e){setError(e.message)}finally{setBusy(false)}}
  async function records(){try{setResult(await request('/memory-api/commands'))}catch(e){setError(e.message)}}
  async function importFile(event){
    const file=event.target.files?.[0];event.target.value='';if(!file)return;
    const types={txt:'text/plain',md:'text/markdown',csv:'text/csv',pdf:'application/pdf',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'};
    const media=types[file.name.split('.').pop().toLowerCase()];
    if(!media||file.size>5*1024*1024){setError('请选择不超过 5 MB 的 TXT、Markdown、CSV、PDF 或 DOCX。');return}
    if(fileRetry.current&&(fileRetry.current.name!==file.name||fileRetry.current.size!==file.size||fileRetry.current.modified!==file.lastModified)){setError('上次文档导入尚未确认，请重新选择原文件重试。');return}
    if(!fileRetry.current){fileRetry.current={id:crypto.randomUUID(),name:file.name,size:file.size,modified:file.lastModified};sessionStorage.setItem(uploadKey,JSON.stringify(fileRetry.current))}
    setBusy(true);setError('');
    try{const data=await request('/memory-api/documents',{method:'POST',headers:{...csrf(),'Content-Type':media,'X-Upload-ID':fileRetry.current.id},body:file});clearUpload();setResult(data.result);captureTasks(data.result);await load()}
    catch(e){if([413,415,422].includes(e.status))clearUpload();setError(e.message)}finally{setBusy(false)}
  }
  return <section className="memory-center"><header className="memory-header"><div><button className="quiet" onClick={onBack}>← 返回对话</button><h1>我的记忆</h1><p>保存、查找和维护属于你的记忆。<span className={connected?'memory-online':''}>{connected?' P3 已连接':connected===null?' 正在检查 P3 连接':' P3 暂时不可用'}</span></p></div><button className="quiet" onClick={()=>load()}>刷新列表</button></header>
    <div className="memory-layout"><aside className="memory-catalog"><h3>记忆目录 <small>{items.length}</small></h3>{items.map(item=><button key={item.ref.memory_id} className={'memory-item '+(selected?.ref.memory_id===item.ref.memory_id?'selected':'')} onClick={()=>select(item)}><strong>{names[item.kind]||item.kind}</strong><span>{names[item.status]||item.status} · {names[item.projection_state]||item.projection_state}</span><small>{new Date(item.created_at).toLocaleString('zh-CN')}</small><small>{item.ref.memory_id.slice(0,18)}…</small></button>)}{!items.length&&<p className="muted">还没有可见记忆。可以保存一条，或在对话中开始记录。</p>}{cursor&&<button className="quiet" onClick={()=>load(true)}>加载更多</button>}</aside>
    <main className="memory-detail">{selected&&<div className="memory-snapshot"><div className="memory-badges"><span>{names[selected.kind]}</span><span>{names[selected.status]}</span><span>版本 {selected.ref.version}</span><span>{names[selected.projection_state]}</span></div><p>{selected.content}</p><div className="memory-tools">{[['body','查看全文'],['range','分段读取'],['source','来源原文'],['processing','处理状态'],['retention','当前保留策略']].map(([view,label])=><button className="quiet" key={view} disabled={busy} onClick={()=>read(view)}>{label}</button>)}</div></div>}
    <div className="memory-form"><div className="memory-tools"><h2>记忆操作</h2><input hidden ref={upload} type="file" accept=".txt,.md,.csv,.pdf,.docx" aria-label="选择记忆文档" onChange={importFile}/><button className="quiet" disabled={busy} onClick={()=>upload.current?.click()}>导入文档</button></div><p className="memory-hint">文档导入 P3：TXT、Markdown、CSV、PDF、DOCX，最大 5 MB；扫描件尚未支持 OCR。</p>{fileRetry.current&&<p className="memory-hint">{busy?"正在导入文档「"+fileRetry.current.name+"」，请等待回执。":"上次文档「"+fileRetry.current.name+"」尚待确认，请点击导入文档并重新选择原文件，将继续原操作。"}</p>}<label>操作<select value={action} onChange={e=>{setAction(e.target.value);setExtraPolicy({});setError('')}}>{actions.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
    {action==='save'&&<label>记忆类别<select value={category} onChange={e=>setCategory(e.target.value)}>{[['fact','事实'],['event','经历事件'],['decision','决策'],['explicit_constraint','明确约束'],['observation','普通观察']].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>}{action==='recall'&&<label>检索来源<select value={recallSource} onChange={e=>setRecallSource(e.target.value)}>{[['long_term','长期记忆'],['working','当前会话的工作记忆'],['both','工作与长期记忆'],['auto','自动选择']].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>}
    {['save','recall','correct'].includes(action)&&<label>{action==='recall'?'想查找什么？':'内容'}<textarea value={text} onChange={e=>setText(e.target.value)} maxLength={12000} placeholder="例如：我喜欢蓝莓，不喜欢草莓。"/></label>}
    {!['save','recall','distill','consolidate','reindex','reprocess'].includes(action)&&<label>原因<input value={reason} onChange={e=>setReason(e.target.value)}/></label>}
    {['save','recall','consolidate','reflection'].includes(action)&&sessionId&&<label className="memory-check"><input type="checkbox" checked={useSession} onChange={e=>setUseSession(e.target.checked)}/>仅当前会话{action==='recall'?'（工作记忆或混合检索时必选）':''}</label>}
    {['reflection','retention'].includes(action)&&<label className="memory-check"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>启用策略</label>}
    {action==='distill'&&<fieldset><legend>选择经历记忆（可多选）</legend>{items.filter(x=>x.kind==='episodic'&&x.status==='active').map(x=><label className="memory-check" key={x.ref.memory_id}><input type="checkbox" checked={chosen.includes(x.ref.memory_id)} onChange={e=>setChosen(old=>e.target.checked?[...old,x.ref.memory_id]:old.filter(id=>id!==x.ref.memory_id))}/>{x.ref.memory_id.slice(0,16)} · {new Date(x.created_at).toLocaleString('zh-CN')}</label>)}</fieldset>}
    {['source_delete','source_revoke'].includes(action)&&selected&&<label>要处理的来源<select value={sourceIndex} onChange={e=>setSourceIndex(Number(e.target.value))}>{selected.sources.map((s,i)=><option value={i} key={s.source_id}>来源 {i+1} · {s.source_id.slice(0,16)}</option>)}</select></label>}
    {selected&&<details><summary>分段读取范围</summary><label>起始字符<input type="number" min="0" value={rangeStart} onChange={e=>setRangeStart(e.target.value)}/></label><label>结束字符（留空读取到末尾）<input type="number" min="0" value={rangeEnd} onChange={e=>setRangeEnd(e.target.value)}/></label></details>}
    {['retention','reflection'].includes(action)&&<details><summary>高级策略设置</summary>{(action==='reflection'?[['min_episodes','最少经历数',2],['max_episodes','最多经历数',8],['importance_threshold','重要度阈值',0.8],['min_reinforcements','最少强化次数',3],['period_hours','检查周期（小时）',24]]:[['delete_after_archive_hours','归档后删除等待小时（留空不自动删除）',''],['delete_below_strength','删除强度阈值',0.05]]).map(([key,label,initial])=><label key={key}>{label}<input type="number" step="any" value={extraPolicy[key]??initial} onChange={e=>setExtraPolicy(old=>({...old,[key]:e.target.value===''?null:Number(e.target.value)}))}/></label>)}{action==='retention'&&<><label className="memory-check"><input type="checkbox" checked={extraPolicy.legal_hold||false} onChange={e=>setExtraPolicy(old=>({...old,legal_hold:e.target.checked}))}/>保留锁定</label><label className="memory-check"><input type="checkbox" checked={extraPolicy.completed||false} onChange={e=>setExtraPolicy(old=>({...old,completed:e.target.checked}))}/>相关事项已完成</label><label>到期时间<input type="datetime-local" onChange={e=>setExtraPolicy(old=>({...old,expires_at:e.target.value?new Date(e.target.value).toISOString():null}))}/></label></>}</details>}
    {action==='retention'&&<label>闲置多少小时后归档<input type="number" min="24" max="87600" value={hours} onChange={e=>setHours(e.target.value)}/></label>}
    {action==='reflection'&&<button className="quiet" onClick={async()=>{try{const data=await request('/memory-api/reflection'+(useSession&&sessionId?'?session_id='+sessionId:''));setReflection(data);setResult(data)}catch(e){setError(e.message)}}}>读取当前反思设置</button>}
    <p className="memory-hint">工作记忆启用保留策略前需勾选「相关事项已完成」。保存成功与可检索是不同状态；整理、提炼、重建索引等任务完成后，请刷新处理状态。</p>
    {error&&<div className="error-banner" role="alert">{error}</div>}<div className="memory-tools"><button className="primary" disabled={busy} onClick={submit}>{busy?'正在处理…':pending.current?'重试原操作':actions.find(x=>x[0]===action)[1]}</button><button className="quiet" onClick={records}>操作记录</button>{pending.current&&<button className="quiet" disabled={busy} onClick={resume}>继续上次提交</button>}</div></div>
    {result&&<div className="memory-result"><h3>操作结果</h3>{result.saved&&<p>已保存到 P3 · {names[result.phase]||result.phase}</p>}{result.outcome&&<p>{result.outcome==='empty'?'没有找到符合条件的记忆':result.outcome==='available'?'已找到相关记忆':'部分结果可用'}</p>}{result.rendered_context&&<p>{result.rendered_context}</p>}{result.blocked&&<p>已阻止后续访问；清理状态：{result.cleanup_state}</p>}{result.content&&<p>{result.content}</p>}{tasks.map(id=><button className="quiet" key={id} onClick={async()=>{try{setResult(await request('/memory-api/operations/'+id))}catch(e){setError(e.message)}}}>查询任务 {id.slice(0,12)}…</button>)}{result.state&&<p>状态：{names[result.state]||result.state}</p>}<details><summary>查看完整回执</summary><pre>{JSON.stringify(result,null,2)}</pre></details></div>}
    </main></div></section>
}
