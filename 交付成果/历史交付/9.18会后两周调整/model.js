/* Review-only stateful simulator. No network calls or production services. */
(function(root){
  const clone=x=>JSON.parse(JSON.stringify(x));
  class ReviewModel {
    constructor(){this.reset();}
    reset(){
      this.memories=[];this.objects={};this.vectors={};this.edges=[];this.events=[];
      this.operations={};this.results={};this.actions=[];this.decisions=[];this.seq=0;
      this.fault='none';this.last=null;this.storageEvents=0;this.readEvents=0;
      this.credential={tenant:'tenant-a',agent:'project-assistant',task:'aether-pilot'};
    }
    id(prefix){return prefix+'-'+String(++this.seq).padStart(3,'0');}
    log(owner,stage,detail,refs={}){this.events.push({seq:this.events.length+1,owner,stage,detail,...refs});}
    finish(value){this.last=clone(value);return value;}
    authorize(scope){return scope.tenant===this.credential.tenant&&scope.agent===this.credential.agent&&scope.task===this.credential.task;}
    current(id){return this.memories.find(m=>m.id===id&&m.state==='active');}
    write(text,{key='input-'+(this.seq+1),kind='working',scope=this.credential,source='项目会议记录'}={}){
      if(!this.authorize(scope))return this.finish({status:'rejected',code:'SCOPE_DENIED',message:'请求范围不在演示凭证授权内；未读写目标资源。'});
      if(!text.trim())return this.finish({status:'rejected',code:'EMPTY_CONTENT'});
      const signature=JSON.stringify({text,kind,scope,source});
      if(this.operations[key]){
        const old=this.operations[key];
        this.log('P3 · 接入','幂等检查',old.signature===signature?'返回原操作，无新增记忆':'同键不同内容，拒绝覆盖');
        return this.finish(old.signature===signature?{...clone(old.result),idempotent:true}:{status:'conflict',code:'IDEMPOTENCY_CONFLICT'});
      }
      if(this.fault==='object')return this.finish({status:'failed',code:'OBJECT_UNAVAILABLE',saved:false,message:'模拟 P2 对象保存失败，不能报告已保存。'});
      const id=this.id('mem'),op=this.id('op'),ref=id+'/v1';
      const m={id,version:1,text,kind,source,scope:clone(scope),state:'active',projection:'pending',tier:'warm',providerTier:'warm',routeEpoch:1,reads:0,cleanup:'none',objectRef:ref,representation:id+':canonical:v1'};
      this.memories.push(m);this.objects[ref]={body:text,version:1,readable:true};
      this.log('P3 · Remember','接收输入','校验身份、范围与幂等键',{memory_id:id,operation_id:op});
      this.log('P2 · E2','PutObject → Head/Get','模拟对象已保存，版本正文可以回读',{object_ref:ref});
      this.log('P3 · Remember','登记事实与任务','保存已完成；长期投影仍待处理',{memory_id:id,version:1});
      this.storageEvents++;
      const result={status:'saved',operation_id:op,memory_id:id,version:1,processing:'pending',long_term_ready:false,working_available:kind==='working',evidence:'simulated'};
      this.operations[key]={signature,result:clone(result)};this.observe(m,'存储：已保存');return this.finish(result);
    }
    process(id){
      const m=this.current(id);
      if(!m)return this.finish({status:'blocked',code:'NO_ACTIVE_VERSION'});
      if(m.projection==='ready')return this.finish({status:'ready',memory_id:id,version:m.version,idempotent:true});
      this.log('P3 · 加工','后台任务','按固定样例生成表示；本 Mock 不调用模型',{memory_id:id,version:m.version});
      if(this.fault==='vector'){
        m.projection='failed';this.log('P2 · E1','InsertVector 失败','原内容保留；不报告可长期检索');
        return this.finish({status:'processing_failed',saved:true,long_term_ready:false,code:'VECTOR_UNAVAILABLE'});
      }
      const object=this.objects[m.objectRef];
      if(!object?.readable||object.body!==m.text)return this.finish({status:'processing_failed',code:'CONTENT_MISMATCH'});
      this.vectors[m.objectRef]={memory_id:id,version:m.version,object_ref:m.objectRef,scope:clone(m.scope)};m.projection='ready';
      this.edges.push({from:m.id+':v'+m.version,to:m.source,type:'derived_from'});
      this.log('P2 · E1/E2','写入与回读核对','模拟向量引用、正文、版本一致；没有真实 ANN/Embedding');
      this.log('P3 · Remember','长期检索就绪','只授予当前版本长期查询资格',{memory_id:id,version:m.version});
      this.storageEvents++;this.observe(m,'存储：加工完成');
      return this.finish({status:'ready',memory_id:id,version:m.version,saved:true,long_term_ready:true,evidence:'simulated'});
    }
    matches(text,query){
      if(!query.trim())return true;
      if(/全部|项目|风险|约束|进度/.test(query))return true;
      if(/预算|金额/.test(query))return /预算|万元/.test(text);
      if(/会议|周五|汇总/.test(query))return /会议|周五|汇总/.test(text);
      return query.split(/[\s，。？?]+/).some(w=>w&&text.includes(w));
    }
    recall({query='当前项目有哪些约束？',sources='both',budget=260,session='session-2',scope=this.credential}={}){
      if(!this.authorize(scope)){
        this.log('P3 · 接入','授权拒绝','模拟凭证与请求范围不匹配，未执行候选查询');
        return this.finish({status:'unavailable',code:'SCOPE_DENIED',items:[],message:'不能通过修改租户或 Agent 字段扩大访问。'});
      }
      if(!Number.isFinite(budget)||budget<1)return this.finish({status:'unavailable',code:'INVALID_BUDGET',items:[]});
      const rid=this.id('recall');const chosen=sources==='auto'?(/历史|跨会话/.test(query)?['long_term']:['working','long_term']):sources==='both'?['working','long_term']:[sources];
      let missing=[];const candidates=[];
      this.log('P3 · Recall','绑定来源与预算','来源 '+chosen.join(' + ')+'；演示按 Unicode 字符计数',{recall_id:rid});
      for(const source of chosen){
        if((source==='long_term'&&this.fault==='vector')||this.fault==='all'){
          missing.push(source);this.log('P2 · E1','来源不可用','保留所选来源，不能静默改为完整成功');continue;
        }
        const list=this.memories.filter(m=>m.state==='active'&&this.authorize(m.scope)&&(source==='working'?m.kind==='working':m.projection==='ready')&&this.matches(m.text,query));
        this.log(source==='working'?'P3 · Remember':'P2 · E1','候选查询',source+' 返回 '+list.length+' 个模拟候选');
        for(const m of list)if(!candidates.some(x=>x.id===m.id))candidates.push(m);
      }
      const valid=candidates.filter(m=>{
        const o=this.objects[m.objectRef];
        if(this.fault==='object'||!o?.readable||o.version!==m.version||o.body!==m.text){missing.push(m.objectRef);return false;}
        return m.state==='active'&&this.authorize(m.scope);
      });
      this.log('P3 · Recall','版本与正文核验','去重后 '+valid.length+' 条合格；失效版本不能进入上下文');
      const budgetFacts=valid.filter(m=>/预算.*\d+\s*万/.test(m.text));
      const conflicting=new Set(budgetFacts.map(m=>(m.text.match(/(\d+)\s*万/)||[])[1])).size>1;
      let groups=[];
      if(conflicting)groups.push({members:budgetFacts,conflict:true});
      for(const m of valid)if(!conflicting||!budgetFacts.includes(m))groups.push({members:[m],conflict:false});
      let used=0,items=[],blocks=[],omitted=0;
      for(const g of groups){
        const block=(g.conflict?'【未裁决冲突：以下来源均有效，不能只保留一个结论】\n':'')+g.members.map(m=>m.text+' ['+m.id+' v'+m.version+' · '+m.source+']').join('\n');
        const cost=Array.from(block).length+(blocks.length?2:0);
        if(used+cost>budget){omitted+=g.members.length;continue;}
        used+=cost;blocks.push(block);
        items.push(...g.members.map(m=>({memory_id:m.id,version:m.version,text:m.text,source:m.source,object_ref:m.objectRef,tier:m.tier,conflict:g.conflict})));
      }
      const status=items.length?(missing.length?'degraded':'complete'):(missing.length||valid.length?'unavailable':'empty');
      const result={status,recall_id:rid,session,selected_sources:chosen,missing_sources:[...new Set(missing)],items,context:blocks.join('\n\n'),length:used,budget,length_unit:'Unicode 字符（仅演示，正式 tokenizer 待契约确定）',omitted,conflict:conflicting,code:items.length?null:valid.length?'ALL_GROUPS_OVER_BUDGET':missing.length?'REQUIRED_SOURCE_UNAVAILABLE':'NO_MATCH',evidence:'simulated'};
      this.results[rid]=clone(result);
      this.log('P3 · Recall','最终资格复核与交付',status+'；'+items.length+' 条；'+used+'/'+budget+' 字符',{recall_id:rid});
      for(const item of items){const m=this.current(item.memory_id);m.reads++;this.readEvents++;this.observe(m,'召回：本次已交付');}
      return this.finish(result);
    }
    update(id,text,expectedVersion){
      const m=this.current(id);if(!m)return this.finish({status:'blocked',code:'NO_ACTIVE_VERSION'});
      if(m.version!==expectedVersion)return this.finish({status:'conflict',code:'VERSION_CONFLICT',current_version:m.version});
      if(this.fault==='object')return this.finish({status:'failed',code:'OBJECT_UNAVAILABLE',message:'更正未保存，原版本仍有效。'});
      m.state='superseded';m.cleanup='pending';
      const v=m.version+1;const next={...clone(m),version:v,text,state:'active',projection:'pending',objectRef:id+'/v'+v,representation:id+':canonical:v'+v,reads:0,cleanup:'none',tier:'warm',providerTier:'warm',routeEpoch:1};
      this.memories.push(next);this.objects[next.objectRef]={body:text,version:v,readable:true};
      this.log('P3 · Remember','显式更正','同一记忆创建 v'+v+'；旧版退出召回；新投影待处理',{memory_id:id});
      this.storageEvents++;this.observe(next,'存储：更正');return this.finish({status:'saved',memory_id:id,version:v,previous_version:m.version,long_term_ready:false});
    }
    remove(id){
      const versions=this.memories.filter(m=>m.id===id);
      if(!versions.length)return this.finish({status:'not_found'});
      for(const m of versions){m.state='deleted';m.projection='invalid';m.cleanup='pending';}
      this.log('P3 · Remember','逻辑删除','各版本停止后续交付；物理清理另行跟踪',{memory_id:id});
      this.storageEvents++;
      this.decisions.push({memory_id:id,decision:'defer',reason:'存储：删除，目标已无效',inputs:{storage:this.storageEvents,recall:this.readEvents}});
      return this.finish({status:'logically_deleted',memory_id:id,blocked_for_new_reads:true,cleanup:'pending',evidence:'simulated'});
    }
    cleanup(id){
      for(const m of this.memories.filter(x=>x.id===id&&x.state!=='active')){delete this.objects[m.objectRef];delete this.vectors[m.objectRef];m.cleanup='completed';}
      this.log('P2 · E1/E2','清理核对','模拟索引与对象已移除；不代表备份和日志擦除');
      return this.finish({status:'cleanup_completed',scope:'仅浏览器内模拟对象和向量',memory_id:id});
    }
    replay(id){
      const r=this.results[id];if(!r)return this.finish({status:'not_found'});
      if(r.items.some(i=>!this.current(i.memory_id)||this.current(i.memory_id).version!==i.version)){
        this.log('P3 · Recall','历史结果复核','旧结果含失效版本，拒绝再次交付');
        return this.finish({status:'unavailable',code:'RESULT_INVALIDATED',items:[],message:'原上下文已失效，请发起新的 Recall。'});
      }
      this.log('P3 · Recall','查询原结果','未创建新的召回或访问事件');return this.finish({...clone(r),replayed:true});
    }
    observe(m,reason){
      const inputs={storage:this.storageEvents,recall:this.readEvents,version:m.version,policy:'mock-rule-1'};
      const pending=this.actions.find(a=>a.representation===m.representation&&['unknown','submitted'].includes(a.status));
      let decision=m.reads>=2&&m.tier==='warm'?'promote':'keep';
      if(this.fault==='capacity'||pending)decision='defer';
      this.decisions.push({memory_id:m.id,representation:m.representation,decision,reason,inputs});
      this.log('P3 · Operate','后台观察',reason+' → '+decision+'（样例规则：两次交付后温→热）');
      if(decision!=='promote')return;
      const action={id:this.id('act'),memory_id:m.id,version:m.version,representation:m.representation,from:m.tier,to:'hot',status:'submitted',expected_route_epoch:m.routeEpoch};
      this.actions.push(action);this.log('P2 · 执行替身','受理动作','原层仍可读，受理不是完成',{action_id:action.id});
      m.providerTier='hot'; // Simulated remote execution, independent from acknowledgement.
      if(this.fault==='ack'){action.status='unknown';this.log('P3 · Operate','回执丢失','保持正在核对，不把 Unknown 改为失败或成功',{action_id:action.id});}
      else this.confirm(action);
    }
    confirm(a){
      const m=this.current(a.memory_id);
      if(!m||m.version!==a.version){a.status='stale';this.log('P3 · Operate','迟到结果隔离','对象已更正或删除，旧动作不能激活对象');return;}
      if(m.providerTier!==a.to||!this.objects[m.objectRef]?.readable){a.status='unknown';return;}
      m.tier=a.to;m.routeEpoch++;a.status='succeeded';a.observed_route_epoch=m.routeEpoch;
      this.log('P2 · 执行替身','状态与读取确认','模拟目标正文可读，路由版本更新；下一次读取使用热层',{action_id:a.id});
    }
    reconcile(){
      const unresolved=this.actions.filter(a=>a.status==='unknown');
      unresolved.forEach(a=>this.confirm(a));
      return this.finish({status:unresolved.length?'reconciled':'no_pending_action',actions:clone(unresolved),new_actions_created:0,evidence:'simulated'});
    }
    p2inspect(){
      const m=this.memories.find(x=>x.state==='active');
      if(!m)return this.finish({status:'empty',message:'先提交并加工一条记忆。'});
      const vector=this.vectors[m.objectRef];
      this.log('P2 · E2/E1/E3','三引擎关联检查','按同一对象版本核对正文、向量引用和来源关系');
      return this.finish({status:'inspected',object_service:{rpc:'HeadObject / GetObject',ref:m.objectRef,readable:!!this.objects[m.objectRef],body:this.objects[m.objectRef]?.body},vector_service:{rpc:'SearchVector',method:'固定引用匹配，未运行 ANN',hits:vector?[clone(vector)]:[]},graph_service:{rpc:'Traverse',edges:this.edges.filter(e=>e.from===m.id+':v'+m.version)},evidence:'simulated',contract_note:'此聚合视图不是 P2 的实际 RPC 响应 Schema。'});
    }
    snapshot(){return clone({mode:'mock',memories:this.memories,events:this.events,decisions:this.decisions,actions:this.actions,last:this.last});}
  }
  if(typeof module!=='undefined')module.exports={ReviewModel};else root.ReviewModel=ReviewModel;
})(typeof window!=='undefined'?window:globalThis);
