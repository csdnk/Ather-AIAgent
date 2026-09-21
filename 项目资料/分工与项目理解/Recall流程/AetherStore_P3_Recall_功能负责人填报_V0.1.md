# AetherStore P3 · Recall 负责人功能填报 V0.1

## Person A — Recall / Context Serving

**Primary Outcome**：从 Query 到最终 Context Pack 的完整读路径正确、可降级、可追踪；Recall 最终结果错误找 A。

按 A-EPIC-01～A-EPIC-10 组织为 **10 项功能**，每个 Epic 对应一个 Feature，统一填写一套 User Story、功能描述、流程、依赖、验收、计划和人天。Epic 名称、范围、PRR Action 与完成标准沿用分工说明。公司视图见[功能汇总表](AetherStore_P3功能汇总表_V0.1.md)。

## A 的最终 Epic 结构

| Epic ID | 最终 Epic | 功能名称 | 估算人天 | Epic 内独立计划 |
| --- | --- | --- | --- | --- |
| [A-EPIC-01](#a-epic-01--semantic-compute--resource-isolation) | Semantic Compute & Resource Isolation | 共享向量计算、资源隔离与模型发布 | 28 | D001～D028 |
| [A-EPIC-02](#a-epic-02--vector-projection-mechanism) | Vector Projection Mechanism | 向量投影写入、确认、恢复与删除 | 20 | D001～D020 |
| [A-EPIC-03](#a-epic-03--vector-backend-simulator) | Vector Backend Simulator | 向量后端模拟与故障验证 | 7 | D001～D007 |
| [A-EPIC-04](#a-epic-04--query-runtime--ingress-protection) | Query Runtime & Ingress Protection | 召回查询接入与流量保护 | 9 | D001～D009 |
| [A-EPIC-05](#a-epic-05--candidate-retrieval--search-degradation) | Candidate Retrieval & Search Degradation | 候选检索与搜索降级 | 8 | D001～D008 |
| [A-EPIC-06](#a-epic-06--candidate-validation--reference-integrity) | Candidate Validation & Reference Integrity | 候选有效性与引用完整性校验 | 8 | D001～D008 |
| [A-EPIC-07](#a-epic-07--canonical-load) | Canonical Load | 可信原文加载与预热回退 | 9 | D001～D009 |
| [A-EPIC-08](#a-epic-08--rank--decay--conflict-execution) | Rank / Decay / Conflict Execution | 排序、衰减与冲突规则执行 | 6 | D001～D006 |
| [A-EPIC-09](#a-epic-09--context-assembly) | Context Assembly | 上下文组装、预算控制与可靠交付 | 12 | D001～D012 |
| [A-EPIC-10](#a-epic-10--recall-observability-slo-audit--acceptance) | Recall Observability, SLO, Audit & Acceptance | 召回可观测性、SLO、审计与验收 | 29 | D001～D029 |

## 计划与人天口径

- **每个 Epic 独立从 D001 起算。** 设计、开发、测试、联调依次累计本 Epic 的有效投入日，不接续其他 Epic 的时间。
- 例如 A-EPIC-01 的局部周期为 D001～D028；A-EPIC-02 重新从 D001 起算，到 D020 结束；A-EPIC-03 同样从 D001 起算，到 D007 结束。
- 本文的日期是 Epic 内部的投入序号，用于说明各阶段工作量；不表示所有 Epic 在项目首日同时开工，也不用于将不同 Epic 串行相加。
- **团队配置为 3 人，项目周期目标为 90 个工作日。** 各 Epic 依据接口与人员条件并行实施，局部投入序号与项目全局日期分别表达。
- **功能工作量为 136 人天＝设计 23＋开发 55＋测试 33＋联调 25。** 每项局部阶段以一名执行人的有效投入核算；跨 Epic 并行缩短总体周期，不改变既有功能工作量。
- Owner A 对 Recall 整体交付负责；B、C、P2、RF 保留各自领域职责，外部团队实施投入和设备采购不包含在本表。

### 三人分工与并行关系

| 执行岗位 | 主要职责 | 负责 Epic | 功能投入（人天） |
| --- | --- | --- | --- |
| A1 | 共享 Embedding、资源隔离、模型发布与投影机制 | A-EPIC-01、A-EPIC-02 | 48 |
| A2 | Recall 入口、检索、正文、排序与 Context 交付 | A-EPIC-04、A-EPIC-05、A-EPIC-07、A-EPIC-08、A-EPIC-09 | 44 |
| A3 | 模拟器、资格校验、Trace/SLO/审计及端到端验收 | A-EPIC-03、A-EPIC-06、A-EPIC-10 | 44 |
| 合计 | 3 人 | 10 个 Epic | 136 |

- Embedding、Recall 入口和模拟器按统一契约分别开发，可以并行启动；真实 Query 联调接在基础向量能力之后。
- 投影、候选检索、资格校验和正文加载可使用模拟器及明确标识的契约样例并行开发；真实联调分别依赖 P2 和 B 的对应能力。
- 排序和 Context 可先按固定样例开发；完整链路验收在候选、资格、正文、最终复核与持久化能力具备后进行。
- Trace/事件、指标和审计工具从前期设计开始穿插开发；性能、模型切换和完整链路验收在各相关能力交付后汇合。
- 同一岗位承担的 Epic 可穿插推进，其投入日不重复占用；并行主要发生在 A1、A2、A3 三个岗位之间。

## 文档依据

- 公司结构与工作范围：[Epic 分工](../总设计/AetherStore_P3_Owner_Epics_Merged_V1.0.md)、[原始 WBS](../总设计/AetherStore_P3_Engineering_WBS_V1.0.md)、[A 工作包](../总设计/PROGRAM_A_RECALL_WORK_PACKAGE.md)、[A 生产就绪补充](../../../PPR/PROGRAM_A_Production_Readiness_Addendum_V1.0.md)。
- 填写格式：[功能填报模板](../总设计/AetherStore_P3_功能填报模板_V1.0.md)、[汇总表模板](../总设计/AetherStore_P3_功能汇总表模板_V1.0.md)。
- 流程、字段与接口：[运行时主设计](运行时详细设计_V0.1/召回流程详细设计_V0.1.md)、[数据定义](运行时详细设计_V0.1/召回数据定义_V0.1.md)、[Recall 总流程](AetherStore_P3_Recall_Overall_Flow_V1.0.md)、[P2 对接](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md)、[B 对接](运行时详细设计_V0.1/召回与记忆形成接口对接需求_V0.1.md)、[C 对接](运行时详细设计_V0.1/召回与调度优化接口对接需求_V0.1.md)。
- 外部能力和接入条件：[跨模块接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。正文中的 AL 编号沿用此清单。

---
## A-EPIC-01 · Semantic Compute & Resource Isolation

**Epic 范围**：passage/query 两类 Embedding；model contract/version/dimension；真实向量；吞吐与资源隔离；模型变更安全。

**关联 PRR Action**：A-P0-2、A-P0-3、A-P0-5。

**Epic 级完成标准**：passage/query 语义清晰；query 不被 passage/后台抢占；隔离态与混合负载 Profile 均满足吞吐目标；模型/维度变化可 Shadow/Canary/Rollback。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-01 |
| Epic Name | Semantic Compute & Resource Isolation |
| Feature ID | A-FEAT-01-01 |
| Feature Name | 共享向量计算、资源隔离与模型发布 |
| Owner | A（Recall / Context Serving） |
| Contributor | B1；B / RF / 验收负责人 |
| 目标里程碑 | M1 |

### 2. User Story

作为 Remember 模块、Recall Runtime 和模型服务维护者，我希望获得兼容、可追踪且有资源保障的正文/查询向量服务，并能安全升级和回退模型，从而支撑记忆写入与在线检索，控制混合负载及模型变更风险。

### 3. 功能描述

#### 3.1 功能目标

统一提供 Passage/Query 真实向量生成、模型与检索空间绑定、完成结果复用、资源隔离、性能验证，以及 Shadow/Canary/Rollback 受控发布能力。

#### 3.2 核心输入

- SemanticEmbeddingRequest：文本或受控片段、usage、source_hash、模型契约、租户/授权和 deadline。
- B 提供 Passage 的片段及版本依据；Recall 提供 Query。
- Query/Passage/后台负载、模型版本、硬件、batch、并发和队列配置。
- 前台延迟与错误率观察、已签收 Acceptance Profile。
- 当前和候选模型制品、模型/预处理版本、维度、检索空间绑定。
- 评测样本、发布条件、回退条件和 B 的投影可读性事实。

#### 3.3 核心处理

1. 校验输入、授权和摘要，固定模型、预处理、维度与检索空间。
2. 分配独立队列、并发和执行资源，落实 Query 高于 Passage、高于后台的优先级。
3. 按租户、用途和完整输入绑定查询已完成结果；未命中则执行真实推理。
4. 验证向量维度、有限数值及输入对应关系，保存执行次数、可信结果和摘要。
5. 前台恶化时限制后台准入或暂停相应任务，并记录触发和恢复依据。
6. 在固定模型/设备/数据集下运行两套 Profile，测有效吞吐、P95/P99、错误率及隔离效果。
7. 校验候选模型与目标空间，固定发布版本和请求绑定。
8. Shadow 比较新旧模型，再按获准范围 Canary。
9. 监测兼容性和质量/性能，异常回退至已验证兼容的稳定绑定，保存全过程证据。

#### 3.4 核心输出

- SemanticEmbeddingResult：受控向量及摘要、model_version、dimension、用途和输入绑定。
- SemanticEmbeddingExecution：执行状态、尝试次数与可恢复结果引用。
- 经验证的资源配额和节流配置。
- 两套 Benchmark 报告、容量边界和运行配置记录。
- 版本化发布配置、评测对比和灰度记录。
- 回退结果及交给端到端验收的发布演练证据。

### 4. 正常流程

接收正文或查询请求 → 校验用途、授权和模型/空间 → 按用途分配资源 → 复用完成结果或真实推理 → 校验并保存向量 → 返回调用方。容量验证执行隔离态和混合负载态两套 Profile；模型变更依次执行兼容性检查、Shadow、Canary 和发布/回退，并留存证据。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 输入超长、摘要错误或模型不兼容 | 推理前拒绝并给出稳定原因 | 不产生成功向量 |
| 推理超时或进程重启 | 共享执行层按原 deadline 和额度恢复；调用方重放附着原执行 | 不增加隐式重试额度 |
| 批内单条失败或向量非法 | 逐项绑定并拒绝非法结果 | 不以零向量或其他条目冒充成功 |
| 共享设备无法证明隔离 | 调整部署或降低 Passage 准入 | 不宣称已实现前台保障 |
| 前台延迟越线 | 节流后台并观察恢复 | 有明确容量保护证据 |
| 任一 Profile 不达标 | 记录瓶颈并回到调优 | 性能验收不通过 |
| 模型或维度不兼容 | 阻止查询与投影跨空间混用 | 不进入真实切换 |
| 灰度质量或时延不达标 | 停止扩大并执行回退 | 恢复获准的稳定绑定 |
| 稳定模型对应投影不可读 | 保留故障及可读性事实 | 不将配置回退冒充服务恢复 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| B / Recall | 明确用途、片段和模型空间的消费绑定 | B / A |
| 模型运行环境 | 获准模型、运行资源和版本制品 | A，B1 协作 |
| 可靠执行存储 | 执行绑定、租约和结果一致保存 | RF |
| 共享 Embedding | 真实推理及基础用途区分 | A，A-FEAT-01-01 |
| 容量测试调用器 | 生成查询负载并采集前台阶段时延，完整 Recall 联验纳入 A-EPIC-10 | A |
| 性能验收环境 | 设备、数据集、指标口径和负载基线 | 验收负责人 / RF |
| 共享 Embedding | 请求模型固定、版本与结果追踪 | A，A-FEAT-01-01 |
| Memory / Projection 事实 | 新旧模型的投影编排、当前可读性与切换依据 | B |
| 评测与发布环境 | 质量/时延基线、发布配置、运行观察 | A / RF / 验收负责人 |

接口依赖：AL-B05、AL-B08、AL-RF05、AL-RF04、AL-PM01。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. Query 与 Passage 均输出来自真实模型的合法向量，模型/维度/来源可追踪。
2. 同输入合法重放可复用完成结果；不同租户、usage、模型或片段绑定不误复用。
3. 单条异常不造成批次结果错配，崩溃恢复不重置推理额度。
4. Passage 结果交回 B，由 B 决定写入；Query 不自动写入长期索引。
5. 隔离态和混合负载态均按冻结口径验证有效吞吐 ≥2000 item/s。
6. 记录模型、维度、设备、batch、并发、warm-up、P95/P99 和错误率。
7. 混合负载中 Query 保留资源不被 Passage 或后台占满。
8. 前台恶化能够触发后台节流，恢复行为可复现；正式时延阈值按签收 Profile 判断。
9. Shadow、Canary、Rollback 均有可执行步骤和演练结果。
10. 在途请求沿用已固定模型，不因配置更新混用维度和空间。
11. 兼容性不满足时阻止切换；回退后校验实际可用链路。
12. 发布记录能够关联模型、空间、指标、原因与 trace。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D005 | 模型/输入绑定与复用设计；容量与双 Profile 测试方案；发布与回退方案及判定条件 |
| 开发 | D006 | D017 | 推理服务、结果校验及恢复实现；资源隔离、优先级及节流实现；版本绑定和受控切换实现 |
| 测试 | D018 | D024 | 真实向量、隔离复用及崩溃用例；混合负载测试和调优证据；不兼容拦截及回退测试 |
| 联调 | D025 | D028 | B Passage 与 Recall Query 调用记录；正式环境双 Profile Benchmark；与 B 联调的发布演练记录 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 5 |
| 开发 | 12 |
| 测试 | 7 |
| 联调 | 4 |
| **总人天** | **28** |

### 10. 备注

- WBS 对应：A-EPIC-01-T1、A-EPIC-01-T2、A-EPIC-01-T3、A-EPIC-01-T4。
- PRR Action：A-P0-2、A-P0-3、A-P0-5。
- 设计依据：主设计第 2.1～2.3、2.8、5.4 节；数据定义第 19、20、25 章；B 对接说明。；Epic A-EPIC-01；A 生产就绪补充；主设计第 2.3 节及参数目录。；Epic A-EPIC-01；A 生产就绪补充 A-P0-5；主设计第 2.2、3.2 节。
- 工作量及责任边界：包含原共享推理、资源保障和模型发布三部分的完整工作量。B 负责投影领域编排与切换，A 不代写 ProjectionState。专项性能与模型发布演练在本项实现；A-EPIC-10 汇集证据并验证完整 Recall 链路，不重复计入这些实现。

---
## A-EPIC-02 · Vector Projection Mechanism

**Epic 范围**：VectorProjectionPort；五元组幂等；ProviderResult 五态；写入/query/delete/version/schema。

**关联 PRR Action**：—。

**Epic 级完成标准**：ProviderResult=ACCEPTED/PENDING/READY/FAILED/UNKNOWN 均有明确语义；A 不写 ProjectionState；版本/维度错误不误报 Ready。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-02 |
| Epic Name | Vector Projection Mechanism |
| Feature ID | A-FEAT-02-01 |
| Feature Name | 向量投影写入、确认、恢复与删除 |
| Owner | A（Recall / Context Serving） |
| Contributor | B1；B / P2 / RF |
| 目标里程碑 | M1 |

### 2. User Story

作为 Remember 投影构建与维护模块，我希望将获准向量写入存储、查询真实完成状态，并在超时、版本更新和删除时安全收敛，从而为领域 Ready 判定提供可信证据，避免重复写入、旧版本覆盖和删除后复活。

### 3. 功能描述

#### 3.1 功能目标

统一提供 VectorProjectionPort 契约、五元组幂等、ProviderResult 五态处理、写入及操作/目标查询、有界恢复、版本更新与准确目标删除。

#### 3.2 核心输入

- B 批准的 VectorProjectionRequest：准确五元组、目标空间、Passage 向量、内容引用、元数据、授权。
- 稳定幂等身份和载荷摘要；P2 原始操作与对象证据。
- 原 VectorProjectionOperation、调用意图、尝试额度和已有证据。
- B 发起的合法新版本目标或准确删除授权；P2 操作、目标和屏障事实。

#### 3.3 核心处理

1. 固定目标、幂等键和请求指纹，可靠保存操作及调用意图。
2. 调用 upsert，按需 query_operation/get_projection 核验准确目标、载荷和索引状态。
3. 保留原始证据，形成 ACCEPTED/PENDING/READY/FAILED/UNKNOWN，交由 B 执行 Ready Guard。
4. 按原身份恢复 PENDING/UNKNOWN，先查证，满足无迟到副作用等保证才允许预算内安全重发。
5. 真实版本变化写新目标，把结果交回 B 决定领域切换。
6. 删除前可靠保存退役标记，禁止旧写重发；核验对象退出索引以及在途旧写不会复活。

#### 3.4 核心输出

- VectorProjectionOperation、ProjectionTargetBinding 和可追踪操作引用。
- ProviderResult：机制状态、准确身份、证据与原因。
- 恢复后的操作及 ProviderResult 观察版本。
- 删除完成证据，或带原因和证据的 attention_required。

### 4. 正常流程

B 提交准确目标及获准向量 → 固定五元组和幂等绑定 → 可靠保存调用意图 → P2 写入 → 查询原操作并核验目标/索引 → 返回 ProviderResult 供 B 执行 Ready Guard。超时或重启按原身份恢复；版本变化写入新目标；删除先建立退役约束，再删除并验证不会复活。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 同幂等身份载荷变化 | 拒绝请求并保留原操作 | 不覆盖原向量 |
| P2 已受理但索引未就绪 | 返回 ACCEPTED 或有证据的 PENDING | 不提前 READY |
| 回包丢失或完成证据不全 | 记录 UNKNOWN 或有依据的 PENDING | 转恢复功能继续核验 |
| 查询 not_found 或写入超时 | 保留未知并继续预算内查证 | 不据此盲目重写 |
| 恢复窗口耗尽 | 保留 PENDING/UNKNOWN 并标识 attention_required | 不虚报成功或永久失败 |
| 同五元组 rebuild 请求 | 在 P2 调用前拒绝 | 保持当前版本不支持的明确边界 |
| 删除 Ack 缺少防复活证据 | 继续核验或保留未确定状态 | 不报告安全删除完成 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| Passage Embedding | 可信向量及模型/输入绑定 | A，A-FEAT-01-01 |
| 投影领域编排 | 目标、五元组、写入授权与 Ready Guard | B |
| VEC-001 | upsert、query_operation、get_projection 及完成证据 | P2 |
| 持久化机制 | 幂等、调用意图和结果保存 | RF |
| 投影写查机制 | 准确绑定和 ProviderResult | A，A-FEAT-02-01 |
| 领域决策 | 版本变更、清理时机与删除授权 | B |
| 操作恢复与屏障 | 按原键查询、精确删除、迟到写隔离 | P2 |
| 恢复持久化 | 租约、次数、退役和未决操作保留 | RF |

接口依赖：AL-B08、AL-P205～208、AL-RF05、AL-P206、AL-P207。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. 五元组、模型、维度、Schema、向量及元数据绑定均校验。
2. 同键同载荷重复请求复用原操作，同键冲突明确拒绝。
3. ProviderResult 五态可区分，缺索引/目标证据不返回 READY。
4. B 能消费机制结果且 A 不修改 ProjectionState；模拟与真实 P2 证据分别保存。
5. 重启保持同一操作及已消耗额度，UNKNOWN 不触发无证据重写。
6. 旧版本迟到 READY 不修改新版本目标，也不直接改变 B 当前可读性。
7. 删除精确作用于授权目标；完成证据包含不可查询及不会复活。
8. 同版本 rebuild 拒绝、重复删除、未知恢复和崩溃场景均可复现。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D004 | 投影契约、幂等绑定与五态映射；恢复、更新及删除状态规则 |
| 开发 | D005 | D012 | 写入、查询、准确对象核验实现；恢复协调器、版本更新和删除屏障消费 |
| 测试 | D013 | D016 | 幂等/冲突/索引未就绪契约用例；超时重启/迟到写/重复删除用例 |
| 联调 | D017 | D020 | B Ready Guard 与 P2 写查联调；B 与 P2 故障恢复及删除联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 4 |
| 开发 | 8 |
| 测试 | 4 |
| 联调 | 4 |
| **总人天** | **20** |

### 10. 备注

- WBS 对应：A-EPIC-02-T1、A-EPIC-02-T2、A-EPIC-02-T3。
- PRR Action：—。
- 设计依据：主设计第 2.4～2.6、2.8 节；数据定义第 21～24 章；P2 对接 RP2-06～08。；主设计第 2.7 节；数据定义第 21～24 章；P2 对接 RP2-07～09。
- 工作量及责任边界：正常写查、五态处理和恢复/更新/删除统一核算，共享契约和状态处理不重复计费。A 只提供机制结果，B 唯一拥有 ProjectionState。同五元组 rebuild 按现有规则拒绝，不包含新增代际重建能力。

---
## A-EPIC-03 · Vector Backend Simulator

**Epic 范围**：有状态向量模拟；index_ready；幂等；版本/维度；超时/失败/stale/partial/unknown 故障注入。

**关联 PRR Action**：—。

**Epic 级完成标准**：可独立验证 VectorProjection / Search 契约与失败链；不依赖真实 P2；不实现真实 ANN。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-03 |
| Epic Name | Vector Backend Simulator |
| Feature ID | A-FEAT-03-01 |
| Feature Name | 向量后端模拟与故障验证 |
| Owner | A（Recall / Context Serving） |
| Contributor | B1 |
| 目标里程碑 | M1 |

### 2. User Story

作为 Recall 与 Remember 开发者，我希望在真实 P2 尚未就绪时验证向量写入、查询和故障恢复契约，从而提前开发并发现接口和状态处理问题。

### 3. 功能描述

#### 3.1 功能目标

提供有状态 Vector Backend Simulator，支撑向量写读契约与可重现故障验证。

#### 3.2 核心输入

- 投影、搜索契约请求和已配置的模拟向量/版本/索引状态。
- 正常模式或显式故障注入配置。

#### 3.3 核心处理

1. 维护目标、幂等、模型/维度与 index_ready 等模拟状态。
2. 返回与状态一致的写入、查询、删除和预置候选结果。
3. 按注入配置模拟 timeout、failed、stale、partial、unknown 和回包丢失。

#### 3.4 核心输出

- 模拟 Provider 响应与可检查的状态快照。
- 可重复执行的契约场景和故障证据。

### 4. 正常流程

配置契约场景 → 初始化状态 → 执行消费方请求 → 正常响应或注入故障 → 检查状态与结果 → 输出测试证据。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 重复写入 | 按同目标同载荷幂等处理 | 模拟状态不重复增长 |
| 版本或维度不符 | 显式返回契约错误 | 不产生虚假就绪 |
| 故障模式启用 | 标注注入类型及配置 | 不会把模拟证据当成真实后端事实 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 投影和搜索契约 | 方法、状态及输入输出基线 | A |
| 联调使用方 | 契约场景、故障断言与测试调用 | A / B1 |

接口依赖：AL-P202、AL-P205～208。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. 同一输入配置能够重现相同正常或故障过程。
2. 幂等、索引未就绪、版本/维度错误和五态行为覆盖。
3. 正常模式响应与模拟状态一致；故障模式明确可辨。
4. 模拟契约通过与真实 P2 联调通过分别记录，不实现或声称具备真实 ANN。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D001 | 模拟状态与故障目录 |
| 开发 | D002 | D004 | 有状态模拟器及注入开关 |
| 测试 | D005 | D006 | 正常一致性和故障场景测试 |
| 联调 | D007 | D007 | A/B 使用模拟器的契约验收记录 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 1 |
| 开发 | 3 |
| 测试 | 2 |
| 联调 | 1 |
| **总人天** | **7** |

### 10. 备注

- WBS 对应：A-EPIC-03-T1、A-EPIC-03-T2。
- PRR Action：—。
- 设计依据：Epic A-EPIC-03；WBS A-EPIC-03；P2 对接联调场景。
- 工作量及责任边界：提供有状态模拟器和显式故障注入，不实现真实 ANN。消费方业务处理在各自 Epic 内实现；模拟与真实 P2 验收分别记录。

---
## A-EPIC-04 · Query Runtime & Ingress Protection

**Epic 范围**：Query→Query Embedding；契约校验；入口限流；Query Embedding 队列上限；Deadline。

**关联 PRR Action**：A-P0-1。

**Epic 级完成标准**：突发流量下有明确 admission；429/Busy 语义稳定；队列有上限；限流时前台 P99 不失控。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-04 |
| Epic Name | Query Runtime & Ingress Protection |
| Feature ID | A-FEAT-04-01 |
| Feature Name | 召回查询接入与流量保护 |
| Owner | A（Recall / Context Serving） |
| Contributor | B1；P4 / RF |
| 目标里程碑 | M2 |

### 2. User Story

作为 Agent 调用方，我希望提交问题、授权范围和预算后获得自动选路、兼容 Query 向量和有界准入的召回服务，从而简化调用并避免突发流量压垮在线链路。

### 3. 功能描述

#### 3.1 功能目标

完成 Recall 入口、内部来源选择、Query 调用、幂等执行和有界准入。

#### 3.2 核心输入

- 问题、可信身份/Scope、session/task、筛选约束、token 预算、deadline、幂等键。
- 获准模型/空间配置及入口容量配置；上游不传 retrieval_mode。

#### 3.3 核心处理

1. 校验授权及请求，按 scope_union_v1 固定 working_only、long_term_only 或 combined。
2. 建立 RecallRequestIndex/RecallExecution 和阶段检查点，处理同键重放及冲突。
3. 执行入口限流和有界排队；长期分支调用共享 Query Embedding 并校验检索空间。

#### 3.4 核心输出

- RecallRequest、SourceSelection、RecallExecution 和 QueryEmbeddingResult（适用时）。
- 稳定准入拒绝、Busy/429 或协议错误，以及向后续阶段交接的执行绑定。

### 4. 正常流程

收到请求 → 授权/参数/幂等校验 → 固定来源模式 → 有界准入 → 按需 Query 向量化 → 交给候选发现。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 越权、非法预算或同键不同请求 | 入口拒绝 | 不开始检索 |
| 队列满或容量不足 | 稳定 Busy/429 映射 | 排队有上限 |
| Query 向量不兼容或失败 | 保留原因，按固定模式的失败规则处理 | combined 可在剩余时间内继续 Working，不重选模式 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 网关与调用方 | 身份、请求、预算和状态转交 | P4 / 实际调用方 |
| 共享基础 | 授权、CAS/租约、执行持久化及资源准入 | RF |
| Query Embedding | 真实向量与兼容空间 | A，A-FEAT-01-01 |

接口依赖：AL-RF01、AL-RF02、AL-RF04、AL-P401、AL-B05、AL-PM01。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. 三种来源模式有确定选择依据，受理后故障不改变已固定模式。
2. working_only 不调用 Query Embedding；长期分支校验模型/维度/空间。
3. 同键重放不新建执行，向量传输重试不引发额外推理额度。
4. 突发流量下队列有上限、429/Busy 稳定，前台 P99 按冻结 Profile 验证。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D002 | 入口、模式和流量保护设计 |
| 开发 | D003 | D006 | 请求执行、Query 接入和限流实现 |
| 测试 | D007 | D008 | 模式/幂等/突发流量测试 |
| 联调 | D009 | D009 | 网关、RF 与共享 Query 调用联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 2 |
| 开发 | 4 |
| 测试 | 2 |
| 联调 | 1 |
| **总人天** | **9** |

### 10. 备注

- WBS 对应：A-EPIC-04-T1、A-EPIC-04-T2。
- PRR Action：A-P0-1。
- 设计依据：主设计第 3.1、3.2 节；数据定义第 1～5 章；A 生产就绪补充。
- 工作量及责任边界：负责入口、来源模式、执行骨架、Query 调用及入口保护。共享推理/隔离归 A-EPIC-01，候选发现归 A-EPIC-05，最终交付与结果重放归 A-EPIC-09。

---
## A-EPIC-05 · Candidate Retrieval & Search Degradation

**Epic 范围**：VectorSearchPort；TopK；filter；稳定引用；partial/degraded；Search 后端半故障/不可用降级。

**关联 PRR Action**：A-P1-3。

**Epic 级完成标准**：Search 超时/不可用不冒充完整；降级阈值明确；结果携带稳定引用和版本；可输出 recall_availability。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-05 |
| Epic Name | Candidate Retrieval & Search Degradation |
| Feature ID | A-FEAT-05-01 |
| Feature Name | 候选检索与搜索降级 |
| Owner | A（Recall / Context Serving） |
| Contributor | B1；B / P2 |
| 目标里程碑 | M2 |

### 2. User Story

作为 Recall 调用方，我希望按查询条件获得当前 Working 和相关长期记忆候选，并明确获知来源缺失及部分故障，从而正确判断本次检索是否完整。

### 3. 功能描述

#### 3.1 功能目标

按既定模式读取 Working 和长期候选，统一记录来源覆盖、稳定引用和搜索故障。

#### 3.2 核心输入

- 固定来源模式、Query 向量（适用时）、Scope、filter、TopK 和剩余 deadline。
- B Working 结果与 P2 search 返回的候选及完成性证据。

#### 3.3 核心处理

1. working_only 读 B；long_term_only 查 P2；combined 按阶段规则读取两来源。
2. 保留候选 ID、稳定引用、版本、score/rank 及来源证据。
3. 形成两来源 SourceReadResult，显式处理部分结果、超时、半故障和缺失。

#### 3.4 核心输出

- VectorCandidateSet、SourceReadResult 与交给资格校验的候选。
- 来源覆盖和缺失原因，供最终 recall_availability 判定。

### 4. 正常流程

读取固定模式 → 调用 B Working / P2 search → 校验来源响应 → 保存候选和来源完整性 → 交给资格校验。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 搜索超时或后端半故障 | 采用可信部分并保留缺失原因 | 不宣称来源完整 |
| 确认完整但无候选 | 保存正常空来源事实 | 由后续汇总判断正常空 |
| 引用/版本缺失或响应不可信 | 隔离受影响候选并记录缺口 | 不把脏候选作为成功搜索内容 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| Query 入口 | 固定模式与兼容 Query 向量 | A，A-FEAT-04-01 |
| Working 读取 | 当前 session/task 的有效 Working 候选与完整性 | B |
| VEC-002 | 带 Scope/filter/TopK 的检索与稳定引用 | P2 |

接口依赖：AL-B01、AL-P201、AL-P202、AL-RF04、AL-PM01。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. Working 是独立来源，Prewarm 不作为第三个搜索来源。
2. TopK、过滤和租户范围按契约生效；候选版本和引用可追踪。
3. 完整空、partial、timeout、failed 有不同事实记录，零条不直接当成功空。
4. 降级阈值和恢复行为形成配置及故障验证；未签收数值不宣称生产达标。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D001 | 来源契约和搜索降级规则 |
| 开发 | D002 | D004 | Working/向量发现适配及结果归一 |
| 测试 | D005 | D006 | 完整空/partial/超时/半故障测试 |
| 联调 | D007 | D008 | B Working 与真实 P2 搜索联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 1 |
| 开发 | 3 |
| 测试 | 2 |
| 联调 | 2 |
| **总人天** | **8** |

### 10. 备注

- WBS 对应：A-EPIC-05-T1、A-EPIC-05-T2、A-EPIC-05-T3。
- PRR Action：A-P1-3。
- 设计依据：主设计第 3.3、4 章；数据定义第 6、7 章；P2 RP2-01；B Working 对接。
- 工作量及责任边界：负责 Working/长期候选发现及来源事实归一，Prewarm 不作为第三个搜索来源。候选资格、正文读取和统一终态判定分别归后续 Epic。

---
## A-EPIC-06 · Candidate Validation & Reference Integrity

**Epic 范围**：调用 B 的 MemoryReadCapability；校验 Memory state/version/scope/conflict/evidence；悬空引用检测。

**关联 PRR Action**：A-P1-2。

**Epic 级完成标准**：只使用当前有效版本；object_ref / graph_node_ref 等引用悬空可识别并显式处理；不因脏候选产生完整结果。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-06 |
| Epic Name | Candidate Validation & Reference Integrity |
| Feature ID | A-FEAT-06-01 |
| Feature Name | 候选有效性与引用完整性校验 |
| Owner | A（Recall / Context Serving） |
| Contributor | B1；B |
| 目标里程碑 | M2 |

### 2. User Story

作为 Recall 调用方，我希望只有当前版本、权限及状态有效且引用可解析的候选进入后续流程，从而避免过期、已删除、越权或悬空引用导致错误上下文。

### 3. 功能描述

#### 3.1 功能目标

消费 B 的权威 MemoryRead 事实校验候选，并提供可供最终复核复用的校验适配。

#### 3.2 核心输入

- 候选 memory_id、memory_version、表示/对象引用及来源。
- B 返回的权限、状态、当前版本、Working TTL、投影可读性和语义证据。

#### 3.3 核心处理

1. 按准确身份向 B 核验 state/version/scope/conflict/evidence 等事实。
2. 区分 Working 当前性与长期 Projection 可读性，不向 Working 强加长期索引条件。
3. 检测 object_ref/graph_node_ref 悬空，保存通过、权威排除和未知原因。

#### 3.4 核心输出

- CandidateValidationResult 及可信候选资格快照。
- 排除依据、悬空引用和未确定事实，供加载与终态处理。

### 4. 正常流程

收到候选 → B 权威核验 → 检查当前版本/授权/适用可读性 → 识别悬空引用 → 保存结果 → 交正文加载。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 删除、过期或旧版本被权威确认 | 排除并保存依据 | 不进入正文采用集合 |
| 权限/状态无法确认 | 隔离并记录缺失 | 不假定候选有效 |
| 对象或图引用悬空 | 记录可定位的引用问题 | 不以高相似度绕过校验 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 候选发现 | 稳定身份和来源证据 | A，A-FEAT-05-01 |
| MemoryRead | 当前权威事实、批量校验与有效窗口 | B |
| 引用事实 | 准确对象/映射与版本依据 | B / 对应 Provider |

接口依赖：AL-B01、AL-B03、AL-B05、AL-P201。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. 无效生命周期、旧版本、越权和未知权限候选均不能被采用。
2. Working TTL 与长期 Projection 可读性分别校验。
3. 悬空引用可定位到 memory/representation/版本和 trace。
4. 保留最终复核所需逐项身份及证据；不重新定义 B 的领域过滤规则。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D001 | 资格与引用校验规则 |
| 开发 | D002 | D004 | MemoryRead 消费适配与结果记录 |
| 测试 | D005 | D006 | 失效/越权/未知/悬空引用测试 |
| 联调 | D007 | D008 | B 批量核验及有效窗口联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 1 |
| 开发 | 3 |
| 测试 | 2 |
| 联调 | 2 |
| **总人天** | **8** |

### 10. 备注

- WBS 对应：A-EPIC-06-T1。
- PRR Action：A-P1-2。
- 设计依据：主设计第 3.4、3.7 节；数据定义第 8 章；B 对接说明。
- 工作量及责任边界：B 定义并提供 Memory 权威事实，A 消费这些事实完成候选校验。首次校验和复用适配在本项实现；返回前的最终复核编排与 Pack 重组归 A-EPIC-09。

---
## A-EPIC-07 · Canonical Load

**Epic 范围**：经 B 的 mapping + ContentStorePort 读取原文；checksum/not_found/partial。

**关联 PRR Action**：—。

**Epic 级完成标准**：canonical payload 加载失败可定位；checksum/not_found/partial 不被静默吞掉；A 不拥有物理 payload 事实。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-07 |
| Epic Name | Canonical Load |
| Feature ID | A-FEAT-07-01 |
| Feature Name | 可信原文加载与预热回退 |
| Owner | A（Recall / Context Serving） |
| Contributor | B；P2 / C / RF |
| 目标里程碑 | M2 |

### 2. User Story

作为 Recall 调用方，我希望读取与获准 Memory 版本及范围一致的完整正文，并在可选热副本不可用时回退至正式内容，从而获得可信内容和可定位的加载结果。

### 3. 功能描述

#### 3.1 功能目标

通过 B 正式映射加载 Working 内联内容或 canonical 原文，校验完整性并控制读取资源。

#### 3.2 核心输入

- 已通过资格校验的候选、B 正式 content_ref/版本/范围/授权/expected_hash。
- 读取次数/字节预算、deadline 和可选热副本证据。

#### 3.3 核心处理

1. 校验已有内联字节；需物理读取时按需补 head 元数据。
2. 调用前预占次数和字节；仅在可保障正式回退时尝试可选 Prewarm。
3. 按准确版本/范围读取正式正文，核对编码、hash 和实际来源，结算或保守保留额度。

#### 3.4 核心输出

- ContentLoadResult 与 RecallCandidate 可信正文快照。
- 读取账本、checksum/范围校验结果及真实加载来源。

### 4. 正常流程

取 B 内容映射 → 校验内联正文或准备读取 → 预占额度 → 可选 Prewarm → 必要时 canonical get/get_range → 校验/结算 → 保存可信正文。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 缓存 miss、失效或释放竞态 | 在预算内由唯一责任方执行正式回退 | 回退成功不因缓存故障自动降级 |
| checksum 不符、not_found 或断流 | 拒绝采用不可信正文，保留原因 | 不把 partial 字节当完整内容 |
| 重启后接收字节未知 | 预算保守按预留扣费，统计保持未知 | 不重置额度或伪造真实流量 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 资格结果与内容映射 | 当前有效候选及准确内容授权/摘要 | A-FEAT-06-01 / B |
| OBJ-001 | get/get_range、按需 head、条件版本与字节上限 | P2 / Durable Provider |
| 可选热副本 | 获准表示、释放行为和真实来源 | C / B / Provider |
| 资源与账本 | 原子预占、并发及取消支持 | RF |

接口依赖：AL-B02、AL-P203、AL-P204、AL-C01、AL-C04、AL-RF04。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. 跨版本、范围、编码或 checksum 不符的内容不被采用。
2. Working 内联正文同样验证；Working 与 Prewarm 不混用。
3. 预热失败后可信正式回退不自动降级，Provider 已内部回退时不重复回退。
4. 读取预算在并发、断流和崩溃下仍有界，真实 bytes 未知不填为零。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D002 | 映射、校验、读取账本与回退设计 |
| 开发 | D003 | D005 | 正文适配器、完整性校验和可选热读 |
| 测试 | D006 | D007 | 损坏/超限/断流/释放/重启测试 |
| 联调 | D008 | D009 | B 映射及 Provider 正文读取联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 2 |
| 开发 | 3 |
| 测试 | 2 |
| 联调 | 2 |
| **总人天** | **9** |

### 10. 备注

- WBS 对应：A-EPIC-07-T1。
- PRR Action：—。
- 设计依据：主设计第 3.5、3.5.1 节；数据定义第 9、10 章及读取账本；P2 RP2-02～05；C 对接。
- 工作量及责任边界：包括正式原文及可选热副本读取、完整性校验和资源账本，不建设底层物理存储或 C 的调度能力。成功正式回退不因缓存失败单独降级。

---
## A-EPIC-08 · Rank / Decay / Conflict Execution

**Epic 范围**：消费 B 提供的语义属性、衰减/冲突/证据规则执行排序。

**关联 PRR Action**：—。

**Epic 级完成标准**：B 定义 Memory Domain 属性/规则，A 对 Recall 执行结果负责；排序可解释、可追踪输入来源。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-08 |
| Epic Name | Rank / Decay / Conflict Execution |
| Feature ID | A-FEAT-08-01 |
| Feature Name | 排序、衰减与冲突规则执行 |
| Owner | A（Recall / Context Serving） |
| Contributor | B |
| 目标里程碑 | M2 |

### 2. User Story

作为上下文使用方，我希望优先获得相关且可解释的记忆，减少重复内容并完整保留必要冲突，从而避免排序或裁剪掩盖重要证据和分歧。

### 3. 功能描述

#### 3.1 功能目标

对已可信加载的候选去重、融合来源排名，消费 B 的语义/衰减/冲突策略。

#### 3.2 核心输入

- 已校验且已加载的 RecallCandidate、各来源 rank 及真实来源证据。
- B 提供的版本化语义策略、衰减/证据属性、完整冲突成员和获准回退。

#### 3.3 核心处理

1. 按内容身份合并重复，固定代表候选及正文/加载路径绑定。
2. 按版本化规则融合来源内排名，不直接比较异构原始分数。
3. 执行 B 语义策略并形成必须一起呈现的冲突关系，保存稳定排序依据。

#### 3.4 核心输出

- RankedRecallCandidates、可追溯 final_rank 和 policy_version。
- 冲突成员及解释、语义回退或隔离原因。

### 4. 正常流程

接收可信候选 → 内容身份去重 → 来源排名融合 → 执行 B 语义规则 → 核验完整冲突成员 → 保存稳定排序。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 语义事实缺失 | 仅用 B 明确批准的回退，否则隔离 | 保留缺口，不编造默认语义 |
| 冲突成员不完整或不安全 | 整组隔离 | 不只呈现单方 |
| 同内容重复命中或同分 | 按固定代表与稳定键处理 | 不靠重复次数抬高权重 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 可信正文 | 身份、加载结果与来源依据 | A，A-FEAT-07-01 |
| Memory 语义规则 | 策略、衰减、冲突成员与证据 | B |

接口依赖：AL-B04。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. 只排序已通过校验和加载的候选；高分不覆盖权限。
2. 重复内容合并后仍保留来源证据，实际加载归因不拼接其他候选。
3. 同输入与策略版本得到稳定排序，不直接混比来源 raw_score。
4. 冲突完整呈现或整组隔离，语义回退记录原因。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D001 | 去重、融合及冲突规则设计 |
| 开发 | D002 | D004 | 稳定排序和 B 策略执行实现 |
| 测试 | D005 | D005 | 重复/同分/缺语义/冲突测试 |
| 联调 | D006 | D006 | B 策略及冲突成员联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 1 |
| 开发 | 3 |
| 测试 | 1 |
| 联调 | 1 |
| **总人天** | **6** |

### 10. 备注

- WBS 对应：A-EPIC-08-T1。
- PRR Action：—。
- 设计依据：主设计第 3.6 节；数据定义第 11 章；B 语义策略对接。
- 工作量及责任边界：B 定义语义属性和衰减/冲突/证据规则，A 执行并记录依据。Token 分组、最终复核与预算装配归 A-EPIC-09。

---
## A-EPIC-09 · Context Assembly

**Epic 范围**：Context Pack；Token Budget；complete/degraded/failed；truncated；missing_sources。

**关联 PRR Action**：—。

**Epic 级完成标准**：Context Pack 状态正确；缺失源与预算截断显式；不能把 degraded 冒充 complete。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-09 |
| Epic Name | Context Assembly |
| Feature ID | A-FEAT-09-01 |
| Feature Name | 上下文组装、预算控制与可靠交付 |
| Owner | A（Recall / Context Serving） |
| Contributor | B2；RF / 实际调用方 |
| 目标里程碑 | M2 |

### 2. User Story

作为 Agent 调用方，我希望获得预算内、引用可追踪且状态准确的 Context Pack，从而安全使用内容，并区分完整有内容、正常空、降级和失败结果。

### 3. 功能描述

#### 3.1 功能目标

实现最终复核、ContextPack 装配、四终态判定及可靠提交/结果重放。

#### 3.2 核心输入

- 稳定排序和完整冲突成员、已加载候选及替补。
- 调用方确认的模板/tokenizer、token_budget、deadline、来源覆盖、缺失和授权证据。

#### 3.3 核心处理

1. 按不可拆分内容组试装，对全部已加载可组装候选及替补向 B 做一轮最终复核。
2. 移除失效/未知项，重算完整冲突组和实际渲染 Token，再按规则判定终态。
3. 一致保存 Pack、终态、最小 Trace 和已产生事件 Outbox；确认提交后返回，并执行同键重放/保留规则。

#### 3.4 核心输出

- ContextPack / ContextItem：真实文本、引用、版本、used_tokens、truncated 和缺失说明。
- 四业务终态：COMPLETE_AVAILABLE、COMPLETE_EMPTY、DEGRADED_AVAILABLE、FAILED_UNAVAILABLE；对应 result_class/recall_availability。

### 4. 正常流程

试装预算组 → B 最终复核全部已加载候选 → 剔除并重算 → 判定拟议终态 → 一致提交/查证 → 有效期内返回。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 有合格组但全部装不下 | 返回预算失败，正文为空 | 不能标为 COMPLETE_EMPTY |
| 最终权限或安全证明失败 | 清空待交付内容并安全失败 | 不跳过复核 |
| 提交效果未知或恢复超期 | 按原提交身份查证或依既定隔离规则收尾 | 未证明提交不发出正文/业务终态 |
| 历史正文已清除或版本失效 | 按既有重放规则拒绝或返回可证明结果 | 不以同键新建执行补正文 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 排序与资格适配 | 已加载候选和最终复核消费能力 | A-FEAT-08-01 / 06-01 |
| 最终权威核验 | 当前状态、授权、版本、TTL 与有效窗口 | B |
| 调用格式 | 真实注入模板、tokenizer 与预算消费 | 实际调用方 / Agent Runtime |
| 一致持久化 | 唯一提交、恢复查证、Outbox 和分期清除 | RF |

接口依赖：AL-B03、AL-B06、AL-RF02、AL-P401、AL-P403。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. used_tokens 按完整渲染文本精确计数且不超预算，冲突组不被拆散。
2. 替补候选也经过最终复核，不临时重搜或加载新版本补包。
3. 完整有内容、完整空、降级有内容、失败不可用四种终态均有可复现用例。
4. 缓存成功回退及正常 Token 裁剪不单独强制降级；来源故障不冒充正常空。
5. 一致提交得到确认前不发布正文；提交未知、重复调用、崩溃和正文清除后重放保持安全。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D002 | 预算、复核、终态与提交设计 |
| 开发 | D003 | D007 | Pack 装配、一致提交及重放实现 |
| 测试 | D008 | D010 | 四终态/超预算/复核变化/提交崩溃测试 |
| 联调 | D011 | D012 | B、RF 与实际调用方端到端交付联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 2 |
| 开发 | 5 |
| 测试 | 3 |
| 联调 | 2 |
| **总人天** | **12** |

### 10. 备注

- WBS 对应：A-EPIC-09-T1、A-EPIC-09-T2。
- PRR Action：—。
- 设计依据：主设计第 3.7、3.8、3.8.1、4 章；数据定义第 12～14 章及提交/保留嵌入结构；总流程状态机。
- 工作量及责任边界：包含最终复核、预算、四终态、最小 Trace/Outbox 一致提交、重放和保留行为。完整阶段事件投递与旁路归因归 A-EPIC-10；发出正文不等于模型已使用。

---
## A-EPIC-10 · Recall Observability, SLO, Audit & Acceptance

**Epic 范围**：Recall trace；degraded/缺失源告警；E2E SLO/availability；结果重放审计；Benchmark/Acceptance Evidence。

**关联 PRR Action**：A-P0-4、A-P1-1、A-P1-4（并承接 A-P0-5 的发布验收证据）。

**Epic 级完成标准**：Query→Context Pack trace_id 贯通；complete/degraded/failed、缺失源、projection/model/version 可查；SLO/availability 有口径；告警可触发；同 trace 可定位/重放；Lane 2 与 Lane 3 验收证据分离。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-10 |
| Epic Name | Recall Observability, SLO, Audit & Acceptance |
| Feature ID | A-FEAT-10-01 |
| Feature Name | 召回可观测性、SLO、审计与验收 |
| Owner | A（Recall / Context Serving） |
| Contributor | RF-Steward；B / C / P2 / 实际调用方 / 验收负责人 |
| 目标里程碑 | M3 |

### 2. User Story

作为运维人员、Operate 调度模块和验收负责人，我希望查询完整 Recall 访问过程、监控服务指标和故障告警，并凭同一 trace 审计回放关键决策及验证预热使用事实，从而定位错误、评估服务表现并确认交付质量。

### 3. 功能描述

#### 3.1 功能目标

贯通 Query→Context Pack 的 Trace 与业务访问事件，提供可靠投递、可选预热使用核验、E2E SLO/availability 指标与告警、审计回放及模拟/真实环境分离的验收证据。

#### 3.2 核心输入

- 各阶段真实访问观察：request/trace、memory/representation、版本、来源、latency、bytes。
- C/Provider 动作成功、实际副本及有效窗口证据；RF 覆盖证据；可用的实际调用方回执。
- Recall 终态、阶段延迟、reason_code、来源覆盖和真实 Trace。
- 获准统计窗口、分母、阈值和 Acceptance Profile。
- trace_id、固定模型/策略/模板版本、候选和校验快照、来源/预算/提交证据。
- 现有开发验收场景、P2/B/C 联调场景、性能报告和发布演练记录。

#### 3.3 核心处理

1. 分别记录 Search Hit、Candidate Accepted、Canonical Loaded、Context Selected、Context Emitted 等实际时点。
2. 映射获批共享 Schema，经 Outbox 稳定身份投递和补发，保留 Ack 的真实含义。
3. 旁路关联准确动作、版本、表示、实际副本和窗口，形成可版本化核验；无使用回执时保持未知。
4. 定义 complete/degraded/failed、available/empty/unavailable 的指标映射与统计分母。
5. 生成 E2E P95/P99、降级率、缺失源等指标及诊断维度。
6. 按签收阈值触发/恢复告警，提供来源、阶段和 trace 定位入口。
7. 按权限读取保存的证据重放决策，核对过滤、排序、预算和终态。
8. 设计并执行完整读链路、投影写链路、故障恢复和真实依赖集成用例。
9. 汇集各 Feature 专项证据，分别出具模拟契约和真实 P2 验收结论及未满足项。

#### 3.4 核心输出

- RecallTrace、RecallAccessObservation、OutboxEntry。
- ContextUseAssessment / PlacementVerificationRecord：真实命中、未知原因、证据和评估版本。
- 指标定义、仪表盘、告警规则和诊断说明。
- 告警触发、定位及恢复证据。
- 可追溯审计/回放结果与不可重放原因。
- 端到端测试记录、Lane 2 / Lane 3 分列的验收报告和证据索引。

### 4. 正常流程

各阶段生成真实 Trace/访问观察 → 可靠保存并经 Outbox 投递 → 聚合终态、时延和来源指标 → 触发或恢复告警 → 按证据执行旁路核验与审计回放 → 执行跨功能和真实 P2 验收 → 汇集 Benchmark、发布演练及验收报告。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| RF 暂不可用或回包丢失 | 保留 Outbox 并按原身份补发 | 不把 Ack 当作 C 已消费 |
| 缺实际副本、版本或动作关联 | 保留不可验证原因 | 不按时间接近推断命中 |
| 窗口未闭合或事件覆盖不足 | 保留未知；迟到证据生成新评估版本 | 不当作未命中 |
| 只有 Context 发出记录 | 只证明发出事实 | 不推断调用方收到或模型已使用 |
| 正常空或正常预算裁剪 | 按业务规则分类 | 不一律计为失败或降级 |
| 遥测缺失 | 标明覆盖不足并暴露观测异常 | 不输出虚假的健康率 |
| 依赖半故障 | 按实际来源/阶段呈现告警 | 不只报无信息的总错误 |
| 证据已清除或缺少必要版本 | 明确不可重放原因 | 不声称可以精确复现 |
| 当前权限失效 | 限制审计/重放内容输出 | 不通过历史 Pack 绕过授权 |
| 模拟通过但真实 P2 未就绪 | 只报告模拟契约结果 | 不标记真实联调通过 |
| 专项指标或发布演练不通过 | 记录未满足项并定位负责 Feature | 不通过全链路放行 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| Recall 各阶段 | 真实时点、来源、加载与交付事实 | A，各主链路 Feature |
| 共享事件体系 | AccessTrace Schema、摄取/Ack 和覆盖证据 | RF |
| 动作与副本证据 | 成功动作、实际表示、generation 或等效关联 | C / Provider |
| 使用回执 | 接收与模型使用事实 | 实际调用方；P4 可转交 |
| 终态和追踪 | 准确分类与阶段指标 | A-FEAT-09-01 / 10-01 |
| 监控基础 | 指标采集、查询和告警通道 | RF / 监控 Provider |
| 验收口径 | SLO、阈值、窗口及统计分母 | 项目验收负责人 |
| 全部 A Feature | 可运行实现、专项测试和版本证据 | A |
| 真实联调环境 | B、P2、RF、调用方，适用时 C | 各接口 Provider |
| 验收评测 | 签收标准、测试数据及放行评审 | 项目验收负责人 |

接口依赖：AL-C01～04、AL-P204、AL-RF02、AL-RF03、AL-P402、AL-PM01。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. Query 到 Context 的 trace_id 可贯通，模型/投影版本、过滤和缺失原因可定位。
2. 检索、校验、加载、选择、发出分别记录，bytes/latency 使用真实观察口径。
3. 重复、乱序、重启和丢失回包不制造新的业务访问，投递状态不冒充消费状态。
4. 只有匹配实际副本与版本等证据才归因；无覆盖证据不判未命中。
5. 旁路核验不阻塞基础 Recall，不改写 C 的 TierAction/Plan，不凭相关性宣称性能收益。
6. 四业务终态到三类结果和 availability 的统计映射一致。
7. degraded 率、缺失源与 E2E 时延均能查询并按来源定位。
8. 注入超时/partial/后端异常能触发相应告警，恢复能留存证据。
9. 正式阈值按 Acceptance Profile 执行，达标结论以实测证据为依据。
10. 同 trace 能解释用了哪个 Memory/投影/模型、为何过滤或降级。
11. 固定证据可重放关键决策；缺失/过期/撤权场景不伪造复现。
12. 模拟与真实环境结论分开，正常、空、降级、失败及关键恢复链覆盖。
13. 性能双 Profile、模型发布演练和告警证据可引用，所有未满足项有归属。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D004 | 阶段事件、投递与归因规则；SLO/availability、分母及阈值方案；审计回放与 E2E 验收方案 |
| 开发 | D005 | D015 | Trace、Outbox 及旁路核验实现；指标、仪表盘与告警配置；审计/回放工具与集成场景编排 |
| 测试 | D016 | D023 | 重复/乱序/缺证据/迟到修订测试；分类准确性及告警触发/恢复测试；跨 Feature 回归与证据一致性检查 |
| 联调 | D024 | D029 | RF/C 事件消费与核验记录交接；监控环境与验收口径联调；真实 P2 E2E 及验收报告 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 4 |
| 开发 | 11 |
| 测试 | 8 |
| 联调 | 6 |
| **总人天** | **29** |

### 10. 备注

- WBS 对应：A-EPIC-10-T1、A-EPIC-10-T2。
- PRR Action：A-P0-4、A-P1-1、A-P1-4（并承接 A-P0-5 的发布验收证据）。
- 设计依据：主设计第 5.2、5.3、7 章；数据定义第 14～18 章；C 对接 RC-01～06。；Epic A-EPIC-10；A 生产就绪补充；主设计第 4、7、9 章。；WBS A-EPIC-10；A 工作包完整 DoD；主设计第 9 章及 P2/B/C 接口验收清单。
- 工作量及责任边界：包含追踪/预热核验、SLO/告警、审计回放和统一验收。各 Epic 局部测试与业务实现不重复计入；A-P0-5 的发布实现及专项演练归 A-EPIC-01，本项承接其证据并完成全链路验收。RF 拥有共享 Schema/Ingest，C 拥有策略及动作事实。

---

## 附录：WBS / PRR 覆盖

| Epic ID | 统一 Feature ID | WBS Task | PRR Action |
| --- | --- | --- | --- |
| A-EPIC-01 | A-FEAT-01-01 | A-EPIC-01-T1、A-EPIC-01-T2、A-EPIC-01-T3、A-EPIC-01-T4 | A-P0-2、A-P0-3、A-P0-5 |
| A-EPIC-02 | A-FEAT-02-01 | A-EPIC-02-T1、A-EPIC-02-T2、A-EPIC-02-T3 | — |
| A-EPIC-03 | A-FEAT-03-01 | A-EPIC-03-T1、A-EPIC-03-T2 | — |
| A-EPIC-04 | A-FEAT-04-01 | A-EPIC-04-T1、A-EPIC-04-T2 | A-P0-1 |
| A-EPIC-05 | A-FEAT-05-01 | A-EPIC-05-T1、A-EPIC-05-T2、A-EPIC-05-T3 | A-P1-3 |
| A-EPIC-06 | A-FEAT-06-01 | A-EPIC-06-T1 | A-P1-2 |
| A-EPIC-07 | A-FEAT-07-01 | A-EPIC-07-T1 | — |
| A-EPIC-08 | A-FEAT-08-01 | A-EPIC-08-T1 | — |
| A-EPIC-09 | A-FEAT-09-01 | A-EPIC-09-T1、A-EPIC-09-T2 | — |
| A-EPIC-10 | A-FEAT-10-01 | A-EPIC-10-T1、A-EPIC-10-T2 | A-P0-4、A-P1-1、A-P1-4（并承接 A-P0-5 的发布验收证据） |

每个 Epic 的工作量包含其 WBS 和关联的生产就绪任务。A-P0-5 的发布实施与专项演练只在 A-EPIC-01 核算；A-EPIC-10 承接发布验收证据和全链路验证。

原 A-01～A-04 是运行时设计主题，不等同于 A-EPIC-01～04。来源与加载落实于 A-EPIC-04～07；失败处理分布在各功能并于 A-EPIC-09 统一终态；AccessTrace 与 Placement Verification 纳入 A-EPIC-10。
