(function(root){
  const legacy=typeof module!=='undefined'?require('./review-content.js'):root.P3ReviewContent;
  const step=(title,explain,act)=>({title,explain,act});
  const scenes=legacy.scenes.map(s=>({...s,steps:[...s.steps]}));
  const recovery=scenes.find(s=>s.id==='S11');
  recovery.steps.splice(4,1,step('模拟依赖重新可用','只改变依赖状态，不直接恢复业务任务。',m=>m.restoreDependency('worker')),step('检测并判断原任务恢复','依赖可用后核验原主体、版本与原任务。',m=>{m.probe();m.recover();}));
  scenes.find(s=>s.id==='S10').steps[4]=step('使用合格压缩表示','结果明确标注表示类型、来源及版本。',m=>m.recall({sources:'long_term',representation:'compressed'}));
  const conflictScene=scenes.find(s=>s.id==='S08'),originalConflictSetup=conflictScene.setup;
  conflictScene.setup=m=>{originalConflictSetup(m);m.memories.filter(x=>/预算/.test(x.text)).forEach(x=>x.conflictKey='budget');};
  const seed=m=>{m.remember('项目预算20万元，必须保留审批记录。');m.process();};
  const add=(id,title,sub,fr,steps,setup=seed,phase='MVP')=>scenes.push({id,title,sub,fr,adr:['D02','D03','D06','D07'],steps,setup,phase});
  add('S13','块命中与完整正文','多个块，一个记忆候选；补取后再组包',['FR07','FR08','FR09','FR10'],[
    step('检索多个块，按记忆占名额','同一记忆的多个块只占一个候选；K=2 时有界补取第二条。',m=>m.recall({sources:'long_term',query:'项目',topK:2,chunkBatch:2,budget:700})),
    step('缩小补取额度','额度不足要说明范围，不能伪装完整成功。',m=>m.recall({sources:'long_term',query:'项目',topK:2,chunkBatch:1,maxBatches:1,budget:700})),
    step('仅查当前，不调用编码器','Working 路径独立，计划中显示未调用编码器。',m=>m.recall({sources:'working',query:'当前项目',budget:700})),
    step('切换自动来源：当前任务','按当前任务线索选择 Working。',m=>m.recall({sources:'auto',query:'当前项目'})),
    step('切换自动来源：历史会议','按历史线索选择长期来源。',m=>m.recall({sources:'auto',query:'历史项目'}))
  ],m=>{m.remember('项目甲预算20万元。采购需要审批。每周五汇报进度。任何更改都要保留原始来源和负责人。',{kind:'episodic',chunkSize:12});m.process();m.remember('项目乙预算15万元。',{kind:'episodic'});m.process();m.remember('当前项目目标是准备发布。');});
  add('S14','不完整组与降级交付','失败发生在一个组，不丢弃其他可信组',['FR08','FR09','FR10'],[
    step('展示完整冲突组','预算两种说法共同呈现，并保留独立的汇报要求。',m=>m.recall({sources:'long_term',query:'项目',budget:600})),
    step('模拟冲突一方正文缺失','整组退出，其他独立可信材料可降级交付。',m=>{m.memories.find(x=>x.conflictKey==='budget').readable=false;m.recall({sources:'long_term',query:'项目',budget:600});}),
    step('全部正文不可用','不能将缺失解释为正常空。',m=>{m.health.object=false;m.recall({sources:'long_term',query:'项目'});}),
    step('恢复正文读取','重新核验后再交付。',m=>{m.restoreDependency('object');m.memories.forEach(x=>x.readable=true);m.recall({sources:'long_term',query:'项目',budget:600});})
  ],m=>{for(const text of ['项目预算20万元。','项目预算15万元。','项目每周五汇报。']){m.remember(text,{kind:'episodic'});m.process();if(text.includes('预算'))m.active().conflictKey='budget';}});
  add('S15','索引批次与模型空间','完整核验后发布，同空间查询',['FR07','FR08'],[
    step('部分块尚未核验','预期块未全部确认，不能 Ready。',m=>{m.fault='partial_index';m.process();}),
    step('长期查询不使用未发布批次','待加工记忆不充当合格候选。',m=>m.recall({sources:'long_term',query:'项目'})),
    step('补齐核验并发布','同一原任务继续，发布完整块批次。',m=>{m.fault='none';m.process();}),
    step('核对查询与入库空间','计划显示共享模型空间、块与记忆版本。',m=>m.recall({sources:'long_term',query:'项目',budget:600}))
  ],m=>m.remember('项目需要按完整版本核验。所有块完成后才能发布长期检索资格。',{kind:'episodic',chunkSize:10}));
  add('S16','监测、处置与独立复验','维护完成不等于业务恢复',['FR18'],[
    step('第一次异常采样','连续异常未达阈值，不立即创建维护。',m=>{m.health.vector=false;m.sampleSignal('first');}),
    step('重复样本不重复计数','同一个样本只计算一次。',m=>m.sampleSignal('first')),
    step('第二次异常触发维护','异常、原操作与公共任务关联。',m=>m.sampleSignal('second')),
    step('完成维护动作','任务完成，但异常仍在复验。',m=>m.executeMaintenance()),
    step('先做一次业务复验','没有独立业务证据，异常不能关闭。',m=>m.verifyBusiness()),
    step('模拟依赖恢复','健康恢复还不是业务验证通过。',m=>{m.restoreDependency('vector');m.probe();}),
    step('独立检索与正文验证通过','生成业务证明，异常闭环；开始冷却。',m=>{m.businessVerified=true;m.verifyBusiness();}),
    step('冷却期故障不重复处置','保留记录，避免反复创建维护。',m=>{m.health.vector=false;m.sampleSignal('third');m.sampleSignal('fourth');})
  ]);
  add('S17','复验超时转人工','原任务与原操作始终可追踪',['FR18'],[
    step('创建并执行维护','模拟维护完成，业务仍不可用。',m=>{m.health.vector=false;m.sampleSignal('a');m.sampleSignal('b');m.executeMaintenance();}),
    step('等待复验期限','超过本场景配置的虚拟期限。',m=>m.advance(40)),
    step('复验转人工','证据不足，保留原操作并明确待人工排查。',m=>m.verifyBusiness()),
    step('持续异常不重复创建操作','未决事件只关联原任务，不另起冲突维护。',m=>m.sampleSignal('c'))
  ]);
  add('S18','迁移与正文引用交接','目标生效和旧副本清理分开',['FR13','FR14'],[
    step('周期评估触发相邻迁移','模拟目标已经可读，但 B 引用交接暂时失败。',m=>{m.fault='handoff';m.schedule('up');}),
    step('交接前尝试清理','旧引用未更新，旧副本必须保留。',m=>m.cleanOld()),
    step('B 确认精确版本正文引用','不重新搬运，不更换原动作。',m=>{m.fault='none';m.handoff();}),
    step('新位置生效后读取','旧副本待清理不阻止已核验目标使用。',m=>m.recall({sources:'working'})),
    step('清理旧副本','完成清理，与目标生效状态分别记录。',m=>m.cleanOld())
  ]);
  add('S19','配置、健康与隔离恢复','版本冲突、证据失效和备份边界',['FR15','FR17','FR18'],[
    step('建立健康证据','证据绑定配置版本与有效期。',m=>m.probe()),
    step('条件更新配置','记录旧版本，新配置使旧健康证据失效。',m=>m.setConfiguration()),
    step('旧版本再次提交','拒绝过期配置写入，不部分应用。',m=>m.setConfiguration(1)),
    step('创建模拟备份','仅展示 RF 数据范围与校验清单。',m=>m.createBackup()),
    step('备份后删除原内容','在线立即阻断，清理另行执行。',m=>m.mutate('delete')),
    step('隔离恢复演练','不覆盖在线库，不复活删除内容或旧许可。',m=>m.restoreIsolated()),
    step('查询在线状态','仍然无法获取已删除正文。',m=>m.recall())
  ]);
  add('S20','当前任务与稳定事实','作用域、到期、证据不足与正式事实',['FR02','FR04','FR05'],[
    step('当前任务读取','同一任务内可读当前目标。',m=>m.recall({sources:'working'})),
    step('切换任务范围','另一任务不能把旧 Working 当自己的当前目标。',m=>m.recall({sources:'working',session:'other'})),
    step('证据不足不形成事实','保留原事件和未形成原因。',m=>m.deriveFact(false)),
    step('有依据时形成稳定事实','事实独立编号并保留来源关联。',m=>m.deriveFact(true)),
    step('发布事实索引','完成后按长期范围读取。',m=>{m.process();m.recall({sources:'long_term'});}),
    step('事实到期','到期内容退出默认使用。',m=>m.expire()),
    step('重新查询长期内容','仅使用仍有效且获授权的内容。',m=>m.recall({sources:'long_term'}))
  ]);
  add('S21','读取、交付与缓存回源','命中不计读取，交付不重复计热',['FR02','FR10','FR12'],[
    step('读取正文但预算放不下','真实读取留痕；未交付不能伪装已经 packed。',m=>m.recall({sources:'long_term',budget:1})),
    step('正常组包交付','read 与 packed 分别记录，不重复计热。',m=>m.recall({sources:'long_term',budget:600})),
    step('缓存有效时使用当前内容','模拟权威正文源故障，有效且同版本缓存仍可读。',m=>{m.health.object=false;m.memories.forEach(x=>x.cacheValid=true);m.recall({sources:'working'});}),
    step('缓存失效且回源失败','不能把失效缓存当完整正文交付。',m=>{m.memories.forEach(x=>x.cacheValid=false);m.recall({sources:'working'});})
  ]);
  const names=['保存事件与任务信息','当前任务记忆','长文本与文档处理','记忆分类与生命周期','稳定事实与更正冲突','内容提纯与压缩','长期检索就绪','选择并查询记忆来源','排序去重与冲突表达','组装并交付上下文','查询处理与再次获取结果','记录存储与召回轨迹','基础调度算法决策','执行调整与结果确认','租户隔离与访问授权','预测访问与预测预热','删除与使用阻断','运行查询与异常恢复'];
  add('S22','保存结果不明与原操作查询','响应中断不重复写入',['FR01','FR11','FR18'],[
    step('保存后模拟响应中断','客户端只看到结果不明，不假报保存成功。',m=>{m.fault='save_ack';m.remember('项目保存响应中断样例',{key:'save-once'});}),
    step('按原操作查询','找到原事实，不重复提交新的保存。',m=>{m.fault='none';m.querySaved('save-once');}),
    step('按原任务继续加工','原操作与原任务保持关联。',m=>m.process())
  ],()=>{});
  add('S23','等待、断点与当前配置','恢复不能绕过最新执行约束',['FR03','FR18'],[
    step('记录阶段断点与等待','等待及阶段输出属于原任务，显示下次检查时刻。',m=>m.waitTask()),
    step('等待期不重复执行','未到时间仍保持等待。',m=>m.process()),
    step('等待结束后禁用生成策略','当前配置约束优先于原任务快照。',m=>{m.advance(40);m.setConfiguration(m.configVersion,{enabled:false});m.process();}),
    step('恢复获准配置后继续','沿用原任务与阶段来源，完成后记录 completed 断点。',m=>{m.setConfiguration(m.configVersion,{enabled:true});m.process();})
  ],m=>m.remember('项目阶段断点样例。'));
  const requirements=names.map((name,i)=>({id:'FR'+String(i+1).padStart(2,'0'),name,section:'7.'+(i+2),scenes:scenes.filter(s=>s.fr.includes('FR'+String(i+1).padStart(2,'0'))).map(s=>s.id),phase:i===15?'后续 P1':'MVP',status:'目标行为模拟'}));
  const flowCoverage=[
    ['P3.1–P3.2','系统交接、可信身份与租户授权',['S01','S09']],['P3.3','事务、任务、事件及恢复',['S03','S11']],['P3.4','日志与 Trace',['S11','S21']],['P3.5','健康、处置、独立业务复验',['S16','S17']],['P3.6','配置、备份与隔离恢复',['S19']],
    ['B.1–B.3','保存、来源/版本、Working 与缓存',['S01','S03','S20','S21']],['B.4–B.5','加工、质量拒绝与稳定事实',['S10','S20']],['B.6','同空间编码、块核验及 Ready',['S13','S15']],['B.7–B.8','生命周期、删除、更正与恢复',['S02','S05','S11','S20']],['B.9–B.10','能力及正文引用交接',['S07','S18']],
    ['A.1 / A.1a','自动/显式来源、编码与块检索',['S04','S13','S15']],['A.1c','按记忆聚合与有界补取',['S13']],['A.1b','完整正文、表示选择与回源',['S10','S14','S21']],['A.1d','完整关系组、预算和最终核验',['S08','S14','S01']],['A.2–A.5','历史结果、恢复、能力与交接',['S02','S04','S07','S09']],
    ['C.1–C.2','事件/周期评估、相邻层与资源约束',['S03','S06','S12']],['C.3','执行、Unknown 对账与引用交接',['S06','S18']],['C.4','内部能力和双侧轨迹',['S06','S21']]
  ];
  const nonfunctional=[
    ['10.1','Working 核心读写','P99 < 10 ms','目标环境与负载下测量，不等于整次 Recall 延迟'],
    ['10.1','语义计算','≥ 2000 条/秒','需真实编码计算，缓存命中不能代替'],
    ['FR06 / 10.1','内容压缩','≥ 5 倍且质量通过','同一集合实际字节；失败样本不能剔除'],
    ['FR13 / 10.1','基础策略','规则正确率 100%，错误降层为 0','需真实策略场景与执行结果'],
    ['FR16 / 10.1','预测收益','相对提升 ≥ 10%','后续 P1；基础值为 0 不计算相对提升'],
    ['10.7','持续运行','MVP ≥ 24 小时；Final ≥ 72 小时','需真实环境；业务可用才算恢复'],
    ['10.5–10.6','可靠性与扩展','幂等、隔离、一致性及主流程资源保障','模拟异常分支；真实进程、磁盘、扩容另验'],
    ['11 / 12.8','安全与隐私','租户隔离、最小日志、撤权及删除','两租户双 Agent；真实身份/P2 另验'],
    ['9 / 13','接入与交付','接口、配置、诊断、发布与恢复说明','不将此观察台扩展为 PRD 必需的管理产品'],
    ['14.2','交付前业务细则','身份传播时限、数据/策略/运行口径','保留待确认项，模拟参数不冒充合同承诺']
  ];
  const api={scenes,requirements,flowCoverage,nonfunctional,decisions:legacy.decisions};
  if(typeof module!=='undefined')module.exports=api;else root.PlatformContent=api;
})(typeof window!=='undefined'?window:globalThis);
