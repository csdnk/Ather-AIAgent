# AetherStore P3 Engineering WBS V1.0

> 唯一架构基线：V0.4.1 四份冻结文件（Work Map / Register / Mock Strategy / Consistency Check）。
> 本轮只做执行拆解：不研究架构、不映射员工、不排日期。
> 责任模型：PROGRAM B=Remember(B)、PROGRAM A=Recall(A)、PROGRAM C=Operate/C(C)、SHARED=Runtime Foundation + External Integration。
> B1/B2/B3 = Technical Capability / Code Stewardship（非 Primary Ownership，仅出现在 Contributor 列）。

---

## 0. Program / Epic 总览

| Program | Primary DRI | Epic | 任务数 | 依赖 P2 | 可即日开工 |
|---|---|---|---|---|---|
| B Remember | B | B-EPIC-01..08 | 33 | OBJ-001 / VEC-001(经A) | ✅ |
| A Recall | A | A-EPIC-01..10 | 28 | VEC-001/002、OBJ-001(经B) | ✅ |
| C Operate | C | C-EPIC-01..10 | 24 | ACT/SEG/PLC/TIER-001/002/003、HLT | ✅ |
| S Runtime Foundation | Steward | S-EPIC-01..06 | 9 | 无 | ✅ |
| I External Integration | Steward | I-EPIC-01..05 | 5 | 全部（仅 Lane 3 等待） | ✅(维护/包/追踪可先做) |

> 规则：依赖 P2 ≠ Task Blocked。除 Lane 3 Integration Gate 外，其余均走 REAL PATH + DEVELOPMENT PATH 双路径，`Can Start Without P2 = YES`。

---

## PROGRAM B — Remember（Primary DRI = B）

### B-EPIC-01 · Memory Ingest & Fact Formation

#### B-EPIC-01-T1 · Fact-First Write Sequence Design — DESIGN
- **DRI/Contributor**：B / B2
- **Input**：MemoryEvent 语义、RequestContext 契约
- **Deliverable**：Fact-First 写入序列文档（校验→主事实→Task→派生）
- **Owned State**：MemoryRecord（B）
- **Internal Dep**：S-EPIC-05（RequestContext/幂等元数据）
- **External Dep/Port/REQ**：OBJ-001（canonical 落点，可 mock）
- **Mock**：Content Store Simulator
- **Can Start Without P2**：YES
- **DoD**：序列定义含"主事实落点后才报成功；否则 Accepted/Partial"
- **Lane**：L1
- **Failure Cases**：主事实落点失败、幂等重复、校验失败
- **Evidence**：设计文档 + 评审记录
- **Handoff To**：B-EPIC-01-T2/T3/T4
- **Non-Resp**：不定义 P2 对象存储内部实现

#### B-EPIC-01-T2 · MemoryEvent Validation & Idempotency — IMPLEMENTATION
- **DRI/Contributor**：B / B2
- **Input**：MemoryEvent、幂等键
- **Deliverable**：校验+幂等实现（scope/来源/时间/幂等键）
- **Owned State**：事件接收结果
- **Internal Dep**：B-EPIC-01-T1
- **Can Start Without P2**：YES
- **DoD**：同幂等键不重复建 Memory/长期 Task；无效事件返回稳定错误码
- **Lane**：L1/L2
- **Failure Cases**：重复事件、越权、缺 Scope
- **Evidence**：单测 + Contract Test
- **Handoff To**：B-EPIC-01-T3
- **Non-Resp**：不定义上层 Agent 平台逻辑

#### B-EPIC-01-T3 · MemoryRecord Creation & Provenance — IMPLEMENTATION
- **DRI/Contributor**：B / B2
- **Deliverable**：MemoryRecord 创建（版本+来源证据+业务状态）
- **Owned State**：MemoryRecord=Active
- **Internal Dep**：B-EPIC-01-T2、S-EPIC-01（状态目录）
- **Can Start Without P2**：YES
- **DoD**：主事实带 Original/canonical content_uri；派生关系可追溯
- **Lane**：L1
- **Failure Cases**：缺 content_uri、版本冲突
- **Evidence**：单测
- **Handoff To**：B-EPIC-02、B-EPIC-05
- **Non-Resp**：不拥有 canonical 字节物理事实

#### B-EPIC-01-T4 · Success vs Accepted Semantics — STATE MACHINE
- **DRI/Contributor**：B / B2
- **Deliverable**：写入返回语义（Success/Accepted/Partial/Degraded）
- **Owned State**：response_status
- **Internal Dep**：B-EPIC-01-T1、S-EPIC-01
- **Can Start Without P2**：YES
- **DoD**：主事实可靠落点后才 Success；派生未完成返回 Accepted/Partial 并列待处理项
- **Lane**：L1/L2
- **Failure Cases**：派生失败不回滚主事实
- **Evidence**：状态机测试
- **Handoff To**：A（Recall 读路径）
- **Non-Resp**：不负责派生（Embedding/Projection）的执行机制

### B-EPIC-02 · Memory Lifecycle

#### B-EPIC-02-T1 · Lifecycle State Machine — STATE MACHINE
- **DRI/Contributor**：B / B2
- **Deliverable**：Active/Archived/Superseded/Expired/Deleted 状态机
- **Owned State**：Memory 业务状态
- **Internal Dep**：S-EPIC-01
- **Can Start Without P2**：YES
- **DoD**：五状态转移可测试；删除≠静默删
- **Lane**：L1
- **Failure Cases**：错误状态跳转、越权删除
- **Evidence**：状态机测试
- **Handoff To**：B-EPIC-02-T2/T3
- **Non-Resp**：不定义物理删除实现

#### B-EPIC-02-T2 · Classification (Working/Episodic/Semantic) — IMPLEMENTATION
- **DRI/Contributor**：B / B2
- **Deliverable**：Memory Type 判定（规则→Hint→轻量分类器→uncertain 兜底）
- **Owned State**：Memory Type
- **Can Start Without P2**：YES
- **DoD**：uncertain 按 Working 管理；分类结果含 classification_source/policy_version
- **Lane**：L1
- **Failure Cases**：无法判定→uncertain
- **Evidence**：单测
- **Handoff To**：B-EPIC-02-T3
- **Non-Resp**：不冻结具体模型结构/阈值

#### B-EPIC-02-T3 · Version & Conflict — DOMAIN MODEL
- **DRI/Contributor**：B / B2
- **Deliverable**：版本化 + 更正→新版本+Superseded + 冲突不静默覆盖
- **Owned State**：Memory 版本/冲突状态
- **Can Start Without P2**：YES
- **DoD**：矛盾时保留双方证据并返回 Conflict/Degraded
- **Lane**：L1/L2
- **Failure Cases**：多源矛盾、旧版本覆盖新版本
- **Evidence**：单测 + Contract Test
- **Handoff To**：A（MemoryRead）
- **Non-Resp**：不替 A 做 Recall 排序

### B-EPIC-03 · Durable Content

#### B-EPIC-03-T1 · ContentStorePort Contract — PORT CONTRACT
- **DRI/Contributor**：B / B2
- **Deliverable**：ContentStorePort 契约（put/get/head/delete/checksum/durable）
- **Owned State**：content_ref 映射
- **External Dep/REQ**：OBJ-001
- **Mock**：Content Store Simulator
- **Can Start Without P2**：YES
- **DoD**：durable/checksum/head 语义冻结；物理权威=Durable Store Provider
- **Lane**：L2（Contract Test）
- **Failure Cases**：checksum mismatch、not_found、partial
- **Evidence**：Contract Test（mock+real 复用）
- **Handoff To**：B-EPIC-03-T2/T3
- **Non-Resp**：不实现对象存储内核

#### B-EPIC-03-T2 · Content Store Simulator — SIMULATOR
- **DRI/Contributor**：B / B2
- **Deliverable**：Content Store Simulator（durable/checksum/head/partial）
- **Owned State**：模拟对象状态
- **Mock Red Line**：behaviorally complete，不复刻 P2
- **Can Start Without P2**：YES
- **DoD**：durable 前 head 不可见；写超时后 head 可验证
- **Lane**：L2
- **Failure Cases**：durable/not_found/超时/部分写/checksum mismatch
- **Evidence**：Simulator 测试
- **Handoff To**：B-EPIC-03-T4
- **Non-Resp**：不实现真实对象存储

#### B-EPIC-03-T3 · Memory→content_ref Mapping — IMPLEMENTATION
- **DRI/Contributor**：B / B2
- **Deliverable**：Memory→content_ref 映射（B 拥有）
- **Owned State**：映射关系
- **Can Start Without P2**：YES
- **DoD**：MemoryRecord 可解析到 content_ref；映射可追踪
- **Lane**：L1
- **Failure Cases**：映射缺失
- **Evidence**：单测
- **Handoff To**：A（CanonicalLoad）
- **Non-Resp**：不拥有 payload 物理字节

#### B-EPIC-03-T4 · Write-Timeout Reconciliation — RECOVERY
- **DRI/Contributor**：B / B2
- **Deliverable**：写超时后 head 确认真实存在→收敛
- **Owned State**：content 写状态
- **Internal Dep**：S-EPIC-03
- **Can Start Without P2**：YES
- **DoD**：写超时不假设成功/失败，head 验证后收敛
- **Lane**：L2/L3
- **Failure Cases**：写超时但已落、写超时但未落
- **Evidence**：Fault 测试
- **Handoff To**：—
- **Non-Resp**：—

### B-EPIC-04 · Memoryize / Compression

#### B-EPIC-04-T1 · Async Memoryize Task Design — DESIGN
- **DRI/Contributor**：B / B2 + Steward
- **Deliverable**：异步 Task 设计（提纯/压缩/Artifact）
- **Owned State**：Task（B 记录）
- **Internal Dep**：S-EPIC-02（Durable Task/Outbox）
- **Can Start Without P2**：YES
- **DoD**：Task 稳定 ID+状态+恢复语义；Celery 仅执行通道
- **Lane**：L1
- **Failure Cases**：Task 落库未投递、重复投递
- **Evidence**：设计文档
- **Handoff To**：B-EPIC-04-T2/T3
- **Non-Resp**：不定义 Celery 内部

#### B-EPIC-04-T2 · Artifact & Compression — IMPLEMENTATION
- **DRI/Contributor**：B / B2
- **Deliverable**：压缩 Artifact + 持久化字节口径（≥5x）
- **Owned State**：Artifact
- **Can Start Without P2**：YES
- **DoD**：Original/Compressed 真实持久化；失败保 Original
- **Lane**：L1/L2
- **Failure Cases**：压缩失败、质量不达标
- **Evidence**：单测 + 指标证据
- **Handoff To**：B-EPIC-04-T3
- **Non-Resp**：不冻结具体压缩算法

#### B-EPIC-04-T3 · Memoryize Retry & Recovery — RECOVERY
- **DRI/Contributor**：B / B2
- **Deliverable**：任务重试/恢复（幂等键+版本检查）
- **Internal Dep**：S-EPIC-02/S-EPIC-03
- **Can Start Without P2**：YES
- **DoD**：重启后任务可恢复；重复投递不重复执行
- **Lane**：L2/L3
- **Failure Cases**：重启丢失、重复、版本变化
- **Evidence**：Fault 测试
- **Handoff To**：—
- **Non-Resp**：—

### B-EPIC-05 · Make Recallable（写路径，Projection Ready 归 B）

#### B-EPIC-05-T1 · Projection Build Sequence — DESIGN
- **DRI/Contributor**：B / B2
- **Deliverable**：Projection Build 序列（Building→A机制→ProviderResult→版本校验→Ready）
- **Owned State**：ProjectionState（B 唯一）
- **Internal Dep**：A-EPIC-01（Embedding）、A-EPIC-02（Vector mechanism）
- **Can Start Without P2**：YES
- **DoD**：序列写死"只有 B 能转 Ready"；A 禁止写 ProjectionState
- **Lane**：L1
- **Failure Cases**：双写 ProjectionState
- **Evidence**：设计文档 + ADR
- **Handoff To**：B-EPIC-05-T2..T6
- **Non-Resp**：不实现向量写机制（A 提供）

#### B-EPIC-05-T2 · ProjectionBuildCapability Contract — INTERNAL CONTRACT
- **DRI/Contributor**：B（编排）+ A（机制）
- **Deliverable**：ProjectionBuildCapability 契约（五元组幂等键 + ProviderResult）
- **Owned State**：ProviderResult=A；ProjectionState=B
- **Can Start Without P2**：YES
- **DoD**：ProviderResult 五态（ACCEPTED/PENDING/READY/FAILED/UNKNOWN）定义完整
- **Lane**：L1
- **Failure Cases**：版本不匹配、ProviderResult 语义漂移
- **Evidence**：契约文档
- **Handoff To**：A-EPIC-02
- **Non-Resp**：A 不拥有 ProjectionState

#### B-EPIC-05-T3 · Passage Embedding Invoke — IMPLEMENTATION
- **DRI/Contributor**：B / B2（调用 A 的 SemanticEmbeddingCapability）
- **Deliverable**：passage embedding 调用（usage=Passage）
- **Internal Dep**：A-EPIC-01
- **Can Start Without P2**：YES
- **DoD**：不感知 A 的 ONNX/SIMD/backend 细节
- **Lane**：L1/L2
- **Failure Cases**：embedding 失败→Projection 保持 Pending/Retryable
- **Evidence**：集成测试
- **Handoff To**：B-EPIC-05-T4
- **Non-Resp**：不实现 embedding 推理

#### B-EPIC-05-T4 · VectorProjectionPort Invoke + ProviderResult Handling — IMPLEMENTATION
- **DRI/Contributor**：B / B2（调用 A 的 VectorProjectionPort）
- **Deliverable**：写 Projection + ProviderResult 处理
- **Owned State**：ProjectionState（B）
- **Internal Dep**：A-EPIC-02
- **External Dep/REQ**：VEC-001
- **Can Start Without P2**：YES
- **DoD**：五态均有处理；timeout 可 query provider 状态
- **Lane**：L2
- **Failure Cases**：ACCEPTED 后失败、UNKNOWN、stale
- **Evidence**：Contract Test + Fault 测试
- **Handoff To**：B-EPIC-05-T5
- **Non-Resp**：不写 Provider 内部索引

#### B-EPIC-05-T5 · ProjectionState Transition — STATE MACHINE
- **DRI/Contributor**：B / B2
- **Deliverable**：ProjectionState 转移（只有 B 校验版本后转 Ready）
- **Owned State**：ProjectionState
- **Internal Dep**：S-EPIC-01
- **Can Start Without P2**：YES
- **DoD**：memory_version/model_version/schema_version 校验通过才 Ready；version mismatch 不转 Ready
- **Lane**：L1/L2
- **Failure Cases**：版本不匹配、后端缺失
- **Evidence**：状态机测试
- **Handoff To**：A（Recall 使用 Ready Projection）
- **Non-Resp**：不拥有 Provider 索引

#### B-EPIC-05-T6 · Projection Rebuild & Stale — RECOVERY
- **DRI/Contributor**：B / B2
- **Deliverable**：重建/失效（版本变化→Stale→重建）
- **Internal Dep**：S-EPIC-03
- **Can Start Without P2**：YES
- **DoD**：旧版本标记 Stale/Superseded；旧 Task 不覆盖新版本
- **Lane**：L2/L3
- **Failure Cases**：残留 Projection、后端缺失
- **Evidence**：Fault 测试
- **Handoff To**：—
- **Non-Resp**：—

### B-EPIC-06 · MemoryRead Capability（B→A 内部能力）

#### B-EPIC-06-T1 · MemoryReadCapability Contract — INTERNAL CONTRACT
- **DRI/Contributor**：B（Provider）/ A（Consumer）
- **Deliverable**：MemoryRead 契约（scope/filter/version/state/语义属性/降级）
- **Owned State**：Memory 事实（B）
- **Can Start Without P2**：YES
- **DoD**：A 不需知道 B 内部 Redis/DB/schema
- **Lane**：L1
- **Failure Cases**：契约漂移
- **Evidence**：契约文档
- **Handoff To**：A-EPIC-06
- **Non-Resp**：A 不重写 Memory 逻辑

#### B-EPIC-06-T2 · Filter / Version / State — IMPLEMENTATION
- **DRI/Contributor**：B / B2
- **Deliverable**：过滤（scope/权限/状态/版本/有效期）
- **Can Start Without P2**：YES
- **DoD**：只返回有效版本；Deleted/Superseded 默认排除
- **Lane**：L1
- **Failure Cases**：越权读取、返回旧版本
- **Evidence**：单测
- **Handoff To**：A
- **Non-Resp**：不替 A 排序

#### B-EPIC-06-T3 · Semantic Attributes — IMPLEMENTATION
- **DRI/Contributor**：B / B2
- **Deliverable**：confidence/stability/decay inputs/conflict/evidence 输出
- **Owned State**：属性（B 定义）
- **Can Start Without P2**：YES
- **DoD**：属性含策略版本；不替代权限检查
- **Lane**：L1
- **Failure Cases**：属性缺失→显式降级
- **Evidence**：单测
- **Handoff To**：A-EPIC-08（Rank/Decay/Conflict）
- **Non-Resp**：A 执行最终排序

### B-EPIC-07 · MemorySignal（B→C）

#### B-EPIC-07-T1 · MemorySignal Schema — INTERNAL CONTRACT
- **DRI/Contributor**：B（Provider）/ C（Consumer）
- **Deliverable**：signal schema（memory_id/type/importance/lifecycle/scope 摘要/访问/版本）
- **Owned State**：signal（B 发送）
- **Can Start Without P2**：YES
- **DoD**：signal_version 可追踪；Submitted ≠ 已消费
- **Lane**：L1
- **Failure Cases**：重复 signal 导致重复调度
- **Evidence**：契约文档
- **Handoff To**：C-EPIC-01
- **Non-Resp**：C 不拥有 signal 事实

#### B-EPIC-07-T2 · Signal Submission & Retry — IMPLEMENTATION
- **DRI/Contributor**：B / B2
- **Deliverable**：触发/补发（状态变化→signal；恢复后补发）
- **Owned State**：signal Pending/Submitted/Succeeded/Retryable
- **Can Start Without P2**：YES
- **DoD**：C 不可用时主记录不回滚；恢复后补发
- **Lane**：L1/L2
- **Failure Cases**：C 不可用、重复发送
- **Evidence**：单测 + 集成测试
- **Handoff To**：C
- **Non-Resp**：不负责调度决策

### B-EPIC-08 · Remember Recovery / Reconciliation

#### B-EPIC-08-T1 · Unfinished Task Recovery — RECOVERY
- **DRI/Contributor**：B / B2 + Steward
- **Deliverable**：重启后未完成 Task 恢复（读持久化状态，幂等重试）
- **Internal Dep**：S-EPIC-02
- **Can Start Without P2**：YES
- **DoD**：Task 非 Celery 状态；重启后按幂等+版本收敛
- **Lane**：L2/L3
- **Failure Cases**：重启丢失、重复投递
- **Evidence**：Fault 测试（独立进程 Simulator）
- **Handoff To**：—
- **Non-Resp**：—

#### B-EPIC-08-T2 · Projection Pending Reconcile — RECONCILIATION
- **DRI/Contributor**：B / B2
- **Deliverable**：Projection Pending 对账（对比 B 记录/Task/后端）
- **Internal Dep**：S-EPIC-03
- **Can Start Without P2**：YES
- **DoD**：Ready 但后端缺失→降级/失效→重建/补登记
- **Lane**：L2/L3
- **Failure Cases**：后端缺失、版本不匹配、残留
- **Evidence**：Fault 测试
- **Handoff To**：—
- **Non-Resp**：—

#### B-EPIC-08-T3 · Content Write Unknown Reconcile — RECONCILIATION
- **DRI/Contributor**：B / B2
- **Deliverable**：content 写未知→head 确认真实状态
- **Internal Dep**：S-EPIC-03
- **Can Start Without P2**：YES
- **DoD**：不假设成功/失败；head 验证后收敛
- **Lane**：L2/L3
- **Failure Cases**：写超时但已落/未落
- **Evidence**：Fault 测试
- **Handoff To**：—
- **Non-Resp**：—

#### B-EPIC-08-T4 · Duplicate & Stale Version Reconcile — RECONCILIATION
- **DRI/Contributor**：B / B2
- **Deliverable**：重复事件/旧版本收敛
- **Can Start Without P2**：YES
- **DoD**：重复幂等键不重复建；旧版本不覆盖新版本
- **Lane**：L1/L2
- **Failure Cases**：重复事件、版本竞态
- **Evidence**：单测 + Fault 测试
- **Handoff To**：—
- **Non-Resp**：—

---

## PROGRAM A — Recall（Primary DRI = A）

### A-EPIC-01 · Semantic Compute Shared Capability

#### A-EPIC-01-T1 · SemanticEmbeddingCapability Contract — INTERNAL CONTRACT
- **DRI/Contributor**：A（Provider）/ B（Consumer）
- **Deliverable**：Embedding 契约（usage passage/query、model_contract、source_hash、timeout）
- **Owned State**：向量输出（A）
- **Can Start Without P2**：YES
- **DoD**：B 不感知 ONNX/SIMD/backend；返回合法真实向量+model_version+dimension
- **Lane**：L1
- **Failure Cases**：mock 向量不得 PASS、维度不一致
- **Evidence**：契约文档 + 验收证据
- **Handoff To**：B-EPIC-05-T3、A-EPIC-04
- **Non-Resp**：不拥有 Memory 事实

#### A-EPIC-01-T2 · Passage/Query Usage Distinction — DESIGN
- **DRI/Contributor**：A / B1
- **Deliverable**：usage 语义（Passage 建投影 / Query 检索）
- **Can Start Without P2**：YES
- **DoD**：同检索空间维度/版本一致、可比较
- **Lane**：L1
- **Failure Cases**：usage 契约漂移
- **Evidence**：设计文档
- **Handoff To**：A-EPIC-01-T3/T4
- **Non-Resp**：不负责排序/去重/衰减

#### A-EPIC-01-T3 · Model Contract & Version — IMPLEMENTATION
- **DRI/Contributor**：A / B1
- **Deliverable**：model_version/dimension/source_hash 追踪 + 幂等复用
- **Owned State**：模型版本
- **Can Start Without P2**：YES
- **DoD**：版本/文本/chunk/usage 变化→重算；否则复用
- **Lane**：L1/L2
- **Failure Cases**：版本漂移、hash 碰撞
- **Evidence**：单测
- **Handoff To**：—
- **Non-Resp**：—

#### A-EPIC-01-T4 · Real Embedding & Performance Profile — PERFORMANCE TEST
- **DRI/Contributor**：A / B1
- **Deliverable**：Effective Embedding Throughput ≥2000 item/s + P95/P99 证据
- **Owned State**：性能证据
- **Can Start Without P2**：YES
- **DoD**：记录模型版本/维度/硬件/batch/并发/warm-up/错误率
- **Lane**：L1
- **Failure Cases**：mock 冒充、吞吐不达标
- **Evidence**：Benchmark 报告
- **Handoff To**：项目负责人（Review）
- **Non-Resp**：—

### A-EPIC-02 · Vector Projection Mechanism

#### A-EPIC-02-T1 · VectorProjectionPort Contract — PORT CONTRACT
- **DRI/Contributor**：A / B1
- **Deliverable**：VectorProjectionPort 契约（upsert/delete/get + 五元组幂等）
- **Owned State**：ProviderResult（A）
- **External Dep/REQ**：VEC-001
- **Mock**：Vector Backend Simulator
- **Can Start Without P2**：YES
- **DoD**：ProviderResult 五态；A 不拥有 ProjectionState
- **Lane**：L2（Contract Test）
- **Failure Cases**：维度不匹配、版本不匹配
- **Evidence**：Contract Test
- **Handoff To**：B-EPIC-05-T4
- **Non-Resp**：不写 ProjectionState（B 拥有）

#### A-EPIC-02-T2 · ProviderResult Handling — STATE MACHINE
- **DRI/Contributor**：A / B1
- **Deliverable**：ACCEPTED/PENDING/READY/FAILED/UNKNOWN 处理
- **Owned State**：ProviderResult
- **Can Start Without P2**：YES
- **DoD**：timeout 可 query；UNKNOWN 不盲重试
- **Lane**：L2
- **Failure Cases**：UNKNOWN、stale、schema 不符
- **Evidence**：状态机测试
- **Handoff To**：B（B 转 ProjectionState）
- **Non-Resp**：不转 ProjectionState

#### A-EPIC-02-T3 · Upsert/Query/Delete — IMPLEMENTATION
- **DRI/Contributor**：A / B1
- **Deliverable**：写/查/删 + 版本/维度/schema 校验
- **Can Start Without P2**：YES
- **DoD**：幂等 upsert；删除传播
- **Lane**：L2
- **Failure Cases**：重复 upsert、schema 变化
- **Evidence**：单测 + Contract Test
- **Handoff To**：A-EPIC-05
- **Non-Resp**：—

### A-EPIC-03 · Vector Backend Simulator

#### A-EPIC-03-T1 · Stateful Vector State — SIMULATOR
- **DRI/Contributor**：A / B1
- **Deliverable**：Vector Backend Simulator（vector/version/dimension/index_ready）
- **Owned State**：模拟向量状态
- **Can Start Without P2**：YES
- **DoD**：同幂等键不重复；index_ready 语义正确
- **Lane**：L2
- **Failure Cases**：维度/版本 mismatch、超时、stale、partial、unknown
- **Evidence**：Simulator 测试
- **Handoff To**：A-EPIC-03-T2、B-EPIC-05-T4
- **Non-Resp**：不复刻真实 ANN

#### A-EPIC-03-T2 · Fault Scenarios — SIMULATOR
- **DRI/Contributor**：A / B1
- **Deliverable**：故障注入（写失败/超时/stale/索引未就绪）
- **Can Start Without P2**：YES
- **DoD**：Fault 与正常模式可区分
- **Lane**：L3（注入）
- **Failure Cases**：见上
- **Evidence**：Fault 测试
- **Handoff To**：—
- **Non-Resp**：—

### A-EPIC-04 · Query Runtime

#### A-EPIC-04-T1 · Query → Query Embedding — RUNTIME
- **DRI/Contributor**：A / B1
- **Deliverable**：query embedding 调用（usage=Query）
- **Internal Dep**：A-EPIC-01
- **Can Start Without P2**：YES
- **DoD**：维度/版本与检索空间一致
- **Lane**：L1/L2
- **Failure Cases**：维度不一致→稳定错误码
- **Evidence**：单测
- **Handoff To**：A-EPIC-05
- **Non-Resp**：—

#### A-EPIC-04-T2 · Vector Contract Validation — IMPLEMENTATION
- **DRI/Contributor**：A / B1
- **Deliverable**：查询向量契约校验
- **Can Start Without P2**：YES
- **DoD**：校验失败返回稳定错误
- **Lane**：L1
- **Failure Cases**：维度/模型版本不符
- **Evidence**：单测
- **Handoff To**：—
- **Non-Resp**：—

### A-EPIC-05 · Candidate Retrieval

#### A-EPIC-05-T1 · VectorSearchPort Contract — PORT CONTRACT
- **DRI/Contributor**：A / B1
- **Deliverable**：VectorSearchPort 契约（filter/TopK/稳定引用/降级）
- **External Dep/REQ**：VEC-002
- **Mock**：Vector Backend Simulator
- **Can Start Without P2**：YES
- **DoD**：degraded/partial 语义；超时不冒充完整
- **Lane**：L2
- **Failure Cases**：无候选/部分候选/超时
- **Evidence**：Contract Test
- **Handoff To**：A-EPIC-05-T2/T3
- **Non-Resp**：—

#### A-EPIC-05-T2 · Filter / TopK / Stable Reference — IMPLEMENTATION
- **DRI/Contributor**：A / B1
- **Deliverable**：候选检索 + object_ref/graph_node_ref 稳定引用 + score/version
- **Can Start Without P2**：YES
- **DoD**：引用可追踪；版本可校验
- **Lane**：L2
- **Failure Cases**：引用悬空
- **Evidence**：单测 + Contract Test
- **Handoff To**：A-EPIC-06
- **Non-Resp**：—

#### A-EPIC-05-T3 · Degraded / Partial / Timeout — RECOVERY
- **DRI/Contributor**：A / B1
- **Deliverable**：降级语义处理
- **Can Start Without P2**：YES
- **DoD**：超时返回显式 degraded/partial，不冒充完整
- **Lane**：L2
- **Failure Cases**：超时、后端不可用
- **Evidence**：Fault 测试
- **Handoff To**：A-EPIC-09
- **Non-Resp**：—

### A-EPIC-06 · Candidate Validation + Memory Read

#### A-EPIC-06-T1 · MemoryReadCapability Invoke — IMPLEMENTATION
- **DRI/Contributor**：A（Consumer）/ B（Provider）
- **Deliverable**：调用 MemoryRead 校验候选（version/state/scope/conflict/evidence/staleness）
- **Internal Dep**：B-EPIC-06
- **Can Start Without P2**：YES
- **DoD**：只使用当前有效版本 Ready Projection 对应 Memory
- **Lane**：L1/L2
- **Failure Cases**：旧版本、Superseded、冲突
- **Evidence**：集成测试
- **Handoff To**：A-EPIC-07
- **Non-Resp**：不重写 Memory 过滤逻辑

### A-EPIC-07 · Canonical Load

#### A-EPIC-07-T1 · CanonicalLoadCapability Invoke — IMPLEMENTATION
- **DRI/Contributor**：A（Consumer）/ B（Mapping 提供）
- **Deliverable**：content_ref→payload 加载（checksum/not_found/partial）
- **Internal Dep**：B-EPIC-03（Mapping）、ContentStorePort
- **External Dep/REQ**：OBJ-001
- **Can Start Without P2**：YES
- **DoD**：checksum 校验；not_found/partial 显式处理
- **Lane**：L1/L2
- **Failure Cases**：checksum mismatch、not_found
- **Evidence**：集成测试
- **Handoff To**：A-EPIC-08
- **Non-Resp**：不拥有 payload 物理事实

### A-EPIC-08 · Rank / Decay / Conflict

#### A-EPIC-08-T1 · Consume B Policy/Attributes — IMPLEMENTATION
- **DRI/Contributor**：A（执行）/ B（定义能力）
- **Deliverable**：消费 confidence/stability/decay/conflict 属性执行排序
- **Internal Dep**：B-EPIC-06-T3
- **Can Start Without P2**：YES
- **DoD**：A 对 Recall E2E 结果负责；策略由 B 定义、A 消费
- **Lane**：L1/L2
- **Failure Cases**：属性缺失→显式降级
- **Evidence**：单测 + 集成测试
- **Handoff To**：A-EPIC-09
- **Non-Resp**：B 不执行最终排序

### A-EPIC-09 · Context Assembly

#### A-EPIC-09-T1 · Context Pack Assembly — IMPLEMENTATION
- **DRI/Contributor**：A / B2（协作）
- **Deliverable**：Context Pack（条目+memory_id+Type+evidence+版本+冲突提示）
- **Owned State**：Context 结果（A）
- **Can Start Without P2**：YES
- **DoD**：complete/degraded/failed + recall_availability 正确
- **Lane**：L1/L2
- **Failure Cases**：缺失源、冲突、预算截断
- **Evidence**：单测 + E2E
- **Handoff To**：A-EPIC-09-T2
- **Non-Resp**：—

#### A-EPIC-09-T2 · Token Budget & Truncation — IMPLEMENTATION
- **DRI/Contributor**：A
- **Deliverable**：Token Budget 校验 + 截断（truncated=true + 原因）
- **Can Start Without P2**：YES
- **DoD**：Budget≤0→INVALID_ARGUMENT；截断不破坏单条证据可追踪
- **Lane**：L1
- **Failure Cases**：缺 Budget、静默丢弃冲突
- **Evidence**：单测
- **Handoff To**：—
- **Non-Resp**：—

### A-EPIC-10 · Recall Observability + Acceptance

#### A-EPIC-10-T1 · Recall Trace — OBSERVABILITY
- **DRI/Contributor**：A / Steward
- **Deliverable**：Recall 可解释（用了哪个 projection/model、哪些源缺失、为什么 degraded、哪个 Memory 被过滤）
- **Internal Dep**：S-EPIC-04
- **Can Start Without P2**：YES
- **DoD**：trace_id 贯通 Query→Embedding→Search→Read→Load→Rank→Pack
- **Lane**：L1/L2
- **Failure Cases**：trace 断裂
- **Evidence**：Trace 样例
- **Handoff To**：A-EPIC-10-T2
- **Non-Resp**：—

#### A-EPIC-10-T2 · Recall Acceptance — E2E TEST
- **DRI/Contributor**：A
- **Deliverable**：Recall E2E 验收（正确/降级/追踪）
- **Can Start Without P2**：YES（L2 可先做）
- **DoD**：L2 Contract PASS；L3 真实 P2 后 PASS
- **Lane**：L2/L3
- **Failure Cases**：召回错误可定位到步骤
- **Evidence**：E2E 测试 + 证据
- **Handoff To**：项目负责人（Review）
- **Non-Resp**：—

---

## PROGRAM C — Operate / Optimize（Primary DRI = C）

### C-EPIC-01 · Signal / Trace Consumption

#### C-EPIC-01-T1 · MemorySignal Consume — IMPLEMENTATION
- **DRI/Contributor**：C / B3
- **Deliverable**：消费 MemorySignal（去重、signal_version）
- **Internal Dep**：B-EPIC-07
- **Can Start Without P2**：YES
- **DoD**：Submitted ≠ 已确认消费；重复 signal 不重复调度
- **Lane**：L1/L2
- **Failure Cases**：重复 signal、缺 signal_version
- **Evidence**：集成测试
- **Handoff To**：C-EPIC-02
- **Non-Resp**：不拥有 signal 事实

#### C-EPIC-01-T2 · AccessTrace Consume — IMPLEMENTATION
- **DRI/Contributor**：C（Consumer）/ A、B（Producer）/ Steward（Schema）
- **Deliverable**：消费 AccessTrace（hit/miss/context usage）
- **Internal Dep**：S-EPIC-04
- **Can Start Without P2**：YES
- **DoD**：Schema/Ingest 契约由 Steward 拥有；trace 可复现
- **Lane**：L1/L2
- **Failure Cases**：trace 缺失、版本漂移
- **Evidence**：集成测试
- **Handoff To**：C-EPIC-02
- **Non-Resp**：不拥有 Trace Store（共享）

### C-EPIC-02 · Representation Hotness

#### C-EPIC-02-T1 · Per-Representation Hotness — DOMAIN MODEL
- **DRI/Contributor**：C / B3
- **Deliverable**：per-representation hotness 模型（非笼统 Memory hot）
- **Owned State**：hotness_score（C）
- **Can Start Without P2**：YES
- **DoD**：每条 representation 独立 hotness；含权重/衰减/唤醒频次/重要度
- **Lane**：L1
- **Failure Cases**：把 Memory hot 当成单一事实
- **Evidence**：单测
- **Handoff To**：C-EPIC-03
- **Non-Resp**：—

### C-EPIC-03 · RepresentationPlacementPlan

#### C-EPIC-03-T1 · RepresentationPlacementPlan Domain Object — DOMAIN MODEL
- **DRI/Contributor**：C / B3
- **Deliverable**：冻结 Domain Object（memory_id + entries[] 全字段）
- **Owned State**：RepresentationPlacementPlan（C）
- **Can Start Without P2**：YES
- **DoD**：字段完整（representation_id/type/provider_ref/observed_tier/desired_tier/actuation_target_ref/target_generation/hotness_score/decision_reason/policy_version/generated_at/valid_until）
- **Lane**：L1
- **Failure Cases**：字段缺失
- **Evidence**：单测
- **Handoff To**：C-EPIC-03-T2
- **Non-Resp**：—

#### C-EPIC-03-T2 · Plan Build Runtime — RUNTIME
- **DRI/Contributor**：C / B3
- **Deliverable**：Signal/AccessTrace → Plan 的运行时
- **Can Start Without P2**：YES
- **DoD**：Plan 是 Signal/AccessTrace → decision → TierAction 之间的正式 Domain Fact
- **Lane**：L1/L2
- **Failure Cases**：缺输入→Keep/No-op
- **Evidence**：单测
- **Handoff To**：C-EPIC-04
- **Non-Resp**：—

### C-EPIC-04 · Actuation Target Resolve

#### C-EPIC-04-T1 · ActuationTargetResolvePort — PORT CONTRACT
- **DRI/Contributor**：C / B3
- **Deliverable**：ActuationTargetResolvePort 契约（provider_ref/representation_ref + type → opaque target）
- **External Dep/REQ**：ACT-001
- **Mock**：Storage Control Simulator
- **Can Start Without P2**：YES
- **DoD**：P3 自己负责 memory_id→representation_id→provider_ref；不绑定 Segment
- **Lane**：L2
- **Failure Cases**：unresolvable
- **Evidence**：Contract Test
- **Handoff To**：C-EPIC-04-T2
- **Non-Resp**：不设计 P2 内部 target 实现

#### C-EPIC-04-T2 · Consume Opaque ActuationTarget — IMPLEMENTATION
- **DRI/Contributor**：C / B3
- **Deliverable**：消费 opaque target（不按 target_type 分支）
- **Can Start Without P2**：YES
- **DoD**：policy 禁止按 provider-specific target_type 分支
- **Lane**：L2
- **Failure Cases**：按 target_type 分支（违反铁律）
- **Evidence**：单测 + 评审
- **Handoff To**：C-EPIC-06
- **Non-Resp**：—

### C-EPIC-05 · Scheduling View / Observation

#### C-EPIC-05-T1 · PlacementStatePort Consume — IMPLEMENTATION
- **DRI/Contributor**：C / B3
- **Deliverable**：消费真实 observed tier（get_current_placement）
- **External Dep/REQ**：PLC-001
- **Mock**：Storage Control Simulator
- **Can Start Without P2**：YES
- **DoD**：只消费不拥有；unknown 标 Unknown 不假设覆盖
- **Lane**：L2
- **Failure Cases**：unknown、stale
- **Evidence**：Contract Test
- **Handoff To**：C-EPIC-06
- **Non-Resp**：不拥有层级事实

#### C-EPIC-05-T2 · SegmentIntrospectionPort Consume — IMPLEMENTATION
- **DRI/Contributor**：C / B3
- **Deliverable**：段级观测（仅诊断/观测，非永久 Domain）
- **External Dep/REQ**：SEG-001
- **Mock**：Storage Control Simulator
- **Can Start Without P2**：YES
- **DoD**：段级统计不反推对象级事实
- **Lane**：L2
- **Failure Cases**：not_found/stale
- **Evidence**：Contract Test
- **Handoff To**：C-EPIC-06
- **Non-Resp**：—

### C-EPIC-06 · Placement Decision

#### C-EPIC-06-T1 · Desired/Current/Supported Ops — IMPLEMENTATION
- **DRI/Contributor**：C / B3
- **Deliverable**：desired tier 计算（基于 current_tier/supported_operations/hotness）
- **Can Start Without P2**：YES
- **DoD**：决策含 policy_version + decision_reason
- **Lane**：L1/L2
- **Failure Cases**：状态不足
- **Evidence**：单测
- **Handoff To**：C-EPIC-06-T2
- **Non-Resp**：—

#### C-EPIC-06-T2 · Cooldown / Threshold / Keep-No-op — IMPLEMENTATION
- **DRI/Contributor**：C / B3
- **Deliverable**：防抖 + 回退链（预测→上一稳定→Heuristic→Keep/No-op）
- **Can Start Without P2**：YES
- **DoD**：Executor 不可用/状态不足→Keep/No-op
- **Lane**：L1/L2
- **Failure Cases**：震荡、Executor 不可用
- **Evidence**：单测 + Fault 测试
- **Handoff To**：C-EPIC-07
- **Non-Resp**：—

### C-EPIC-07 · TierAction Runtime

#### C-EPIC-07-T1 · TierAction State Machine — STATE MACHINE
- **DRI/Contributor**：C / B3
- **Deliverable**：Generated/Submitted/Succeeded/Failed/Unknown 状态机
- **Owned State**：TierAction.ActionState（C）
- **Internal Dep**：S-EPIC-01
- **Can Start Without P2**：YES
- **DoD**：状态机与 Provider ACCEPTED/RUNNING/SUCCEEDED/FAILED/UNKNOWN 映射正确
- **Lane**：L1
- **Failure Cases**：状态映射错误
- **Evidence**：状态机测试
- **Handoff To**：C-EPIC-07-T2
- **Non-Resp**：—

#### C-EPIC-07-T2 · P3↔Provider State Mapping — STATE MACHINE
- **DRI/Contributor**：C / B3
- **Deliverable**：Submitted≈ACCEPTED/RUNNING；Succeeded 仅收 P2 SUCCEEDED；Unknown=超时无反馈
- **Can Start Without P2**：YES
- **DoD**：accepted≠success；Unknown→Reconcile
- **Lane**：L1/L2
- **Failure Cases**：把 accepted 当成功
- **Evidence**：状态机测试
- **Handoff To**：C-EPIC-09
- **Non-Resp**：—

### C-EPIC-08 · Storage Control Simulator

#### C-EPIC-08-T1 · Unified Simulator — SIMULATOR
- **DRI/Contributor**：C / B3
- **Deliverable**：Storage Control Simulator（6 个 control Port 视图 + 共享 SimulatedStorageState）
- **Owned State**：SimulatedStorageState
- **Can Start Without P2**：YES
- **DoD**：正常模式 SUCCEEDED→更新 tier→generation++→feedback
- **Lane**：L2
- **Failure Cases**：跨 port 不一致（应为故障注入，非 Simulator 没联动）
- **Evidence**：Simulator 测试
- **Handoff To**：C-EPIC-08-T2
- **Non-Resp**：不复刻 P2、不真搬数据

#### C-EPIC-08-T2 · Case 1–6 — SIMULATOR
- **DRI/Contributor**：C / B3
- **Deliverable**：六故障用例（submit timeout but success / accepted then failed / feedback lost / P3 restart while running / duplicate action / desired≠observed）
- **Can Start Without P2**：YES
- **DoD**：独立进程或持久化；六用例可注入
- **Lane**：L3
- **Failure Cases**：见 Case 1–6
- **Evidence**：Fault 测试
- **Handoff To**：C-EPIC-09
- **Non-Resp**：—

### C-EPIC-09 · Feedback + Reconciliation

#### C-EPIC-09-T1 · Unknown / Stale / Missing Feedback — RECONCILIATION
- **DRI/Contributor**：C / B3
- **Deliverable**：对账（unknown/stale placement/missing feedback/generation mismatch）
- **Internal Dep**：S-EPIC-03
- **Can Start Without P2**：YES
- **DoD**：先确认真实状态再重试/对账/标记/人工；不盲重试
- **Lane**：L2/L3
- **Failure Cases**：反馈丢失、generation mismatch
- **Evidence**：Fault 测试
- **Handoff To**：C-EPIC-09-T2
- **Non-Resp**：—

#### C-EPIC-09-T2 · Query / Retry Policy — RECOVERY
- **DRI/Contributor**：C / B3
- **Deliverable**：query(action_id) + 重试策略（幂等、不盲重试）
- **Can Start Without P2**：YES
- **DoD**：重启后 query 仍有效；重复 action_id 不重复执行
- **Lane**：L2/L3
- **Failure Cases**：重复执行、重启丢失
- **Evidence**：Fault 测试
- **Handoff To**：—
- **Non-Resp**：—

### C-EPIC-10 · Optimize / Predict

#### C-EPIC-10-T1 · Heuristic Baseline — IMPLEMENTATION
- **DRI/Contributor**：C / B3
- **Deliverable**：Frozen Heuristic Baseline（Feature/Policy Version + Threshold/Cooldown）
- **Can Start Without P2**：YES
- **DoD**：同版本同输入可复现决策
- **Lane**：L1
- **Failure Cases**：复现失败
- **Evidence**：Baseline 报告
- **Handoff To**：C-EPIC-10-T2
- **Non-Resp**：不直接从第一版跳 RL

#### C-EPIC-10-T2 · Feature Contract & Policy Version — DESIGN
- **DRI/Contributor**：C / B3
- **Deliverable**：feature_schema/label_schema/policy_version
- **Can Start Without P2**：YES
- **DoD**：数据/模型可版本化可复现
- **Lane**：L1
- **Failure Cases**：测试后改版本
- **Evidence**：设计文档
- **Handoff To**：C-EPIC-10-T3
- **Non-Resp**：—

#### C-EPIC-10-T3 · Prediction + Fallback + Measurement — IMPLEMENTATION
- **DRI/Contributor**：C / B3
- **Deliverable**：预测接入 + 回退链 + 命中率测量（较启发式 +10%）
- **Can Start Without P2**：YES
- **DoD**：回退链完整；命中率证据可审计
- **Lane**：L2/L3
- **Failure Cases**：模型异常→回退
- **Evidence**：评测报告
- **Handoff To**：项目负责人（Review）
- **Non-Resp**：—

---

## 最终质量检查（15 问）

1. **A 明天开始能做什么**：A-EPIC-01/02/03（Embedding 契约 + VectorProjectionPort + Vector Backend Simulator）。
2. **B 明天开始能做什么**：B-EPIC-01/02/03（Fact-First + 生命周期 + ContentStorePort + Content Store Simulator）。
3. **C 明天开始能做什么**：C-EPIC-01/03/07/08（Signal 消费 + Plan Domain Object + TierAction 状态机 + Storage Control Simulator）。
4. **没有 P2 三人还能做哪些完整任务**：全部（除 Lane 3 集成门禁），双路径 Simulator 支撑。
5. **第一批产出**：见各 Program 工作包 "First Batch"。
6. **哪条 Task 完成后另一人才能继续**：A-EPIC-01（Embedding 契约）→ B-EPIC-05-T3；A-EPIC-02-T1（VectorProjectionPort）→ B-EPIC-05-T4；B-EPIC-06（MemoryRead）→ A-EPIC-06；B-EPIC-03（ContentStorePort）→ A-EPIC-07；B-EPIC-07（MemorySignal）→ C-EPIC-01；S-EPIC-04（AccessTrace）→ C-EPIC-01-T2。
7. **Recall 出问题找谁**：A（E2E Accountable）；根因是 Memory Fact 则 A 经 MemoryRead 契约找 B 修。
8. **Memory 没形成找谁**：B。
9. **TierAction 一直 Unknown 找谁**：C（先 Reconciliation，再升级 P2 反馈问题）。
10. **Projection Ready 一直失败找谁**：B（ProjectionState owner）；机制失败找 A（ProviderResult）。
11. **P2 缺接口谁整理需求给 PM**：Integration Steward（I-EPIC-01/02）。
12. **PM 收到的是明确 REQ 还是"帮我想接口"**：明确 REQ（P2 Requirement Package V0.4.1 可直接发）。
13. **哪些 Lane 2 PASS 但 Lane 3 不能 PASS**：所有真实 P2 语义（真实 ANN 质量/性能、真实持久化/恢复、真实迁移/层级、真实 SLA、生产 72h）。
14. **三人是否存在新系统单点**：见 `P3_WORKLOAD_BALANCE_REVIEW.md`。
15. **是否仍存在"PM 不补就没人管"的工作**：否——所有 Runtime/State/Recovery/Reconciliation/接口字段/Mock 设计均有 DRI（见各 Program + Shared）。

---

*主 WBS 完。详细 DoD/依赖/验收/工作量见配套文件。*
