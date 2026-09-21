/* PRD V1.3 review simulator. All state, identities, clocks and effects are local examples. */
(function(root){
  const copy=x=>JSON.parse(JSON.stringify(x));
  class P3ReviewModel {
    constructor(state){
      this.seq=0;this.now=0;this.actor='a1';this.requestTenant='A';this.credential='valid';
      this.tenants={A:{name:'公司 A',enabled:true,revision:1},B:{name:'公司 B',enabled:true,revision:1}};
      this.actors={a1:{name:'A · 项目 Agent',tenant:'A',enabled:true},a2:{name:'A · 协作 Agent',tenant:'A',enabled:true},b1:{name:'B · 项目 Agent',tenant:'B',enabled:true},unbound:{name:'已认证 · 未开通',tenant:null,enabled:true}};
      this.grants=[];this.memories=[];this.tasks=[];this.outbox=[];this.actions=[];this.contexts=[];this.spans=[];this.audit=[];this.history=[];this.barriers={};
      this.health={api:true,worker:true,model:true,vector:true,object:true,executor:true,identity:true};
      this.fault='none';this.deliveryPaused=false;this.last=null;this.lastMemory=null;this.lastContext=null;this.lastTask=null;this.lastAction=null;this.checkpoint=null;this.draft=null;
      this.extWindow=30;this.policy={readsToPromote:2,capacity:3,forecastBudget:1};this.counts={storage:0,recall:0,consumed:0};
      if(state)Object.assign(this,copy(state));this._trace=null;
    }
    id(p){return p+'-'+String(++this.seq).padStart(4,'0');}
    select(actor,tenant){this.actor=actor;this.requestTenant=tenant??this.actors[actor]?.tenant??'A';this.credential='valid';}
    active(id=this.lastMemory){return this.memories.find(m=>m.id===id&&m.state==='active');}
    span(owner,stage,status,input={},output={},reason='',trace){
      const row={span_id:this.id('span'),trace_id:trace||this._trace||this.id('trace'),time:this.now,owner,stage,status,input:copy(input),output:copy(output),reason};
      this.spans.push(row);return row;
    }
    run(owner,name,input,fn,trace){
      this.now++;const previous=this._trace;this._trace=trace||this.id('trace');const traceId=this._trace;
      this.span(owner,name+' · 开始','running',input);
      let result;try{result=fn();}catch(e){result={status:'failed',code:'SIMULATION_ERROR',message:e.message,items:[]};}
      this.span(owner,name+' · 结果',result.status,input,result,result.code||'');
      this.last={...copy(result),trace_id:traceId,mode:'simulated',prd:'V1.3'};
      this.history.push({name,trace_id:traceId,time:this.now,result:copy(this.last)});this._trace=previous;return this.last;
    }
    denied(code,message){return {status:'denied',code,message,items:[]};}
    context(actor=this.actor,tenant=this.requestTenant,entry=true){
      if(entry&&!this.health.api)return {status:'unavailable',code:'API_UNAVAILABLE',message:'模拟API进程不可用，业务请求尚未处理。',items:[]};
      if(entry&&(!this.health.identity||this.credential==='unverifiable'))return this.denied('IDENTITY_UNVERIFIABLE','身份无法可靠验证，本次不开放业务。');
      if(entry&&this.credential==='expired')return this.denied('TOKEN_EXPIRED','模拟凭证已过期。');
      const a=this.actors[actor];if(!a?.tenant)return this.denied('TENANT_NOT_BOUND','已认证，但尚未开通有效业务映射。');
      if(!a.enabled)return this.denied('SUBJECT_DISABLED','调用主体已停用。');
      if(a.revokedAt!==undefined&&this.now>=a.revokedAt+this.extWindow)return this.denied('EXTERNAL_REVOKED','已在示例传播窗口内获知外部撤销，拒绝后续使用。');
      if(!this.tenants[a.tenant]?.enabled)return this.denied('TENANT_DISABLED','业务租户已停用；不自动删除原数据。');
      if(tenant!==a.tenant)return this.denied('SCOPE_DENIED','请求范围超出可信业务映射。');
      return {actor,tenant,revision:this.tenants[tenant].revision};
    }
    permits(ctx,m,op='read'){
      return ctx.status!=='denied'&&m&&m.state==='active'&&ctx.tenant===m.tenant&&(ctx.actor===m.owner||this.grants.some(g=>g.actor===ctx.actor&&g.memory===m.id&&g.active&&g.expires>this.now&&g.operations.includes(op)));
    }
    check(ctx,m,op='read'){
      const yes=this.permits(ctx,m,op);this.span('RF','当前资源与操作授权',yes?'passed':'denied',{actor:ctx.actor,tenant:ctx.tenant,operation:op},{allowed:!!yes},yes?'':'RESOURCE_DENIED');return yes;
    }
    task(kind,m){const t={id:this.id('task'),kind,memory_id:m.id,version:m.version,actor:m.owner,tenant:m.tenant,revision:this.tenants[m.tenant].revision,status:'queued',attempts:0,lease:0,fence:0,trace_id:this._trace,error:null,recovery:[]};this.tasks.push(t);this.lastTask=t.id;return t;}
    event(type,m){const e={id:this.id('event'),type,memory_id:m.id,version:m.version,tenant:m.tenant,trace_id:this._trace,status:'pending',consumer_applied:false,delivery_attempts:0};this.outbox.push(e);this.counts[type==='access'?'recall':'storage']++;this.span('RF','事实与待办同一模拟提交','passed',{type},{memory_id:m.id,event_id:e.id});if(!this.deliveryPaused)this.deliver(e);return e;}
    deliver(e,loseAck=false){e.delivery_attempts++;if(!e.consumer_applied){e.consumer_applied=true;this.counts.consumed++;this.span('Operate','消费并去重','passed',{event_id:e.id},{applied:true});const m=this.active(e.memory_id);if(m&&e.type!=='access')this.evaluate(m,'storage_change');}else this.span('Operate','重复投递去重','passed',{event_id:e.id},{applied:false});e.status=loseAck?'pending':'acked';}
    flush(loseAck=false){return this.run('RF','投递持久事件',{},()=>{for(const e of this.outbox.filter(e=>e.status==='pending'))this.deliver(e,loseAck);return {status:loseAck?'unknown':'completed',message:loseAck?'消费已生效但确认丢失；重投不重复计热。':'待投事件已确认。',pending:this.outbox.filter(e=>e.status==='pending').length,applied:this.counts.consumed};});}
    remember(text='项目预算上限20万元，每周五汇报。',options={}){
      return this.run('Remember','保存输入',{text,actor:this.actor,tenant:this.requestTenant},()=>{
        const c=this.context();if(c.status)return c;if(!text.trim())return this.denied('EMPTY_CONTENT','请输入内容。');
        const key=options.key||this.id('key');const signature=JSON.stringify([text,options.kind||'working',options.source||'项目会议']);
        const old=this.memories.find(m=>m.key===key&&m.owner===c.actor&&m.tenant===c.tenant);
        if(old)return old.signature===signature?{status:'saved',memory_id:old.id,version:old.version,idempotent:true,message:'同键同内容，返回原保存操作；不新增事实。'}:{status:'conflict',code:'IDEMPOTENCY_CONFLICT',message:'同键不同内容，拒绝覆盖。'};
        if(!this.health.object)return {status:'failed',code:'OBJECT_UNAVAILABLE',message:'对象保存失败，未报告已保存。'};
        const m={id:this.id('memory'),version:1,owner:c.actor,tenant:c.tenant,text,source:options.source||'项目会议',kind:options.kind||'working',state:'active',projection:'pending',cleanup:'none',tier:'warm',observed:'warm',epoch:1,reads:0,key,signature};
        this.memories.push(m);this.lastMemory=m.id;const t=this.task('extract-project',m);this.event('created',m);
        this.span('P2 · 模拟','正文准确回读','passed',{memory_id:m.id,version:1},{readable:true,source:m.source});
        return {status:'saved',memory_id:m.id,version:1,task_id:t.id,saved:true,ready:false,message:'原内容与加工待办已保存；适用Working立即可用，长期投影尚未就绪。'};
      });
    }
    process(id=this.lastTask){
      const t=this.tasks.find(t=>t.id===id)||this.tasks.find(t=>t.memory_id===id&&['queued','failed','expired'].includes(t.status));
      return this.run('Remember','后台加工与投影',{task_id:t?.id},()=>{
        if(!t)return this.denied('TASK_NOT_FOUND','没有可处理的原任务。');
        const c=this.context(t.actor,t.tenant,false),m=this.active(t.memory_id);
        if(c.status||!this.check(c,m,'write')||m.version!==t.version||c.revision!==t.revision){if(t.status!=='completed'){t.status='blocked';t.error='CURRENT_QUALIFICATION_CHANGED';}return {status:'blocked',code:'CURRENT_QUALIFICATION_CHANGED',message:'原任务主体、租户、授权或版本已变化，拒绝旧任务提交；已完成历史不改写。'};}
        if(t.status==='completed')return {status:'completed',idempotent:true,message:'原任务已完成，不重复形成记忆。'};
        if(!this.health.worker){return {status:'waiting',code:'WORKER_UNAVAILABLE',message:'Worker不可用，持久待办保留。'};}
        t.attempts++;t.fence++;t.lease=this.now+10;t.status='running';this.span('RF','领取任务与租约','passed',{task_id:t.id},{attempt:t.attempts,fence:t.fence,lease:t.lease,actor:t.actor});
        if(this.fault==='crash'){this.health.worker=false;t.error='WORKER_LOST';t.abandonedFence=t.fence;return {status:'unknown',task_id:t.id,message:'Worker在提交前停止，任务仍为运行中；等待租约检测。'};}
        const dependency=!this.health.model?'model':!this.health.vector?'vector':!this.health.object?'object':null;
        if(dependency){t.status='failed';t.error=dependency.toUpperCase()+'_UNAVAILABLE';m.projection='failed';this.span(dependency,'依赖调用','failed',{task_id:t.id},{},t.error);return {status:'processing_failed',code:t.error,saved:true,ready:false,message:'原输入仍保留；加工失败，尚未取得长期资格。'};}
        if(this.fault==='quality'){t.status='failed';t.error='QUALITY_REJECTED';return {status:'processing_failed',code:t.error,message:'候选遗漏否定或改变数字，被质量规则拒绝；不发布派生产物。',candidate:'预算为200万元，可取消周五汇报',source:m.text};}
        const artifact={text:m.text.replace(/会议第1节：|第2节：/g,''),source:m.source,source_version:m.version,classification:m.kind==='episodic'?'Semantic 候选（来源为 Episodic 事件）':m.kind,simulated:true,method:'预设提纯样例：移除章节引导，保留数字、否定、条件与来源'};
        this.span('Remember','候选与来源核验','passed',{source:m.source,version:m.version},{candidate:artifact,approved:true});
        this.span('P2 · 模拟','投影查询与正文回读','passed',{memory_id:m.id,version:m.version},{queryable:true,exact_read:true});
        m.projection='ready';t.status='completed';t.error=null;this.event('projection_ready',m);
        return {status:'ready',memory_id:m.id,version:m.version,task_id:t.id,saved:true,ready:true,artifact,message:'来源、版本、投影及准确回读核验通过，长期可用。'};
      },t?.trace_id);
    }
    recall(options={}){
      return this.run('Recall',options.pause?'检索后暂停，等待交付':'统一召回',{actor:this.actor,tenant:this.requestTenant,query:options.query||'项目约束',sources:options.sources||'both',budget:options.budget??400},()=>{
        const c=this.context();if(c.status)return c;
        const src=options.sources||'both',selected=src==='auto'?['working','long_term']:src==='both'?['working','long_term']:[src];
        const missing=selected.filter(s=>s==='long_term'&&!this.health.vector);if(!this.health.object)missing.push('exact_content');
        this.span('Recall','来源覆盖','passed',{selected},{missing});
        let candidates=this.memories.filter(m=>this.permits(c,m)&&((selected.includes('working')&&m.kind==='working')||(selected.includes('long_term')&&this.health.vector&&m.projection==='ready')));
        if(options.query==='火星气象')candidates=[];if(!this.health.object)candidates=[];
        this.span('Recall','候选引用与版本核验','passed',{projection_refs:this.memories.filter(m=>m.projection==='ready').map(m=>m.id+'@'+m.version)},{accepted:candidates.map(m=>m.id+'@'+m.version),rule:'只保留当前有效且获授权版本'});
        const conflict=candidates.filter(m=>/预算/.test(m.text));const hasConflict=new Set(conflict.map(m=>(m.text.match(/\d+万/)||[])[0]).filter(Boolean)).size>1;
        const groups=hasConflict?[{items:conflict,conflict:true},...candidates.filter(m=>!conflict.includes(m)).map(m=>({items:[m],conflict:false}))]:candidates.map(m=>({items:[m],conflict:false}));
        let used=0,omitted=0,items=[];const budget=Number(options.budget??400);
        if(!Number.isFinite(budget)||budget<1)return this.denied('INVALID_BUDGET','预算必须是正数。');
        for(const g of groups){const cost=g.items.reduce((n,m)=>n+Array.from(m.text+' ['+m.source+' v'+m.version+']').length,0)+(g.conflict?28:0);if(used+cost>budget){omitted+=g.items.length;continue;}used+=cost;items.push(...g.items.map(m=>({memory_id:m.id,version:m.version,text:m.text,source:m.source,tier:m.tier,conflict:g.conflict})));}
        this.span('Recall','融合、冲突与完整组预算','passed',{budget,unit:'模拟字符'},{used,omitted,conflict:hasConflict,items:items.map(i=>i.memory_id)});
        const draft={actor:c.actor,tenant:c.tenant,items,selected,missing,used,budget,omitted,trace_id:this._trace};
        if(options.pause){this.draft=draft;return {status:'waiting',message:'候选已经形成，正文尚未向调用方交付；可在此撤权或删除，再推进最终交付。',candidate_count:items.length,items:[]};}
        return this.finishRecall(draft);
      });
    }
    finishRecall(d){
      const c=this.context(d.actor,d.tenant,false);if(c.status)return c;
      if(d.items.some(i=>{const m=this.active(i.memory_id);return !this.check(c,m)||m.version!==i.version;}))return {status:'blocked',code:'FINAL_QUALIFICATION_CHANGED',items:[],message:'交付前权限或版本变化，拒绝交付旧候选正文。'};
      const status=d.items.length?(d.missing.length?'degraded':'complete'):(d.missing.length||d.omitted?'unavailable':'empty');
      const r={id:this.id('context'),actor:d.actor,tenant:d.tenant,items:copy(d.items),status,selected:d.selected,missing:d.missing,used:d.used,budget:d.budget,omitted:d.omitted,trace_id:this._trace};this.contexts.push(r);this.lastContext=r.id;
      for(const i of r.items){const m=this.active(i.memory_id);m.reads++;this.event('access',m);this.evaluate(m,'actual_access');}
      return {...r,context_id:r.id,message:{complete:'所选来源可用，交付当前有效材料。',degraded:'交付独立可信材料，并说明缺失来源。',empty:'来源正常，没有相关且获准的材料。',unavailable:'来源故障或全部完整组超预算，不能伪装成正常空。'}[status],budget_unit:'模拟字符；正式实现按目标tokenizer统计正文、引用及说明'};
    }
    finishPending(){const d=this.draft;return this.run('Recall','交付前最终复核',{},()=>{if(!d)return this.denied('NO_PENDING_RESULT','没有暂停的召回。');this.draft=null;return this.finishRecall(d);},d?.trace_id);}
    replay(id=this.lastContext){return this.run('Recall','重新获取原结果',{context_id:id},()=>{const c=this.context();if(c.status)return c;const r=this.contexts.find(r=>r.id===id);if(!r||r.actor!==c.actor||r.tenant!==c.tenant)return this.denied('RESULT_DENIED','没有获取该结果的权限或结果不可用。');if(r.items.some(i=>!this.permits(c,this.active(i.memory_id))||this.active(i.memory_id).version!==i.version))return {status:'blocked',code:'RESULT_INVALIDATED',items:[],message:'旧结果需遵守当前授权与有效版本，本次不重新交付正文。'};return {...copy(r),status:r.status,replayed:true,message:'当前许可及版本有效；返回原结果，不重复计热。'};});}
    mutate(operation,text,id=this.lastMemory){return this.run('Remember',operation==='correct'?'显式更正':operation==='delete'?'删除与阻断':operation==='archive'?'归档':'重新激活',{memory_id:id,text,actor:this.actor},()=>{
      const c=this.context();if(c.status)return c;let m=this.active(id)||this.memories.find(m=>m.id===id&&m.state==='archived');
      if(!m||m.tenant!==c.tenant||m.owner!==c.actor)return this.denied('WRITE_DENIED','读取共享不包含更正、删除或生命周期写权限。');
      if(operation==='correct'){m.state='superseded';m.cleanup='pending';const n={...copy(m),version:m.version+1,text,state:'active',projection:'pending',cleanup:'none',reads:0};this.barriers[id]={minVersion:n.version};this.memories.push(n);const t=this.task('extract-project',n);this.event('corrected',n);return {status:'saved',memory_id:id,version:n.version,task_id:t.id,message:'新版本生效，旧投影即使保留也不能再交付。'};}
      if(operation==='delete'){this.barriers[id]={deleted:true};for(const v of this.memories.filter(v=>v.id===id)){v.state='deleted';v.cleanup='pending';}this.event('deleted',m);return {status:'blocked',memory_id:id,cleanup:'pending',message:'所有版本已停止使用；来源与派生清理作为独立待办。'};}
      m.state=operation==='archive'?'archived':'active';this.event(operation,m);return {status:m.state,message:operation==='archive'?'退出默认召回，原内容保留。':'重新激活后仍按当前授权、版本及投影核验。'};
    });}
    cleanup(id=this.lastMemory){return this.run('Remember','核对派生清理',{memory_id:id},()=>{const c=this.context();if(c.status)return c;const versions=this.memories.filter(m=>m.id===id);if(!versions.length||versions.some(m=>m.owner!==c.actor||m.tenant!==c.tenant))return this.denied('CLEANUP_DENIED','没有清理目标的权限。');for(const m of versions.filter(m=>m.state!=='active')){m.cleanup='completed';m.projection='removed';}return {status:'completed',message:'本模拟内失效版本的对象、向量、派生副本完成清理；备份与日志按正式保留约定另验。'};});}
    configure(op){return this.run('RF','受控配置变更',{operation:op,operator:'模拟接入维护人员'},()=>{
      if(op==='invalid'){this.audit.push({operation:op,result:'rejected',time:this.now});return {status:'failed',code:'CONFIG_VALIDATION_FAILED',message:'配置缺少获准身份来源，整批拒绝；未部分开通。'};}
      if(op==='grant')this.grants.push({id:this.id('grant'),actor:'a2',memory:this.lastMemory,operations:['read'],active:true,expires:this.now+500,revision:1});
      if(op==='revoke')for(const g of this.grants){g.active=false;g.revision++;}
      if(op==='disable'||op==='enable'){this.tenants.A.enabled=op==='enable';this.tenants.A.revision++;}
      if(op==='disable_subject')this.actors.a1.enabled=false;
      if(op==='enable_subject')this.actors.a1.enabled=true;
      if(op==='bind'){this.actors.unbound.tenant='A';}
      if(op==='rename')this.tenants.A.name='公司 A · 新显示名称';
      if(op==='external_revoke')this.actors.a1.revokedAt=this.now;
      this.audit.push({operation:op,result:'applied',time:this.now,tenant_revision:this.tenants.A.revision,trace_id:this._trace});
      return {status:'configured',operation:op,message:{grant:'为A2授予目标记忆只读共享；有效期为示例配置。',revoke:'共享已撤销；新请求、在途交付和旧结果重新核验。',disable:'租户A已停用，数据保留；已发生外部动作仍可受控对账。',enable:'重新启用租户A；旧任务不可盲目沿用旧配置版本。',disable_subject:'A1已停用，同租户其他主体不自动停用。',enable_subject:'A1重新启用。',bind:'未绑定身份已通过受控配置映射到A；并未自动取得他人资源权限。',rename:'只更新显示名称，业务编号和数据归属不变。',external_revoke:'模拟身份平台撤销A1。示例传播窗口30秒，仅演示时间线，不是约定SLA。'}[op]||'配置已变更。'};
    });}
    evaluate(m,reason,target){
      this.span('Operate','读取当前状态并计算差异','passed',{memory_id:m.id,reason,reads:m.reads},{current:m.tier});
      if(!target&&!(m.reads>=this.policy.readsToPromote&&m.tier==='warm'))return;
      target=target||'hot';if(this.actions.some(a=>a.memory_id===m.id&&['unknown','submitted'].includes(a.status)))return;
      const c=this.context(m.owner,m.tenant,false);if(c.status||!this.permits(c,m,'write'))return;
      if(this.fault==='capacity'||(target==='hot'&&this.memories.filter(x=>x.tier==='hot').length>=this.policy.capacity)){this.span('Operate','容量保护','deferred',{target},{},'CAPACITY_LIMIT');return;}
      const a={id:this.id('action'),memory_id:m.id,version:m.version,tenant:m.tenant,actor:m.owner,from:m.tier,to:target,status:'submitted',trace_id:this._trace,provider:'pending',epoch:m.epoch,reason,mode:'simulated'};this.actions.push(a);this.lastAction=a.id;
      this.span('Operate','持久化原动作','passed',{action_id:a.id},{from:a.from,to:a.to});
      if(this.fault==='executor_fail'){a.status='failed';a.provider='failed';return;}
      if(this.fault==='evidence_lost'||!this.health.executor){a.status='unknown';a.provider='lost';return;}
      a.provider='completed';m.observed=target;if(this.fault==='ack'){a.status='unknown';this.span('Operate','执行响应丢失','unknown',{action_id:a.id},{old_read_path:m.tier},'ACK_LOST');return;}this.confirm(a);
    }
    confirm(a){const m=this.active(a.memory_id);if(!m||m.version!==a.version){a.status='stale';return;}
      if(a.provider==='lost'||!this.health.executor){a.status='unknown';return;}if(a.provider==='failed'){a.status='failed';return;}
      if(a.provider==='completed'&&m.observed===a.to){a.status='succeeded';m.tier=a.to;m.epoch++;this.span('Operate','查询原动作与目标准确回读','passed',{action_id:a.id},{observed:a.to,epoch:m.epoch,controlled_maintenance:true},'',a.trace_id);}
    }
    reconcile(){return this.run('Operate','受控对账原动作',{operator:'模拟维护人员'},()=>{const unknown=this.actions.filter(a=>a.status==='unknown');unknown.forEach(a=>this.confirm(a));return {status:unknown.some(a=>a.status==='unknown')?'unknown':'completed',message:unknown.some(a=>a.status==='unknown')?'执行端证据丢失，继续保留未知；不能假定未执行或重新发相同效果。':'按原动作观察并核对，未生成新动作；停用不抹除既有外部效果。',actions:copy(unknown)};});}
    schedule(direction='down'){return this.run('Operate','周期策略评估',{direction},()=>{const m=this.active();if(!m)return this.denied('NO_ACTIVE_MEMORY','无有效目标。');const c=this.context(m.owner,m.tenant,false);if(c.status)return c;const levels=['cold','warm','hot'],i=levels.indexOf(m.tier),target=levels[Math.max(0,Math.min(2,i+(direction==='down'?-1:1)))];if(target===m.tier)return {status:'completed',message:'当前已处于边界层，保持原状态。'};this.evaluate(m,'periodic_policy',target);const a=this.actions.find(a=>a.id===this.lastAction);return {status:this.fault==='capacity'?'deferred':a?.status||'deferred',message:this.fault==='capacity'?'容量不足，暂缓；原可读路径保留。':'依据当前状态执行相邻层调整；层级变化不改变事实类型。',action:a};});}
    probe(){return this.run('RF','运行健康检测',{},()=>{const failed=Object.entries(this.health).filter(([,ok])=>!ok).map(([n])=>n);for(const t of this.tasks.filter(t=>t.status==='running'&&t.lease<=this.now)){t.status='expired';t.error='LEASE_EXPIRED';t.recovery.push({at:this.now,decision:'await_domain_recheck'});this.span('RF','租约过期检测','failed',{task_id:t.id},{recovery:'等待领域判断'},'LEASE_EXPIRED',t.trace_id);}return {status:failed.length?'degraded':'healthy',message:'探测只记录状态；不会因依赖失败直接宣布业务失败或自动重启。',dependencies:copy(this.health),failed};});}
    advance(seconds=40){this.now+=seconds;return this.run('RF','推进模拟时钟',{seconds},()=>({status:'completed',message:'虚拟时间已推进'+seconds+'秒；不代表实际等待或性能测量。'}));}
    recover(){return this.run('RF','恢复扫描与领域判定',{},()=>{this.health.worker=true;this.fault='none';const resumed=[];for(const t of this.tasks.filter(t=>['expired','failed'].includes(t.status))){const m=this.active(t.memory_id),c=this.context(t.actor,t.tenant,false);if(c.status||!m||m.version!==t.version||c.revision!==t.revision||!this.permits(c,m,'write')){t.status='blocked';t.recovery.push({at:this.now,decision:'block_stale_work'});}else if(t.attempts>=3){t.status='blocked';t.error='RETRY_LIMIT';t.recovery.push({at:this.now,decision:'manual_review'});}else{t.status='queued';t.recovery.push({at:this.now,decision:'retry_original',reason:'当前资格有效，重做提取不新增正式事实'});resumed.push(t.id);}this.span('RF','领域恢复判定',t.status,{task_id:t.id},{decision:t.recovery.at(-1).decision},t.error||'',t.trace_id);}return {status:'completed',message:'扫描与恢复判定已记录；仅有效原任务重新排队，业务完成仍需处理与核验。',resumed};});}
    lateCommit(){return this.run('RF','检查旧Worker迟到提交',{},()=>{const t=this.tasks.find(t=>t.id===this.lastTask);if(!t)return this.denied('NO_TASK','没有任务。');if(t.abandonedFence===undefined)return {status:'waiting',message:'当前没有中断Worker的旧领取记录。'};const invalid=t.abandonedFence!==t.fence||t.status!=='running'||t.lease<=this.now;return {status:invalid?'blocked':'waiting',code:invalid?'LEASE_FENCE_REJECTED':'LEASE_STILL_CURRENT',message:invalid?'旧领取代次已失效，迟到Worker不能覆盖新结果。':'租约仍有效，不能把它虚构为迟到任务；继续等待检测。',task_id:t.id,current_fence:t.fence};},this.tasks.find(t=>t.id===this.lastTask)?.trace_id);}
    backup(){return this.run('RF','保存模拟快照',{},()=>{this.checkpoint=copy({memories:this.memories,tasks:this.tasks,contexts:this.contexts,outbox:this.outbox,actions:this.actions,tenants:this.tenants,grants:this.grants});return {status:'completed',message:'已保存演示快照，后续可检验旧数据和旧许可不能一起回滚生效。'};});}
    restore(){return this.run('RF','恢复旧业务快照并核验权限',{},()=>{if(!this.checkpoint)return this.denied('NO_BACKUP','请先保存快照。');const current=copy({tenants:this.tenants,grants:this.grants,actors:this.actors});const b=copy(this.checkpoint);Object.assign(this,b,current);for(const m of this.memories){const barrier=this.barriers[m.id];if(barrier?.deleted){m.state='deleted';m.projection='invalid';}else if(barrier?.minVersion>m.version){m.state='superseded';m.projection='invalid';}}for(const t of this.tasks.filter(t=>t.status!=='completed')){const c=this.context(t.actor,t.tenant,false);if(c.status||c.revision!==t.revision||!this.active(t.memory_id)){t.status='blocked';t.error='RESTORE_AUTH_REJECTED';}}return {status:'completed',message:'恢复业务数据后，使用当前受控配置和有效性屏障复核；不复活停用租户、撤销共享、已删除或被替代正文。',design_note:'模拟选择：当前授权与有效性记录独立于业务快照保留。正式恢复权威来源与失联处理需按ADR落实；完成历史不改写为失败。'};});}
    queryTask(id=this.lastTask){return this.run('RF','查询原任务',{task_id:id},()=>{const c=this.context();if(c.status)return c;const t=this.tasks.find(t=>t.id===id);if(!t||t.actor!==c.actor||t.tenant!==c.tenant)return this.denied('TASK_DENIED','任务不存在或无查询权限。');return {status:t.status,task:copy(t),message:'按当前主体及业务范围查询原任务。'};});}
    forecast(){return this.run('Operate','后续P1预测预热',{},()=>{const m=this.active();if(!m)return this.denied('NO_MEMORY','无有效记忆。');if(this.fault==='forecast'||!this.policy.forecastBudget)return {status:'deferred',message:'预测不可用或预热预算不足，回退基础策略；正常Remember和Recall继续。'};this.evaluate(m,'forecast_before_access','hot');return {status:'completed',message:'预测下一次访问前准备目标；这是后续P1目标场景，未计入MVP或实际收益。',phase:'P1',actual_access_before_prepare:false};});}
    state(){const s=copy(this);delete s._trace;return s;}
  }
  if(typeof module!=='undefined')module.exports={P3ReviewModel};else root.P3ReviewModel=P3ReviewModel;
})(typeof window!=='undefined'?window:globalThis);
