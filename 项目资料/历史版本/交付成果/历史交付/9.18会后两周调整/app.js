'use strict';
const model=new ReviewModel();
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={saved:'已保存',ready:'可长期检索',processing_failed:'加工失败',complete:'完整可用',empty:'正常为空',degraded:'降级可用',unavailable:'不可用',logically_deleted:'逻辑删除已生效',cleanup_completed:'模拟清理完成',conflict:'冲突，未执行',rejected:'请求被拒绝',blocked:'处理被阻断',reconciled:'已核对原动作',no_pending_action:'无待核对动作',inspected:'三引擎关联结果',failed:'操作失败',not_found:'未找到原操作'};
const tierLabels={warm:'温层',hot:'热层',cold:'冷层'};
const stateLabels={active:'有效',superseded:'被替代',deleted:'已删除'};
const projectionLabels={pending:'待处理',failed:'加工失败',ready:'可长期检索',invalid:'资格失效'};
let scenarioIndex=0,stepIndex=0,inspectTab='state',ctx={},manualKey=null,manualLastSignature=null;
const baseBudget='本阶段预算上限为20万元。';
function seed(text=baseBudget,kind='working',source='项目约束记录'){const r=model.write(text,{kind,source});model.process(r.memory_id);return r.memory_id;}
const scenarios=[
 {id:'S01',title:'保存 → 当前使用 → 长期复用',short:'先记住，再逐步就绪',note:'存储与加工分开确认',question:'保存成功后，现在能用什么？什么时候才可以跨会话长期检索？',fr:'FR01 · FR02 · FR07',adrs:[1,2],setup(){},steps:[
  ['提交项目约束','保存预算与汇报安排。观察“已保存”和“长期待处理”同时出现。',()=>{ctx.memory=model.write('本阶段预算上限为20万元，每周五汇总进度。',{key:'demo-first',source:'第一次项目会话'}).memory_id;}],
  ['立即读取当前记忆','长期加工尚未完成，当前任务仍能读取有效约束。',()=>model.recall({sources:'working',session:'session-1'})],
  ['推进后台加工','观察对象、向量引用核对后，当前版本进入长期检索范围。',()=>model.process(ctx.memory)],
  ['在新会话召回','同一可信任务范围内，通过统一 Recall 取得历史与来源。',()=>model.recall({sources:'long_term',session:'session-2'})]
 ]},
 {id:'S02',title:'预算从 20 万更正为 15 万',short:'新事实生效，旧结果退出',note:'版本与历史结果',question:'内容改了，旧索引和已经生成的上下文会不会继续交付旧预算？',fr:'FR05 · FR07 · FR11',adrs:[3,4],setup(){ctx.memory=seed();},steps:[
  ['先召回旧预算','保留这次结果引用，后面测试旧结果是否还能再次使用。',()=>{ctx.result=model.recall({query:'当前预算是多少？'}).recall_id;}],
  ['显式更正为 15 万','同一 memory 创建 v2；v1 退出资格，新版投影重新准备。',()=>model.update(ctx.memory,'本阶段预算上限为15万元。',1)],
  ['查询当前预算','返回有效的 15 万；不能把 v1 的长期就绪状态授予 v2。',()=>model.recall({query:'当前预算是多少？'})],
  ['再次获取旧结果','旧 Context 含已替代版本，重新获取必须被阻断。',()=>model.replay(ctx.result)],
  ['完成新版长期准备','v2 经模拟查询与回读核对后，才能独立获得长期资格。',()=>model.process(ctx.memory)]
 ]},
 {id:'S03',title:'后台加工失败，原文仍然保留',short:'向量故障与任务恢复',note:'失败不能抹掉已保存事实',question:'长期加工失败时，接入方能分清已保存内容和未完成目标吗？',fr:'FR03 · FR07 · FR18',adrs:[2,8],setup(){ctx.memory=model.write('项目会议记录：客户要求每周五汇总进度；本周先完成接口样例核对。',{kind:'episodic',source:'会议纪要样例'}).memory_id;},steps:[
  ['模拟向量写入失败','固定文本加工，不运行长文解析或模型提取；原文仍在对象替身中。',()=>{model.fault='vector';model.process(ctx.memory);}],
  ['尝试长期召回','所需来源不可用，应说明失败，不能返回“正常空”。',()=>model.recall({sources:'long_term',query:'会议有哪些要求？'})],
  ['恢复依赖并重试原目标','保持原记忆和版本，推进同一目标的加工。',()=>{model.fault='none';model.process(ctx.memory);}],
  ['确认长期结果可读取','核对返回内容、来源和当前版本。',()=>model.recall({sources:'long_term',query:'会议有哪些要求？'})]
 ]},
 {id:'S04',title:'四种召回结果，含义清楚',short:'完整 / 空 / 降级 / 不可用',note:'不把故障包装成无结果',question:'面对缺失来源和有限预算，接入方应该继续、等待还是修改请求？',fr:'FR08 · FR10',adrs:[3],setup(){seed();seed('每周五汇总项目进度。','episodic','会议纪要');},steps:[
  ['来源齐全：完整可用','两种来源正常，返回合格内容及来源。',()=>model.recall()],
  ['长期服务故障：降级可用','保留独立可信的当前记忆，并明确长期来源缺失。',()=>{model.fault='vector';model.recall();}],
  ['来源正常但无匹配：正常空','恢复依赖后查询一个样例中不存在的主题。',()=>{model.fault='none';model.recall({query:'火星气象观测'});}],
  ['所有材料超预算：不可用','预算改为 8 字符；整组放不下，不能截断，也不能报正常空。',()=>model.recall({budget:8})]
 ]},
 {id:'S05',title:'删除先阻断交付，再跟踪清理',short:'历史上下文也要重新核验',note:'逻辑失效与物理清理分开',question:'删除之后，新请求、旧结果和迟到的后台处理会不会把内容重新带回来？',fr:'FR11 · FR17',adrs:[3,4],setup(){ctx.memory=seed('会议要求：每周五汇总进度。','episodic','待删除会议记录');},steps:[
  ['形成一份历史上下文','先保存旧 Recall 引用。',()=>{ctx.result=model.recall({sources:'long_term'}).recall_id;}],
  ['删除这条会议记忆','立即阻断新使用，清理仍显示待处理。',()=>model.remove(ctx.memory)],
  ['发起新的召回','所选来源正常但无合格内容，应返回正常空。',()=>model.recall({sources:'long_term'})],
  ['再次获取旧上下文','旧结果不能绕过删除规则重新交付。',()=>model.replay(ctx.result)],
  ['模拟迟到加工','旧任务不能让已删除对象重新 Ready。',()=>model.process(ctx.memory)],
  ['核对模拟清理','移除浏览器中的对象与向量，不声称已擦除真实备份或日志。',()=>model.cleanup(ctx.memory)]
 ]},
 {id:'S06',title:'调度在后台运行，结果要核对',short:'回执丢失不等于执行失败',note:'两侧轨迹 → 决策 → 结果',question:'调用方没有调度按钮时，怎样知道内部确实进行了判断和执行？',fr:'FR12 · FR13 · FR14',adrs:[5,6],setup(){ctx.memory=seed();inspectTab='schedule';},steps:[
  ['第一次使用项目记忆','预置存储已产生判断记录；本次召回再增加一次交付事实。',()=>{model.fault='ack';model.recall();}],
  ['第二次使用，触发样例规则','后台自动尝试温→热；模拟执行端完成，但回执丢失，P3 保持 Unknown。',()=>model.recall()],
  ['推进后台结果核对','按原动作 ID 查询目标和准确正文。动作数量不应增加。',()=>{model.fault='none';model.reconcile();}],
  ['再次召回，观察读取层级','结果应显示已确认热层；记忆仍是原来的类型与版本。',()=>model.recall()]
 ]},
 {id:'S07',title:'P2 三引擎的能力如何被消费',short:'对象 / 向量 / 图引用串联',note:'P2 接口消费视角',question:'向量命中的对象、回读的正文和图中的来源，是不是同一个版本？',fr:'P2 E1 · E2 · E3',adrs:[2,6],setup(){},steps:[
  ['写入对象与业务记忆','以同一份项目内容作为三个引擎的关联样例。',()=>{ctx.memory=model.write('项目会议结论：预算上限为20万元。',{source:'meeting-0918'}).memory_id;}],
  ['准备向量与来源关系','创建当前版本引用；图关系仅展示一跳来源，不代表完整图引擎。',()=>model.process(ctx.memory)],
  ['检查三引擎关联输出','显示已有 RPC 名称下的聚合观察结果，不冒充实际 gRPC Schema。',()=>model.p2inspect()],
  ['通过 P3 使用 P2 结果','P2 提供候选和正文，P3 才决定其是否可交付。',()=>model.recall({sources:'long_term'})]
 ]},
 {id:'S08',title:'保留事实冲突，按整组控制长度',short:'放不下首组，继续尝试后组',note:'不靠截断掩盖矛盾',question:'两个有效来源说法不同，长度紧张时会不会只留下其中一个结论？',fr:'FR09 · FR10',adrs:[3],setup(){seed('项目预算上限为20万元。','working','方案记录A');seed('项目预算上限为15万元。','working','方案记录B');seed('每周五汇总进度。','working','执行约定');},steps:[
  ['足够预算：完整表达冲突','两个预算都是独立有效来源，不能将其中一个当作已被替代。',()=>model.recall({budget:400})],
  ['收紧预算：跳过完整冲突组','70 字符放不下冲突组，继续尝试后面的汇报安排。',()=>model.recall({budget:70})],
  ['极小预算：全部无法装入','8 字符连最短完整条目也放不下，明确不可用。',()=>model.recall({budget:8})]
 ]},
 {id:'S09',title:'请求范围不能代替可信授权',short:'跨租户与未授权 Agent',note:'身份替身，不是登录系统',question:'调用方修改请求里的 tenant 或 agent 字段，能不能取到别人的内容？',fr:'FR15',adrs:[7],setup(){seed();},steps:[
  ['伪造其他租户范围','固定演示凭证仅允许 tenant-a，查询会在取候选前被拒绝。',()=>model.recall({scope:{...model.credential,tenant:'tenant-b'}})],
  ['同租户使用未授权 Agent','同租户不代表不同 Agent 自动共享私有记忆。',()=>model.recall({scope:{...model.credential,agent:'other-agent'}})],
  ['使用有效授权范围','在可信范围内正常获得内容。生产须由服务端实现这些校验。',()=>model.recall()]
 ]}
];
function openPage(name){document.querySelectorAll('.page').forEach(e=>e.classList.toggle('active',e.id==='page-'+name));document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.page===name));window.scrollTo({top:0,behavior:'instant'});}
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>openPage(b.dataset.page));
function selectScenario(index){
  scenarioIndex=index;stepIndex=0;ctx={};inspectTab='state';manualKey=null;manualLastSignature=null;model.reset();scenarios[index].setup();model.last=null;
  if(model.events.length)model.log('评审场景','预置数据','以上为本场景模拟前置条件；不是本轮真实业务操作。');
  render();
}
function render(){
  const s=scenarios[scenarioIndex];
  $('scenario-list').innerHTML=scenarios.map((x,i)=>`<button class="scenario-button ${i===scenarioIndex?'active':''}" data-scenario="${i}"><span class="number">${String(i+1).padStart(2,'0')}</span><span><strong>${esc(x.short)}</strong><small>${esc(x.note)}</small></span></button>`).join('');
  document.querySelectorAll('[data-scenario]').forEach(b=>b.onclick=()=>selectScenario(Number(b.dataset.scenario)));
  $('scenario-code').textContent=s.id+' / SCENARIO';$('scenario-title').textContent=s.title;$('scenario-question').textContent=s.question;
  $('scenario-tags').innerHTML=`<span class="tag green">${esc(s.fr)}</span>`+s.adrs.map(n=>`<button class="tag" data-adr-short="${n-1}">ADR ${String(n).padStart(3,'0')} ↗</button>`).join('');
  document.querySelectorAll('[data-adr-short]').forEach(b=>b.onclick=()=>{showADR(Number(b.dataset.adrShort));openPage('adr');});
  $('stepper').innerHTML=s.steps.map((x,i)=>`<div class="step ${i<stepIndex?'done':i===stepIndex?'current':''}" title="${esc(x[0])}"></div>`).join('');
  $('next-title').textContent=stepIndex<s.steps.length?`${stepIndex+1} / ${s.steps.length}  ${s.steps[stepIndex][0]}`:'场景走查完成';
  $('next-help').textContent=stepIndex<s.steps.length?s.steps[stepIndex][1]:'可以查看内部轨迹与关联 ADR，或导出本次模拟记录。';
  $('next').disabled=stepIndex>=s.steps.length;$('next').innerHTML=stepIndex<s.steps.length?'执行这一步 <span>→</span>':'已完成 ✓';
  $('fault').value=model.fault;
  const previous=$('target-memory').value;
  $('target-memory').innerHTML=model.memories.filter(m=>m.state==='active').map(m=>`<option value="${m.id}">${m.id} · v${m.version}</option>`).join('')||'<option value="">暂无有效记忆</option>';
  if([...$('target-memory').options].some(o=>o.value===previous))$('target-memory').value=previous;
  renderResult();renderInspector();
}
$('next').onclick=()=>{const step=scenarios[scenarioIndex].steps[stepIndex];if(!step)return;step[2]();stepIndex++;render();};
$('reset').onclick=()=>selectScenario(scenarioIndex);
function statusClass(status){return ['complete','ready','saved','reconciled','inspected'].includes(status)?'green':['degraded','processing_failed','conflict'].includes(status)?'amber':['unavailable','failed','rejected','blocked'].includes(status)?'red':'';}
function renderResult(){
  const r=model.last;$('result-status').textContent=r?(labels[r.status]||r.status):'等待操作';$('result-status').className='tag '+(r?statusClass(r.status):'');
  if(!r){$('result').innerHTML='<div class="empty-state"><span class="empty-symbol">↗</span><strong>从上方第一步开始</strong><p>每次操作都会改变同一份模拟数据。<br>这里展示接入方获得的结果，右侧解释内部发生了什么。</p></div>';return;}
  let html=`<div class="result-headline">${esc(labels[r.status]||r.status)}${r.idempotent?' · 原操作返回':''}</div>`;
  const notes={saved:'内容已在本地替身中保存，后台加工进度单独表达。',ready:'当前版本已通过模拟引用与回读核对。',complete:'必需来源完整，返回有效内容与可追溯来源。',empty:'所选来源正常完成，没有合格的相关记忆。',degraded:'仍有可信内容可用；缺失的来源已明确列出。',unavailable:'本次无法形成可安全使用的上下文，请根据原因处理。',processing_failed:'长期目标未完成，不抹掉已经保存的原文。',logically_deleted:'后续交付已阻断，后台物理清理尚未完成。',cleanup_completed:'只核对本浏览器内的模拟对象和向量。',reconciled:'沿用原动作核对，没有重新创建冲突调整。',inspected:'P2 接口消费侧的模拟聚合视图。',rejected:'调用方不能通过请求字段扩大演示凭证的授权范围。',blocked:'对象当前状态不允许继续这项处理。'};
  html+=`<p class="result-note">${esc(r.message||notes[r.status]||'本次结果来自本地模拟状态。')}</p>`;
  if(r.memory_id)html+=`<div class="result-meta"><span class="tag">${esc(r.memory_id)}</span>${r.version?`<span class="tag">v${r.version}</span>`:''}${r.operation_id?`<span class="tag">${esc(r.operation_id)}</span>`:''}</div>`;
  if(r.long_term_ready!==undefined)html+=`<div class="outcome-grid"><div><span>保存状态</span><strong>已保存 · 模拟</strong></div><div><span>长期检索资格</span><strong>${r.long_term_ready?'已就绪':'尚未就绪'}</strong></div></div>`;
  if(r.cleanup)html+=`<div class="result-meta"><span class="tag">新请求使用：已阻断</span><span class="tag amber">清理：${esc(r.cleanup)}</span></div>`;
  if(r.selected_sources)html+=`<div class="result-meta"><span class="tag">${esc(r.selected_sources.join(' + '))}</span><span class="tag">${r.length} / ${r.budget} 字符</span><span class="tag">${esc(r.recall_id)}</span></div>`;
  if(r.missing_sources?.length)html+=`<p class="help">缺失来源：${esc(r.missing_sources.join('、'))}。本次没有静默缩小来源范围。</p>`;
  if(r.code)html+=`<p class="help">原因：${esc(r.code)}</p>`;
  if(r.conflict&&r.items?.some(i=>i.conflict))html+='<p class="help">以下预算来自独立的有效来源，尚未裁决，必须保留分歧。</p>';
  for(const i of r.items||[])html+=`<article class="memory-result ${i.conflict?'conflict':''}"><small>${esc(i.memory_id)} · v${i.version} · ${esc(tierLabels[i.tier]||i.tier)}${i.conflict?' · 冲突组成员':''}</small><p>${esc(i.text)}</p><small>来源：${esc(i.source)}</small></article>`;
  if(r.omitted)html+=`<p class="help">${r.omitted} 条因整组预算未入选。预算选择本身不等于来源故障。</p>`;
  if(r.status==='inspected')html+=`<div class="p2-readout"><div class="p2-line"><b>E2 · 对象</b><span>${esc(r.object_service.ref)} · ${r.object_service.readable?'模拟可读':'不可读'}</span><p>${esc(r.object_service.body)}</p></div><div class="p2-line"><b>E1 · 向量引用</b><span>${r.vector_service.hits.length} 个引用 · 固定匹配，未运行 ANN</span></div><div class="p2-line"><b>E3 · 来源关系</b><span>${r.graph_service.edges.map(e=>esc(e.from+' → '+e.to)).join('<br>')||'暂无关系'}</span></div></div>`;
  if(r.actions)html+=r.actions.map(a=>`<div class="memory-result"><small>${esc(a.id)} · ${esc(a.status)}</small><p>${esc(a.memory_id)} · v${a.version} / ${esc(tierLabels[a.from])} → ${esc(tierLabels[a.to])}</p><small>新增动作：${r.new_actions_created} · 模拟结果</small></div>`).join('');
  html+=`<details class="payload"><summary>查看响应样例 JSON · 演示 DTO</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details>`;$('result').innerHTML=html;
}
function renderInspector(){
  document.querySelectorAll('[data-inspect]').forEach(b=>b.classList.toggle('selected',b.dataset.inspect===inspectTab));
  let html='';
  if(inspectTab==='state'){
    html='<p class="microcopy">同一记忆的业务类型、有效版本、检索资格与访问层级分别维护。</p>';
    if(!model.memories.length)html+='<div class="empty-state"><span class="empty-symbol">◇</span><strong>尚无模拟记忆</strong><p>提交后将在这里看见状态变化。</p></div>';
    for(const m of [...model.memories].reverse())html+=`<article class="state-card ${m.state==='active'?'':'retired'}"><h4>${esc(m.id)} · v${m.version}<span class="tag ${m.state==='active'?'green':''}">${esc(stateLabels[m.state])}</span></h4><p>${esc(m.text)}</p><div class="state-line"><span>业务类型</span><span>${esc(m.kind)}</span></div><div class="state-line"><span>长期资格</span><span>${esc(projectionLabels[m.projection])}</span></div><div class="state-line"><span>已确认访问层级</span><span>${esc(tierLabels[m.tier])}</span></div><div class="state-line"><span>清理进度</span><span>${esc(m.cleanup)}</span></div><div class="state-line"><span>对象与向量</span><span>${model.objects[m.objectRef]?'对象在':'对象无'} / ${model.vectors[m.objectRef]?'向量在':'向量无'}</span></div></article>`;
  }else if(inspectTab==='trace'){
    html='<p class="microcopy">依次记录模拟调用与状态变化。顺序号不是时延指标，所有 P2 结果均为替身产生。</p>';
    if(!model.events.length)html+='<p class="help">执行第一步后显示轨迹。</p>';
    html+=[...model.events].reverse().map(e=>`<div class="trace-entry"><span>#${e.seq} · ${esc(e.owner)}</span><b>${esc(e.stage)}</b><p>${esc(e.detail)}</p></div>`).join('');
  }else{
    html=`<p class="microcopy">基础算法自动消费存储与召回轨迹。样例策略：同一表示两次交付后温→热；不代表正式规则或性能收益。</p><div class="mini-stats"><div><b>${model.storageEvents}</b>存储变化</div><div><b>${model.readEvents}</b>交付事实</div><div><b>${model.actions.length}</b>模拟动作</div></div>`;
    for(const a of [...model.actions].reverse())html+=`<article class="state-card"><h4>${esc(a.id)}<span class="tag ${a.status==='succeeded'?'green':'amber'}">${esc(a.status==='unknown'?'正在核对':a.status==='succeeded'?'已模拟确认':a.status)}</span></h4><p>${esc(tierLabels[a.from])} → ${esc(tierLabels[a.to])}</p><div class="state-line"><span>记忆 / 版本</span><span>${esc(a.memory_id)} / v${a.version}</span></div><div class="state-line"><span>路由版本</span><span>${a.expected_route_epoch} → ${a.observed_route_epoch||'尚未确认'}</span></div><p class="help">${a.status==='unknown'?'执行端可能已完成。保留原动作身份，等待结果核对。':'仅模拟；正式验收需要目标正文准确可读的证据。'}</p></article>`;
    html+=[...model.decisions].reverse().slice(0,12).map(d=>`<div class="decision"><b>${esc(d.memory_id)} · ${esc(d.decision)}</b><p>${esc(d.reason)}</p><span>依据：存储 ${d.inputs.storage} / 交付 ${d.inputs.recall}</span></div>`).join('');
    if(!model.decisions.length)html+='<p class="help">存储或召回发生后，在此观察算法判断。</p>';
  }
  $('inspector-content').innerHTML=html;
}
document.querySelectorAll('[data-inspect]').forEach(b=>b.onclick=()=>{inspectTab=b.dataset.inspect;renderInspector();});
function scope(){const selected=$('request-scope').value;return {...model.credential,...(selected==='tenant'?{tenant:'tenant-b'}:selected==='agent'?{agent:'other-agent'}:{})};}
function manualWrite(repeat){if(!repeat||!manualKey)manualKey='manual-'+Date.now();model.write($('input-text').value,{key:manualKey,kind:$('memory-kind').value,scope:scope(),source:'自由输入'});render();}
$('manual-write').onclick=()=>manualWrite(false);$('manual-repeat').onclick=()=>manualWrite(true);
$('manual-process').onclick=()=>{model.process($('target-memory').value);render();};
$('manual-recall').onclick=()=>{model.recall({query:$('query').value,sources:$('sources').value,budget:Number($('budget').value),scope:scope()});render();};
$('manual-update').onclick=()=>{const m=model.current($('target-memory').value);if(!model.authorize(scope()))model.finish({status:'rejected',code:'SCOPE_DENIED'});else if(m)model.update(m.id,$('input-text').value,m.version);else model.finish({status:'blocked',code:'NO_ACTIVE_VERSION'});render();};
$('manual-delete').onclick=()=>{if(!model.authorize(scope()))model.finish({status:'rejected',code:'SCOPE_DENIED'});else model.remove($('target-memory').value);render();};
$('manual-reconcile').onclick=()=>{model.reconcile();render();};$('fault').onchange=()=>{model.fault=$('fault').value;};
$('export').onclick=()=>{const output={title:'P2/P3 原型评审记录',date:new Date().toISOString(),scenario:scenarios[scenarioIndex].id,steps_executed:stepIndex,prd:'P3 V1.2 产品评审稿',notice:'全部为模拟证据，不是实际 P2/MVP 验收结果。',...model.snapshot()};const blob=new Blob([JSON.stringify(output,null,2)],{type:'application/json;charset=utf-8'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='mock-review-'+scenarios[scenarioIndex].id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
function showADR(index){
  const list=REVIEW_CONTENT.adrs;const a=list[index];
  $('adr-list').innerHTML=list.map((x,i)=>`<button data-adr="${i}" class="${i===index?'selected':''}"><small>${esc(x.id)} · Proposed</small><strong>${esc(x.title)}</strong></button>`).join('');
  document.querySelectorAll('[data-adr]').forEach(b=>b.onclick=()=>showADR(Number(b.dataset.adr)));
  $('adr-detail').innerHTML=`<div class="adr-meta"><span class="tag amber">Proposed · 待联合评审</span><span>${esc(a.id)} / 2026-09-18</span><span>${esc(a.fr)}</span></div><h2>${esc(a.title)}</h2><p class="help">依据：${esc(a.refs)} · 场景：${esc(a.scenario)}</p><h3>需要解决的问题</h3><p>${esc(a.context)}</p><h3>建议采用的决策</h3><div class="decision-summary"><p>${esc(a.decision)}</p></div><h3>方案比较</h3><table><thead><tr><th>方案</th><th>选择依据</th></tr></thead><tbody>${a.alternatives.map(x=>`<tr><td>${esc(x[0])}</td><td>${esc(x[1])}</td></tr>`).join('')}</tbody></table><h3>代价与影响</h3><p>${esc(a.cost)}</p><h3>怎样验证</h3><p>${esc(a.verify)}</p><h3>需要明确的事项与责任</h3><p>${esc(a.pending)}</p><p class="adr-links"><a href="ADR/${a.id}.md">查看可编辑 Markdown 决策记录 ↗</a></p>`;
}
$('coverage-body').innerHTML=REVIEW_CONTENT.coverage.map(r=>'<tr>'+r.map((x,i)=>`<td>${i===3?`<span class="tag ${x==='已模拟'?'green':x==='部分模拟'?'amber':''}">${esc(x)}</span>`:esc(x)}</td>`).join('')+'</tr>').join('');
$('source-list').innerHTML=REVIEW_CONTENT.sources.map(s=>`<article><a href="${esc(s.path)}">${esc(s.id+' · '+s.name)} ↗</a><p>${esc(s.use)}</p></article>`).join('');
const contracts=[['01 / 对象','如何证明保存了正确版本？','已有 PutObject / HeadObject / GetObject。P3 还需正文、来源、版本绑定及可靠保存边界。','待核对：精确版本读取与证据'],['02 / 检索','如何阻止越权和旧结果？','已有 InsertVector / SearchVector。需要可信范围过滤、表示身份与当前资格核对。','待核对：过滤语义及能力缺口'],['03 / 关系','图结果关联哪份内容？','已有 CreateNode / CreateEdge / Traverse；按来源与版本关联。','待核对：融合算子与一致性'],['04 / 执行','受理后怎样确认生效？','已有 Segment 内省与迁移回调。Representation 经适配解析为执行目标。','待核对：Executor 与读取证明'],['05 / 版本','哪些版本分别保护什么？','Memory 版本、映射代际、目标代际和路由版本各自维护。','禁止用一个 generation 替代全部']];
$('contract-grid').innerHTML=contracts.map(c=>`<article><span>${esc(c[0])}</span><h3>${esc(c[1])}</h3><p>${esc(c[2])}</p><small>${esc(c[3])}</small></article>`).join('');
showADR(0);selectScenario(0);
