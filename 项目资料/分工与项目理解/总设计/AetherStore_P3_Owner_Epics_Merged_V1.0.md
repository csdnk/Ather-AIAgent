# AetherStore P3 · 三负责人 Epic 合并版 V1.0

# 1. Person A — Recall / Context Serving

**Primary Outcome**：从 Query 到最终 Context Pack 的完整读路径正确、可降级、可追踪；Recall 最终结果错误找 A。

| Epic ID | 最终 Epic | 合并后的 Epic 范围 | 并入的 PRR Action | Epic 级完成标准 |
|---|---|---|---|---|
| **A-EPIC-01** | **Semantic Compute & Resource Isolation** | passage/query 两类 Embedding；model contract/version/dimension；真实向量；吞吐与资源隔离；模型变更安全 | A-P0-2、A-P0-3、A-P0-5 | passage/query 语义清晰；query 不被 passage/后台抢占；隔离态与混合负载 Profile 均满足吞吐目标；模型/维度变化可 Shadow/Canary/Rollback |
| **A-EPIC-02** | **Vector Projection Mechanism** | VectorProjectionPort；五元组幂等；ProviderResult 五态；写入/query/delete/version/schema | — | ProviderResult=ACCEPTED/PENDING/READY/FAILED/UNKNOWN 均有明确语义；A 不写 ProjectionState；版本/维度错误不误报 Ready |
| **A-EPIC-03** | **Vector Backend Simulator** | 有状态向量模拟；index_ready；幂等；版本/维度；超时/失败/stale/partial/unknown 故障注入 | — | 可独立验证 VectorProjection / Search 契约与失败链；不依赖真实 P2；不实现真实 ANN |
| **A-EPIC-04** | **Query Runtime & Ingress Protection** | Query→Query Embedding；契约校验；入口限流；Query Embedding 队列上限；Deadline | A-P0-1 | 突发流量下有明确 admission；429/Busy 语义稳定；队列有上限；限流时前台 P99 不失控 |
| **A-EPIC-05** | **Candidate Retrieval & Search Degradation** | VectorSearchPort；TopK；filter；稳定引用；partial/degraded；Search 后端半故障/不可用降级 | A-P1-3 | Search 超时/不可用不冒充完整；降级阈值明确；结果携带稳定引用和版本；可输出 recall_availability |
| **A-EPIC-06** | **Candidate Validation & Reference Integrity** | 调用 B 的 MemoryReadCapability；校验 Memory state/version/scope/conflict/evidence；悬空引用检测 | A-P1-2 | 只使用当前有效版本；object_ref / graph_node_ref 等引用悬空可识别并显式处理；不因脏候选产生完整结果 |
| **A-EPIC-07** | **Canonical Load** | 经 B 的 mapping + ContentStorePort 读取原文；checksum/not_found/partial | — | canonical payload 加载失败可定位；checksum/not_found/partial 不被静默吞掉；A 不拥有物理 payload 事实 |
| **A-EPIC-08** | **Rank / Decay / Conflict Execution** | 消费 B 提供的语义属性、衰减/冲突/证据规则执行排序 | — | B 定义 Memory Domain 属性/规则，A 对 Recall 执行结果负责；排序可解释、可追踪输入来源 |
| **A-EPIC-09** | **Context Assembly** | Context Pack；Token Budget；complete/degraded/failed；truncated；missing_sources | — | Context Pack 状态正确；缺失源与预算截断显式；不能把 degraded 冒充 complete |
| **A-EPIC-10** | **Recall Observability, SLO, Audit & Acceptance** | Recall trace；degraded/缺失源告警；E2E SLO/availability；结果重放审计；Benchmark/Acceptance Evidence | A-P0-4、A-P1-1、A-P1-4（并承接 A-P0-5 的发布验收证据） | Query→Context Pack trace_id 贯通；complete/degraded/failed、缺失源、projection/model/version 可查；SLO/availability 有口径；告警可触发；同 trace 可定位/重放；Lane 2 与 Lane 3 验收证据分离 |

## A 的最终 Epic 结构

```text
A-EPIC-01 Semantic Compute & Resource Isolation
A-EPIC-02 Vector Projection Mechanism
A-EPIC-03 Vector Backend Simulator
A-EPIC-04 Query Runtime & Ingress Protection
A-EPIC-05 Candidate Retrieval & Search Degradation
A-EPIC-06 Candidate Validation & Reference Integrity
A-EPIC-07 Canonical Load
A-EPIC-08 Rank / Decay / Conflict Execution
A-EPIC-09 Context Assembly
A-EPIC-10 Recall Observability, SLO, Audit & Acceptance
```

---

# 2. Person B — Remember / Memory Formation

**Primary Outcome**：Memory 可靠形成、可追踪、可版本化、可被 Recall 使用；Memory 没形成找 B。

| Epic ID | 最终 Epic | 合并后的 Epic 范围 | 并入的 PRR Action | Epic 级完成标准 |
|---|---|---|---|---|
| **B-EPIC-01** | **Ingest & Fact Formation** | MemoryEvent；Fact-First；幂等；scope/provenance；MemoryRecord；Success vs Accepted | — | 主事实可靠落点后才 Success；派生未完成只能 Accepted/Partial；重复事件不产生重复主事实 |
| **B-EPIC-02** | **Lifecycle & Policy Versioning** | Working/Episodic/Semantic 逻辑语义；Active/Archived/Superseded/Expired/Deleted；分类；版本；冲突；衰减相关策略版本 | B-P0-5（衰减/策略部分） | 生命周期状态机可测；uncertain→Working；冲突不静默；策略变更可追踪并可回退 |
| **B-EPIC-03** | **Durable Content & Capacity Guard** | Memory→content_ref mapping；ContentStorePort；Content Store Simulator；durable/checksum/head；容量 guard；超时后确认 | B-P0-3、B-P1-3（checksum/head 部分） | Durable Store 容量满时明确拒绝并告警；写超时通过 head 收敛；checksum 可校验；B 不拥有物理 payload 事实 |
| **B-EPIC-04** | **Memoryize / Compression & Background Capacity** | 异步 Memoryize；压缩 Artifact；持久 Task；后台 worker 并发/队列预算；节流；压缩策略发布 | B-P0-1、B-P0-5（压缩部分）、B-P1-3（Compression checksum 部分） | 压缩达到合同目标；后台工作不拖垮 Working 前台；前台越线时后台可降速/暂停；策略可受控发布/回退 |
| **B-EPIC-05** | **Make Recallable & Projection Health** | ProjectionState Building→Ready；调用 A Embedding/Vector Projection；ProviderResult 五态；版本校验；Stale/Rebuild；Projection 健康告警 | B-P0-4 | 只有 B 能写 ProjectionState；Provider 未 READY 不转 Ready；Pending/Stale 积压可观测/告警；restart/version mismatch 可恢复 |
| **B-EPIC-06** | **MemoryRead Capability** | 面向 A 提供 scope/filter/version/state/confidence/stability/decay input/conflict/evidence | — | A 不感知 B 内部 Redis/DB/schema；返回权威 Memory facts；partial/degraded 有明确语义 |
| **B-EPIC-07** | **MemorySignal Capability** | B→C signal；signal schema/version；Submitted；补发/确认语义 | — | Submitted≠已消费；重启后未完成 signal 可恢复/补发；C 不需要理解 B 内部存储 |
| **B-EPIC-08** | **Recovery / Reconciliation & Retry Governance** | unfinished Task；Projection Pending；content write unknown；service restart；duplicate event；stale version；Retry Budget；Redis 半故障；Working×migration 共存；双 Owner 窗口 | B-P0-2、B-P1-1、B-P1-2、B-P1-3（删除级联/超时收敛部分） | Retry 有预算、退避、jitter；半故障有进入/退出阈值；重启后任务恢复且不重复主事实；写 unknown 可查询收敛；旧版本不覆盖新版本；迁移/双 Owner 窗口下 Working P99 与事实正确性可验收 |

## B 的最终 Epic 结构

```text
B-EPIC-01 Ingest & Fact Formation
B-EPIC-02 Lifecycle & Policy Versioning
B-EPIC-03 Durable Content & Capacity Guard
B-EPIC-04 Memoryize / Compression & Background Capacity
B-EPIC-05 Make Recallable & Projection Health
B-EPIC-06 MemoryRead Capability
B-EPIC-07 MemorySignal Capability
B-EPIC-08 Recovery / Reconciliation & Retry Governance
```

---

# 3. Person C — Operate / Optimize

**Primary Outcome**：MemorySignal / AccessTrace 到 Placement 决策、TierAction、Feedback、Reconciliation 完整闭环，并形成可验证优化效果；TierAction 一直 Unknown 找 C。

| Epic ID | 最终 Epic | 合并后的 Epic 范围 | 并入的 PRR Action | Epic 级完成标准 |
|---|---|---|---|---|
| **C-EPIC-01** | **Signal / Trace Consumption** | 消费 B MemorySignal + A/B AccessTrace；事件接收、去重、可追溯 | — | Submitted≠已消费；输入可重放/追踪；不把 Signal/Trace schema ownership 吞到 C 内 |
| **C-EPIC-02** | **Representation Hotness** | per-representation hotness；重要度/频次/时间衰减/访问行为 | — | 不再使用笼统 “Memory hot”；同一 Memory 的不同 representation 可产生不同热度 |
| **C-EPIC-03** | **RepresentationPlacementPlan** | 正式 Domain Fact；observed/desired tier；target；generation；hotness；reason；policy version | — | entries[] 字段完整；决策有 reason/version；Plan 可审计、可重算、可作为 TierAction 输入 |
| **C-EPIC-04** | **Actuation Target Resolve** | provider_ref/representation_ref→opaque ActuationTarget；不绑定 Segment | — | P3 不按 provider-specific target_type 分支；target 可稳定用于后续动作 |
| **C-EPIC-05** | **Scheduling View & Capacity Observation** | PlacementState；SegmentIntrospection（当前 Provider 观测）；真实 current_tier；容量水位 | C-P1-3 | 只消费真实 observed state，不自己伪造 current_tier；容量水位支持 nearfull 等分级行为 |
| **C-EPIC-06** | **Placement Decision, Capacity Guard & Admission Control** | desired tier；Keep/No-op；cooldown/threshold；Capacity Guard；带宽/并发预算；Admission；per-Scope 配额 | C-P0-1、C-P0-2、C-P1-4 | 目标层容量不足不发动作；前台 SLO 恶化可 Throttle/Pause/Reject New；动作预算有界；公平/配额规则可解释 |
| **C-EPIC-07** | **TierAction Runtime & Operator Control** | Generated/Submitted/Succeeded/Failed/Unknown；Provider 状态映射；动作提交；Operator Pause/Resume/Cancel/Pin/Force Keep；迁移完成语义 Consumer Requirement | C-P0-3、C-P0-4、C-P1-5（状态时限部分） | accepted≠success；人工控制有权限/幂等/Audit/re-takeover；TierAction 收敛时限可测；迁移完成语义由 C 提 Consumer Requirement、P2 实现物理事务 |
| **C-EPIC-08** | **Storage Control Simulator** | 统一 Stateful Simulator；共享 SimulatedStorageState；Actuation/Placement/Action/Feedback/Health 六类控制能力；Case 1–6 | — | Case 1–6 全部可重现；正常模式 world state 一致；Fault Mode 才允许 response lost/feedback lost/placement stale；不复刻 P2 |
| **C-EPIC-09** | **Feedback / Reconciliation & Migration Safety** | Unknown query；反馈丢失；stale placement；generation mismatch；retry；Stall；in-flight 收敛 | C-P0-5、C-P1-1、C-P1-2、C-P1-5（收敛 SLO 部分） | Unknown 先 query 不盲重试；Stall 可检测；generation/in-flight 冲突可收敛；重启后 action 可恢复；收敛时限可量化 |
| **C-EPIC-10** | **Optimize / Predict & Controlled Release** | heuristic baseline；feature contract；prediction；policy/version；Shadow→Canary/Progressive→Rollback；fallback | C-P0-6 | 不直接跳 RL；Hit Rate 相对 Heuristic +10% 可审计；模型/策略可受控接入；异常时可回退到上一稳定→Heuristic→Keep/No-op |

## C 的最终 Epic 结构

```text
C-EPIC-01 Signal / Trace Consumption
C-EPIC-02 Representation Hotness
C-EPIC-03 RepresentationPlacementPlan
C-EPIC-04 Actuation Target Resolve
C-EPIC-05 Scheduling View & Capacity Observation
C-EPIC-06 Placement Decision, Capacity Guard & Admission Control
C-EPIC-07 TierAction Runtime & Operator Control
C-EPIC-08 Storage Control Simulator
C-EPIC-09 Feedback / Reconciliation & Migration Safety
C-EPIC-10 Optimize / Predict & Controlled Release
```

---

# 4. 合并后的责任视图

| 负责人 | Primary Outcome | Epic 数量 | 生产重点 |
|---|---|---:|---|
| **A** | Recall / Context Serving | 10 | 前台容量隔离、入口保护、Search 降级、SLO/告警、模型发布安全 |
| **B** | Remember / Memory Formation | 8 | Fact-First、后台节流、Durable Guard、Projection 健康、Retry/Recovery |
| **C** | Operate / Optimize | 10 | Capacity/Admission、Operator Control、迁移收敛、Stall/Unknown、策略发布/回退 |

---
