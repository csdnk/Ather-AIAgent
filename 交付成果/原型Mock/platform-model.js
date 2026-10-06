/* Target-behaviour simulator. No backend, model, identity or storage calls. */
(function(root){
  const Base=typeof module!=='undefined'?require('./review-model.js').P3ReviewModel:root.P3ReviewModel;
  const copy=x=>JSON.parse(JSON.stringify(x));
  class PlatformModel extends Base {
    constructor(state){
      super();
      this.session='launch';this.incidents=[];this.maintenance=[];this.snapshots=[];this.configVersion=1;
      this.configuration={strategy:'baseline-v1',maxBackground:4,enabled:true};this.configHistory=[];
      this.observation=null;this.signalCount=0;this.signalSeen=[];this.lastSignalAt=-100;this.cooldownUntil=0;
      this.lastPlan=null;this.readEvents=[];this.savedBackups=[];this.isolatedRestore=null;
      this.businessVerified=false;this.simulationVersion='2026-09-22';
      if(state)Object.assign(this,copy(state));
    }
    renderItems(items){
      const grouped=new Map();for(const i of items){if(!grouped.has(i.group))grouped.set(i.group,[]);grouped.get(i.group).push(i);}
      return [...grouped.values()].map(units=>units.map(i=>i.text+' ['+i.source+' v'+i.version+' · '+i.representation+']').join('\n')+(units[0].conflict?'\n【有效来源存在冲突，须共同理解，不能单独采用一方】':'')).join('\n');
    }
    permits(c,m,op='read'){
      if(!m||c.status||m.expiresAt!==undefined&&this.now>=m.expiresAt)return false;
      const origin=m.origin_id?this.active(m.origin_id):null;
      if(m.origin_id&&(!origin||origin.version!==m.origin_version||origin.expiresAt!==undefined&&this.now>=origin.expiresAt))return false;
      return super.permits(c,m,op)||!!(origin&&op==='read'&&super.permits(c,origin,op)&&m.state==='active');
    }
    remember(text,options={}){
      const r=super.remember(text,options);const m=this.active(r.memory_id);
      if(m&&!r.idempotent){m.session=options.session||this.session;m.bodyLocation='body://'+m.id+'/v'+m.version;m.readable=true;m.chunkSize=options.chunkSize||24;m.modelSpace='bge-zh-demo/v1';if(options.conflictKey)m.conflictKey=options.conflictKey;}
      if(this.fault==='save_ack'&&r.status==='saved'){this.last={status:'unknown',operation_key:options.key,message:'保存响应中断，当前不能确认调用结果；使用原操作键查询，不重复保存。',mode:'simulated'};return this.last;}
      return r;
    }
    process(id=this.lastTask){
      const task=this.tasks.find(t=>t.id===id);if(task?.kind==='maintenance')return this.executeMaintenance(task.incident_id);
      if(task?.waitUntil>this.now)return this.run('RF','等待任务条件',{task_id:id},()=>({status:'waiting',message:'等待条件尚未达到，不提前重新执行。'}));
      if(!this.configuration.enabled)return this.run('RF','当前配置约束',{},()=>({status:'blocked',message:'当前配置已禁用后台策略，不能用旧任务快照绕过。'}));
      const r=super.process(id);const m=this.active(r.memory_id);
      if(r.status==='ready'&&m){
        m.artifact={...copy(r.artifact),quality:'passed',representation:'compressed',id:this.id('artifact')};
        let target=m;
        if(m.kind==='working'){
          target=m.longterm_id?this.active(m.longterm_id):null;
          if(!target){target={...copy(m),id:this.id('memory'),kind:'episodic',origin_id:m.id,origin_version:m.version,key:this.id('derived'),reads:0};this.memories.push(target);m.longterm_id=target.id;}
          m.projection='source_ready';
        }
        target.bodyLocation='body://'+target.id+'/v'+target.version;
        const chars=Array.from(target.text);target.chunks=[];
        for(let i=0;i<chars.length;i+=target.chunkSize||24)target.chunks.push({id:target.id+':c'+(target.chunks.length+1),memory_id:target.id,version:target.version,text:chars.slice(i,i+(target.chunkSize||24)).join(''),batch:'publish-'+target.version,space:target.modelSpace||'bge-zh-demo/v1',verified:true});
        if(this.fault==='partial_index'&&target.chunks.length){target.chunks.at(-1).verified=false;target.projection='pending';task&&(task.status='waiting');r.status='processing';r.ready=false;r.message='预期块尚未全部核验，整批索引不发布 Ready。';}
        r.longterm_id=target.id;r.expected_chunks=target.chunks.length;r.verified_chunks=target.chunks.filter(c=>c.verified).length;
        if(task){task.checkpoints=[...(task.checkpoints||[]),{stage:r.ready?'completed':'index_verification',version:m.version,time:this.now}];task.heartbeat={worker:'worker-demo',time:this.now};}
        this.last={...this.last,...copy(r)};
      }
      return this.last;
    }
    mutate(op,text,id=this.lastMemory){
      const before=this.active(id);const oldVersion=before?.version;
      const r=super.mutate(op,text,id);
      if(['saved','blocked','archived'].includes(r.status)&&!r.code&&['correct','delete','archive'].includes(op)){
        for(const d of this.memories.filter(m=>m.origin_id===id&&m.origin_version===oldVersion)){d.state=op==='delete'?'deleted':op==='archive'?'archived':'superseded';d.projection='invalid';d.cleanup='pending';}
        if(op==='correct'){const m=this.active(id);delete m.longterm_id;delete m.artifact;delete m.chunks;m.readable=true;m.bodyLocation='body://'+id+'/v'+m.version;}
      }
      return r;
    }
    chooseSources(query,source){
      if(source&&source!=='auto')return {selected:source==='both'?['working','long_term']:[source],reason:'严格使用调用方指定来源'};
      if(/当前|本次|现在/.test(query))return {selected:['working'],reason:'查询指向当前任务，选择当前记忆'};
      if(/历史|以前|上次|长期/.test(query))return {selected:['long_term'],reason:'查询指向历史事实，选择长期记忆'};
      const c=this.context();const ready=this.memories.some(m=>this.permits(c,m)&&m.kind!=='working'&&m.projection==='ready');
      return {selected:ready?['working','long_term']:['working'],reason:ready?'综合查询且存在可用长期记忆，合并两类来源':'当前仅有适用 Working，选择当前来源'};
    }
    recall(options={}){
      return this.run('Recall','候选检索与完整组包',{query:options.query||'项目约束',requested:options.sources||'auto'},()=>{
        const ctx=this.context();if(ctx.status)return ctx;
        const query=options.query||'项目约束',scope=options.session||this.session;
        const {selected,reason}=this.chooseSources(query,options.sources);
        if(selected.some(s=>!['working','long_term'].includes(s)))return this.denied('INVALID_SOURCE','来源无效。');
        const k=Number(options.topK??3),budget=Number(options.budget??400);
        if(!Number.isInteger(k)||k<1||!Number.isFinite(budget)||budget<1)return this.denied('INVALID_BUDGET','候选数必须为正整数，预算必须为正数。');
        const missing=[],excluded=[];
        if(selected.includes('long_term')&&!this.health.vector)missing.push('长期检索不可用');
        const allowed=this.memories.filter(m=>this.permits(ctx,m));
        const matches=m=>{const q=query.replace(/当前|历史|本次|以前|上次|长期|现在|是什么|是多少|有哪些|请问/g,'').trim();return !q||q==='项目约束'||q==='项目'||m.text.includes(q)||Array.from({length:Math.max(0,q.length-1)},(_,i)=>q.slice(i,i+2)).some(w=>m.text.includes(w));};
        const working=selected.includes('working')?allowed.filter(m=>m.kind==='working'&&m.session===scope&&matches(m)):[];
        const pool=selected.includes('long_term')&&this.health.vector?allowed.filter(m=>m.kind!=='working'&&m.projection==='ready'&&matches(m)):[];
        const hits=[];
        pool.forEach((m,i)=>{for(const c of m.chunks||[{id:m.id+':c1',text:m.text,verified:true,space:'bge-zh-demo/v1'}])if(c.verified&&c.space==='bge-zh-demo/v1')hits.push({...c,memory_id:m.id,version:m.version,score:Number((.98-i*.06).toFixed(2))});});
        hits.sort((a,b)=>b.score-a.score||a.id.localeCompare(b.id));
        const batchSize=Number(options.chunkBatch||3),maxBatches=Number(options.maxBatches||4),seen=new Set(),chosen=[];let fetched=0,batches=0;
        while(chosen.length<k&&fetched<hits.length&&batches<maxBatches){batches++;for(const hit of hits.slice(fetched,fetched+batchSize)){if(seen.has(hit.memory_id))continue;seen.add(hit.memory_id);const m=pool.find(m=>m.id===hit.memory_id);if(chosen.length<k)chosen.push(m);}fetched+=batchSize;}
        if(chosen.length<k&&fetched<hits.length)missing.push('候选补取额度达到上限');
        let candidates=[...working,...chosen];
        candidates=candidates.filter(m=>!(m.kind!=='working'&&working.some(w=>w.id===m.origin_id&&w.version===m.origin_version)));
        const groups=[];const grouped=new Set();
        for(const m of candidates){if(grouped.has(m.id))continue;const key=m.conflictKey;const related=key?allowed.filter(x=>x.conflictKey===key&&((selected.includes('working')&&x.kind==='working'&&x.session===scope)||(selected.includes('long_term')&&x.kind!=='working'&&x.projection==='ready'))):[];const members=related.filter(x=>!(x.origin_id&&related.some(w=>w.id===x.origin_id)));const values=members.length>1?members:[m];values.forEach(x=>grouped.add(x.id));groups.push({id:key||m.id,members:values,conflict:values.length>1});}
        let items=[],used=0,omitted=0;
        for(const group of groups){
          const unreadable=group.members.some(m=>m.readable===false||!this.health.object&&!m.cacheValid);
          if(unreadable){missing.push('必要正文/关系组缺失：'+group.id);excluded.push({group:group.id,reason:'整组排除，继续其他独立完整组'});continue;}
          const units=group.members.map(m=>{const compressed=options.representation==='compressed'&&m.artifact?.quality==='passed';return {memory_id:m.id,version:m.version,text:compressed?m.artifact.text:m.text,representation:compressed?'合格压缩表示':'原文',source:m.source,tier:m.tier,conflict:group.conflict,group:group.id,origin_id:m.origin_id||null};});
          for(const m of group.members){m.reads++;const ev={id:this.id('read'),memory_id:m.id,stage:'read',trace_id:this._trace};this.readEvents.push(ev);this.event('access',m);this.evaluate(m,'actual_read');}
          const rendered=this.renderItems(units);
          const cost=Array.from(rendered).length+(items.length?1:0);
          if(used+cost>budget){omitted+=units.length;excluded.push({group:group.id,reason:'整组超预算，尝试后续组'});continue;}
          used+=cost;items.push(...units);
        }
        const plan={selected,reason,space:selected.includes('long_term')?'bge-zh-demo/v1':'未调用编码器',hits:hits.slice(0,fetched),batches,topK:k,mainCandidates:chosen.map(m=>m.id),working:working.map(m=>m.id),groups:groups.map(g=>({id:g.id,members:g.members.map(m=>m.id),conflict:g.conflict})),excluded,used,budget,unit:'模拟字符（含来源和冲突说明）'};
        this.lastPlan=plan;
        this.span('Recall','来源与模型空间','passed',{selected},{reason,space:plan.space});
        this.span('Recall','块命中到记忆候选','passed',{chunk_hits:plan.hits.length},{memory_candidates:plan.mainCandidates,batches,k});
        this.span('Recall','完整正文和关系组预算',missing.length?'degraded':'passed',{groups:plan.groups},{used,budget,excluded});
        const draft={actor:ctx.actor,tenant:ctx.tenant,items,selected,missing,used,budget,omitted,plan,trace_id:this._trace};
        if(options.pause){this.draft=draft;return {status:'waiting',items:[],message:'候选与正文已核验，暂停在最终交付前。',candidate_count:items.length,plan};}
        return this.finishRecall(draft);
      });
    }
    finishRecall(d){
      const c=this.context(d.actor,d.tenant,false);if(c.status)return c;
      const invalidGroups=new Set(d.items.filter(i=>{const m=this.active(i.memory_id);return !this.permits(c,m)||m.version!==i.version;}).map(i=>i.group));
      if(invalidGroups.size){d.items=d.items.filter(i=>!invalidGroups.has(i.group));d.missing.push('最终核验发现授权或版本变化，相关整组已退出');}
      const status=d.items.length?(d.missing.length?'degraded':'complete'):(d.missing.length||d.omitted?'unavailable':'empty');
      const rendered=this.renderItems(d.items);d.used=Array.from(rendered).length;
      const r={id:this.id('context'),actor:d.actor,tenant:d.tenant,items:copy(d.items),status,selected:d.selected,missing:d.missing,used:d.used,budget:d.budget,omitted:d.omitted,plan:d.plan,rendered_context:rendered,trace_id:this._trace};
      this.contexts.push(r);this.lastContext=r.id;
      for(const i of r.items)this.readEvents.push({id:this.id('packed'),memory_id:i.memory_id,stage:'packed',trace_id:this._trace,heat_delta:0});
      return {...r,context_id:r.id,message:{complete:'已交付完整可信内容。',degraded:'保留独立可信内容，并说明本次缺失。',empty:'所选来源正常完成，没有合格的相关内容。',unavailable:'没有可安全交付的完整内容，请查看缺失或预算原因。'}[status],budget_unit:'模拟字符，正式产品使用目标 tokenizer'};
    }
    restoreDependency(name='worker'){
      return this.run('RF','模拟依赖恢复',{dependency:name},()=>{if(!(name in this.health))return this.denied('UNKNOWN_DEPENDENCY','未知依赖');this.health[name]=true;this.fault='none';return {status:'completed',message:'依赖已在模拟环境恢复；仍需检测、恢复判定和业务复验。'};});
    }
    recover(){
      if(!this.health.worker||!this.health.model||!this.health.object||!this.health.vector)return this.run('RF','恢复判定',{},()=>({status:'waiting',message:'所需依赖仍不可用，保留原任务；不会因扫描而自动修复依赖。'}));
      return super.recover();
    }
    probe(){
      const r=super.probe();this.observation={at:this.now,validUntil:this.now+30,configuration:this.configVersion,dependencies:copy(this.health),status:r.status};
      this.last.observation=copy(this.observation);return this.last;
    }
    readiness(){return this.observation&&this.now<=this.observation.validUntil&&this.observation.configuration===this.configVersion?'有效':'证据过期或配置已变化，需重新检测';}
    confirm(a){
      const before=a.status;super.confirm(a);
      if(a.status==='succeeded'&&before!=='succeeded'){
        const m=this.active(a.memory_id);a.targetVerified=true;a.oldLocation=m.bodyLocation||'body://'+m.id+'/old';a.newLocation='body://'+m.id+'/'+a.to+'/v'+m.version;
        a.handoff=this.fault==='handoff'?'pending':'confirmed';a.cleanup='pending';
        if(a.handoff==='pending'){m.tier=a.from;a.status='handoff_pending';}
        else m.bodyLocation=a.newLocation;
      }
    }
    evaluate(m,reason,target){if(this.actions.some(a=>a.memory_id===m.id&&a.status==='handoff_pending'))return;return super.evaluate(m,reason,target);}
    handoff(id=this.lastAction){return this.run('Remember','确认正文引用交接',{action_id:id},()=>{const a=this.actions.find(a=>a.id===id),m=a&&this.active(a.memory_id);if(!a||!m||m.version!==a.version||!this.permits(this.context(m.owner,m.tenant,false),m,'write'))return this.denied('STALE_HANDOFF','当前资格或版本变化，禁止旧地址覆盖。');if(!a.targetVerified)return {status:'waiting',message:'目标正文尚未核验。'};a.handoff='confirmed';a.status='succeeded';m.bodyLocation=a.newLocation;m.tier=a.to;return {status:'completed',message:'B 已确认精确版本的正文引用生效；旧副本仍待清理。',action:copy(a)};});}
    cleanOld(id=this.lastAction){return this.run('Operate','旧副本清理',{action_id:id},()=>{const a=this.actions.find(a=>a.id===id);if(!a||a.handoff!=='confirmed')return {status:'waiting',message:'正文引用交接尚未确认，保留旧副本。'};a.cleanup='completed';return {status:'completed',message:'目标已生效，旧副本清理完成；向量索引位置未被覆盖。',action:copy(a)};});}
    sampleSignal(id=this.id('sample')){return this.run('RF','故障信号采样',{sample_id:id},()=>{
      if(this.signalSeen.includes(id))return {status:'completed',message:'相同样本去重，不重复累计。'};
      this.signalSeen.push(id);
      if(this.health.vector){this.signalCount=0;return {status:'healthy',message:'本次采样正常，连续失败次数归零。'};}
      if(this.now-this.lastSignalAt>30)this.signalCount=0;this.lastSignalAt=this.now;this.signalCount++;
      const existing=this.incidents.find(i=>i.state!=='resolved');
      if(existing||this.now<this.cooldownUntil)return {status:'waiting',message:existing?'已有未决异常，继续跟进原操作。':'处于冷却期，不创建重复维护。'};
      if(this.signalCount<2)return {status:'observing',message:'连续异常 1/2，尚未触发维护。'};
      const m=this.active();if(!m)return this.denied('NO_TARGET','先准备一个模拟业务对象。');
      const i={id:this.id('incident'),tenant:m.tenant,actor:m.owner,memory_id:m.id,state:'recovering',verification:'pending',operation_id:this.id('operation'),trace_id:this._trace};
      const t=this.task('maintenance',m);t.incident_id=i.id;i.task_id=t.id;this.incidents.push(i);
      this.maintenance.push({id:i.operation_id,incident_id:i.id,task_id:t.id,state:'accepted'});
      return {status:'recovering',message:'连续异常达到阈值，已通过公共任务机制登记维护。',incident:copy(i)};
    });}
    executeMaintenance(id=this.incidents.at(-1)?.id){return this.run('RF','执行维护任务',{incident_id:id},()=>{const i=this.incidents.find(i=>i.id===id);if(!i)return this.denied('NO_INCIDENT','暂无维护任务');const c=this.context(i.actor,i.tenant,false);if(c.status)return c;const t=this.tasks.find(t=>t.id===i.task_id);if(t.status==='completed')return {status:'completed',message:'原维护任务已完成，等待独立业务复验。'};t.status='completed';t.attempts++;i.state='verifying';i.verifyDeadline=this.now+30;this.maintenance.find(o=>o.id===i.operation_id).state='completed';return {status:'completed',message:'维护动作已完成；异常尚未关闭，等待独立业务复验。'};});}
    verifyBusiness(){return this.run('RF','独立业务复验',{},()=>{const i=this.incidents.at(-1);if(!i)return this.denied('NO_INCIDENT','暂无异常');if(i.state==='resolved')return {status:'resolved',message:'原异常已通过复验关闭。'};const c=this.context(i.actor,i.tenant,false);if(c.status){i.state='attention_required';return c;}if(this.tasks.find(t=>t.id===i.task_id)?.status!=='completed')return {status:'waiting',message:'维护尚未完成。'};if(this.health.vector&&this.businessVerified){i.state='resolved';i.verification='passed';i.proof=this.id('business-proof');this.cooldownUntil=this.now+30;return {status:'resolved',message:'独立检索与正文读取验证通过，异常正式关闭。',proof:i.proof};}i.state=this.now>i.verifyDeadline?'attention_required':'verifying';i.verification='unknown';return {status:i.state,message:i.state==='attention_required'?'复验超时，转人工排查并保留原操作。':'没有业务恢复证据，继续复验；进程健康不代替业务成功。'};});}
    setConfiguration(expected=this.configVersion,values={strategy:'baseline-v2'}){return this.run('RF','配置快照条件更新',{expected},()=>{if(expected!==this.configVersion)return {status:'conflict',message:'配置版本已变化，整批更新拒绝。'};if(values.maxBackground!==undefined&&(!Number.isInteger(values.maxBackground)||values.maxBackground<1))return {status:'failed',message:'资源配置无效，未部分应用。'};this.configHistory.push({version:this.configVersion,value:copy(this.configuration)});this.configuration={...this.configuration,...values};this.configVersion++;return {status:'configured',message:'配置快照更新，历史健康证据失效；不表示自动更换模型或数据库。',version:this.configVersion};});}
    createBackup(){return this.run('RF','模拟隔离备份',{},()=>{const b={id:this.id('backup'),version:this.configVersion,at:this.now,state:'verified',scope:'RF SQLite 范围模拟，不含外部向量库、日志库与模型',data:copy({memories:this.memories,tasks:this.tasks,outbox:this.outbox,incidents:this.incidents})};this.savedBackups.push(b);return {status:'completed',message:'生成模拟备份清单，隔离恢复不覆盖在线状态。',backup_id:b.id};});}
    restoreIsolated(id=this.savedBackups.at(-1)?.id){return this.run('RF','隔离恢复演练',{backup_id:id},()=>{const b=this.savedBackups.find(b=>b.id===id);if(!b)return this.denied('NO_BACKUP','请先建立备份');if(b.state!=='verified')return {status:'failed',message:'备份校验失败，拒绝恢复。'};const data=copy(b.data);for(const m of data.memories){const barrier=this.barriers[m.id];if(barrier?.deleted)m.state='deleted';else if(barrier?.minVersion>m.version)m.state='superseded';}this.isolatedRestore={id:this.id('restore'),backup_id:id,currentAuthorization:true,onlineOverwritten:false,data};return {status:'completed',message:'隔离副本已核验，使用当前授权与有效性屏障；在线数据保持原状。',restore_id:this.isolatedRestore.id};});}
    deriveFact(evidence=true){return this.run('Remember','从事件形成稳定事实',{sufficient_evidence:evidence},()=>{const m=this.active(),c=this.context();if(c.status)return c;if(!m||!this.permits(c,m,'write'))return this.denied('NO_SOURCE','没有获准来源');if(!evidence)return {status:'not_formed',message:'证据不足，保留事件，不生成确定事实。'};const fact={...copy(m),id:this.id('memory'),kind:'semantic',source:m.source+' / 事件证据',evidence:[m.id],origin_id:m.origin_id||m.id,origin_version:m.origin_version||m.version,key:this.id('fact'),projection:'pending'};delete fact.longterm_id;delete fact.chunks;this.memories.push(fact);this.lastMemory=fact.id;const t=this.task('extract-project',fact);return {status:'saved',memory_id:fact.id,task_id:t.id,message:'稳定事实已登记来源证据，独立形成并等待索引核验。'};});}
    expire(id=this.lastMemory){return this.run('Remember','有效期到期',{memory_id:id},()=>{const m=this.active(id),c=this.context();if(c.status)return c;if(!m||!this.permits(c,m,'write'))return this.denied('WRITE_DENIED','无权修改有效期');m.expiresAt=this.now;return {status:'expired',message:'记忆已到期，退出默认使用，原内容按保留规则处理。'};});}
    querySaved(key){return this.run('Remember','按原操作查询保存结果',{operation_key:key},()=>{const c=this.context();if(c.status)return c;const m=this.memories.find(m=>m.key===key&&m.owner===c.actor&&m.tenant===c.tenant);if(!m)return this.denied('RESULT_NOT_AVAILABLE','原结果不存在或无查询权限。');return {status:'saved',memory_id:m.id,version:m.version,message:'已找到原保存结果，不创建第二份记忆。'};});}
    waitTask(id=this.lastTask){return this.run('RF','保存等待与阶段断点',{task_id:id},()=>{const t=this.tasks.find(t=>t.id===id);if(!t)return this.denied('NO_TASK','没有任务');t.status='waiting';t.waitUntil=this.now+30;t.checkpoints=[{stage:'source_acquired',time:this.now,version:t.version}];t.heartbeat={worker:'worker-demo',time:this.now};return {status:'waiting',task_id:t.id,message:'来源阶段已保存断点与等待记录，下次检查前不重复执行。'};});}
  }
  if(typeof module!=='undefined')module.exports={PlatformModel};else root.PlatformModel=PlatformModel;
})(typeof window!=='undefined'?window:globalThis);
