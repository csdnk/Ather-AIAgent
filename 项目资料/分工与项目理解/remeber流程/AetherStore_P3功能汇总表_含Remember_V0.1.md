# AetherStore P2 / P3 · 公司功能汇总与负责人说明 V0.1

更新：2026-09-08。状态：功能规划。当前收录：P3 / A（Recall / Context Serving）、B（Remember / Memory Formation）。

## 1. 汇总

### 1.1 功能汇总

| Project | Epic ID | Epic Name | Feature ID | Feature Name | User Story 摘要 | 功能描述摘要 | Owner | 开始时间（Epic 内） | 完成时间（Epic 内） | 总人天 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P3 | A-EPIC-01 | Semantic Compute & Resource Isolation | [A-FEAT-01-01](AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-01--semantic-compute--resource-isolation) | 共享向量计算、资源隔离与模型发布 | 作为 Remember 模块、Recall Runtime 和模型服务维护者，我希望获得兼容、可追踪且有资源保障的正文/查询向量服务，并能安全升级和回退模型，从而支撑记忆写入与在线检索，控制混合负载及模型变更风险。 | 统一提供 Passage/Query 真实向量生成、模型与检索空间绑定、完成结果复用、资源隔离、性能验证，以及 Shadow/Canary/Rollback 受控发布能力。 | A | D001 | D028 | 28 | 功能规划 |
| P3 | A-EPIC-02 | Vector Projection Mechanism | [A-FEAT-02-01](AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-02--vector-projection-mechanism) | 向量投影写入、确认、恢复与删除 | 作为 Remember 投影构建与维护模块，我希望将获准向量写入存储、查询真实完成状态，并在超时、版本更新和删除时安全收敛，从而为领域 Ready 判定提供可信证据，避免重复写入、旧版本覆盖和删除后复活。 | 统一提供 VectorProjectionPort 契约、五元组幂等、ProviderResult 五态处理、写入及操作/目标查询、有界恢复、版本更新与准确目标删除。 | A | D001 | D020 | 20 | 功能规划 |
| P3 | A-EPIC-03 | Vector Backend Simulator | [A-FEAT-03-01](AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-03--vector-backend-simulator) | 向量后端模拟与故障验证 | 作为 Recall 与 Remember 开发者，我希望在真实 P2 尚未就绪时验证向量写入、查询和故障恢复契约，从而提前开发并发现接口和状态处理问题。 | 提供有状态 Vector Backend Simulator，支撑向量写读契约与可重现故障验证。 | A | D001 | D007 | 7 | 功能规划 |
| P3 | A-EPIC-04 | Query Runtime & Ingress Protection | [A-FEAT-04-01](AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-04--query-runtime--ingress-protection) | 召回查询接入与流量保护 | 作为 Agent 调用方，我希望提交问题、授权范围和预算后获得自动选路、兼容 Query 向量和有界准入的召回服务，从而简化调用并避免突发流量压垮在线链路。 | 完成 Recall 入口、内部来源选择、Query 调用、幂等执行和有界准入。 | A | D001 | D009 | 9 | 功能规划 |
| P3 | A-EPIC-05 | Candidate Retrieval & Search Degradation | [A-FEAT-05-01](AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-05--candidate-retrieval--search-degradation) | 候选检索与搜索降级 | 作为 Recall 调用方，我希望按查询条件获得当前 Working 和相关长期记忆候选，并明确获知来源缺失及部分故障，从而正确判断本次检索是否完整。 | 按既定模式读取 Working 和长期候选，统一记录来源覆盖、稳定引用和搜索故障。 | A | D001 | D008 | 8 | 功能规划 |
| P3 | A-EPIC-06 | Candidate Validation & Reference Integrity | [A-FEAT-06-01](AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-06--candidate-validation--reference-integrity) | 候选有效性与引用完整性校验 | 作为 Recall 调用方，我希望只有当前版本、权限及状态有效且引用可解析的候选进入后续流程，从而避免过期、已删除、越权或悬空引用导致错误上下文。 | 消费 B 的权威 MemoryRead 事实校验候选，并提供可供最终复核复用的校验适配。 | A | D001 | D008 | 8 | 功能规划 |
| P3 | A-EPIC-07 | Canonical Load | [A-FEAT-07-01](AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-07--canonical-load) | 可信原文加载与预热回退 | 作为 Recall 调用方，我希望读取与获准 Memory 版本及范围一致的完整正文，并在可选热副本不可用时回退至正式内容，从而获得可信内容和可定位的加载结果。 | 通过 B 正式映射加载 Working 内联内容或 canonical 原文，校验完整性并控制读取资源。 | A | D001 | D009 | 9 | 功能规划 |
| P3 | A-EPIC-08 | Rank / Decay / Conflict Execution | [A-FEAT-08-01](AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-08--rank--decay--conflict-execution) | 排序、衰减与冲突规则执行 | 作为上下文使用方，我希望优先获得相关且可解释的记忆，减少重复内容并完整保留必要冲突，从而避免排序或裁剪掩盖重要证据和分歧。 | 对已可信加载的候选去重、融合来源排名，消费 B 的语义/衰减/冲突策略。 | A | D001 | D006 | 6 | 功能规划 |
| P3 | A-EPIC-09 | Context Assembly | [A-FEAT-09-01](AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-09--context-assembly) | 上下文组装、预算控制与可靠交付 | 作为 Agent 调用方，我希望获得预算内、引用可追踪且状态准确的 Context Pack，从而安全使用内容，并区分完整有内容、正常空、降级和失败结果。 | 实现最终复核、ContextPack 装配、四终态判定及可靠提交/结果重放。 | A | D001 | D012 | 12 | 功能规划 |
| P3 | A-EPIC-10 | Recall Observability, SLO, Audit & Acceptance | [A-FEAT-10-01](AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-10--recall-observability-slo-audit--acceptance) | 召回可观测性、SLO、审计与验收 | 作为运维人员、Operate 调度模块和验收负责人，我希望查询完整 Recall 访问过程、监控服务指标和故障告警，并凭同一 trace 审计回放关键决策及验证预热使用事实，从而定位错误、评估服务表现并确认交付质量。 | 贯通 Query→Context Pack 的 Trace 与业务访问事件，提供可靠投递、可选预热使用核验、E2E SLO/availability 指标与告警、审计回放及模拟/真实环境分离的验收证据。 | A | D001 | D029 | 29 | 功能规划 |
| P3 | B-EPIC-01 | Ingest & Fact Formation | [B-FEAT-01-01](AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-01--ingest--fact-formation) | 记忆事件接入与可靠主事实形成 | 作为提交对话、任务结果或显式记忆的上游系统，我希望每次写入都能获得可追踪的事实身份和准确的完成状态，从而在超时重试、服务重启或后台尚未完成时，仍能确认内容是否已可靠保存并继续查询。 | 建立从可信 MemoryEvent 到可靠 MemoryRecord 的接入、幂等、提交和响应闭环，区分主事实已形成、当前 Working 可用与长期派生完成。 | B | D001 | D020 | 20 | 功能规划 |
| P3 | B-EPIC-02 | Lifecycle & Policy Versioning | [B-FEAT-02-01](AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-02--lifecycle--policy-versioning) | 记忆分类、生命周期与策略版本管理 | 作为需要持续记住当前任务、历史事件和稳定知识的业务系统，我希望记忆能按证据、时间和明确策略分类、巩固、更正或失效，从而让新旧事实、冲突和过期内容具有可解释的处理结果。 | 统一类型、生命周期、证据依赖和版本规则，可靠形成 Semantic，向 A 提供版本化衰减与冲突语义，向后续表示和 Signal 提供资格变化。 | B | D001 | D022 | 22 | 功能规划 |
| P3 | B-EPIC-03 | Durable Content & Capacity Guard | [B-FEAT-03-01](AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-03--durable-content--capacity-guard) | 可靠正文存储、内容映射与容量保护 | 作为 Remember 和读取可信正文的 Recall，我希望每条 Memory 都能定位到获准、完整、持久的精确内容版本，并在容量不足或写入超时后得到明确结果，从而避免空引用、错版本和错误重写。 | 交付 OBJ-001 消费契约、内容模拟器、不可变内容绑定与真实 Provider 适配，补齐容量保护、完整性核验和写入未知的查询能力。 | B | D001 | D018 | 18 | 功能规划 |
| P3 | B-EPIC-04 | Memoryize / Compression & Background Capacity | [B-FEAT-04-01](AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-04--memoryize--compression--background-capacity) | 异步记忆加工、压缩与后台容量治理 | 作为需要将长文本和长期记忆可靠加工的业务系统，我希望耗时操作能由可查询、可恢复的后台任务完成，并在保护当前交互的同时生成可信压缩表示，从而兼顾内容质量、存储效率和前台响应。 | 提供 Task/Outbox 到 Worker 的可靠执行骨架、可选压缩及质量门槛、Artifact 发布和后台资源预算，供压缩、投影重建及恢复任务复用。 | B | D001 | D028 | 28 | 功能规划 |
| P3 | B-EPIC-05 | Make Recallable & Projection Health | [B-FEAT-05-01](AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-05--make-recallable--projection-health) | 长期记忆可召回构建与投影健康管理 | 作为需要检索长期记忆的 Recall，我希望每个可召回标记都对应当前有效正文和完整、可查询的向量集合，从而避免部分构建、错模型或旧版本被当成已经可用的记忆。 | 统一内联/后台投影构建，固定完整输入，消费 A 的机制证据，执行 Ready Guard、条件发布、失效重建及积压告警。 | B | D001 | D024 | 24 | 功能规划 |
| P3 | B-EPIC-06 | MemoryRead Capability | [B-FEAT-06-01](AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-06--memoryread-capability) | 当前 Working 读写与权威记忆读取能力 | 作为组装当前上下文和长期记忆的 Recall，我希望通过稳定能力获取当前 Working、Memory 资格和合法正文映射，并在发出前重新核对，从而正确处理空结果、不可核验、旧版本及失效内容。 | 交付当前 Working 物化与有界读取、MemoryRead 快照、语义属性与 Canonical 映射交接，并提供初查、发出前和重放前所需复核能力。 | B | D001 | D016 | 16 | 功能规划 |
| P3 | B-EPIC-07 | MemorySignal Capability | [B-FEAT-07-01](AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-07--memorysignal-capability) | 记忆变化信号、可靠投递与消费确认 | 作为接收记忆变化并决定驻留与调度的 Operate，我希望获得有来源、有版本、可重放的 MemorySignal，并能明确反馈消费结果，从而在重复、乱序和中断时继续处理真实业务变化。 | 实现不可变变化载荷、事实与事件可靠提交、至少一次投递、匹配 C 持久消费 Ack 和有界补发，区分事件、传输与 C 动作状态。 | B | D001 | D012 | 12 | 功能规划 |
| P3 | B-EPIC-08 | Recovery / Reconciliation & Retry Governance | [B-FEAT-08-01](AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-08--recovery--reconciliation--retry-governance) | 记忆恢复对账、全局重试与故障治理 | 作为维护 Remember 连续服务的运行人员，我希望在进程、存储、网络和迁移发生故障后，能根据真实证据恢复任务、修复派生结果和清理失效表示，并控制重试压力，从而恢复服务且不丢事实、不复活旧数据。 | 建立横切各 Epic 的统一对账扫描、业务重试预算、Redis 半故障准入、精确删除/恢复屏障和故障矩阵，汇总 B 侧真实集成及生产验收证据。 | B | D001 | D028 | 28 | 功能规划 |

### 1.2 工作量汇总

| Project | Owner | Epic ID | Epic Name | Feature 数 | 设计 | 开发 | 测试 | 联调 | 总人天 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P3 | A | A-EPIC-01 | Semantic Compute & Resource Isolation | 1 | 5 | 12 | 7 | 4 | 28 |
| P3 | A | A-EPIC-02 | Vector Projection Mechanism | 1 | 4 | 8 | 4 | 4 | 20 |
| P3 | A | A-EPIC-03 | Vector Backend Simulator | 1 | 1 | 3 | 2 | 1 | 7 |
| P3 | A | A-EPIC-04 | Query Runtime & Ingress Protection | 1 | 2 | 4 | 2 | 1 | 9 |
| P3 | A | A-EPIC-05 | Candidate Retrieval & Search Degradation | 1 | 1 | 3 | 2 | 2 | 8 |
| P3 | A | A-EPIC-06 | Candidate Validation & Reference Integrity | 1 | 1 | 3 | 2 | 2 | 8 |
| P3 | A | A-EPIC-07 | Canonical Load | 1 | 2 | 3 | 2 | 2 | 9 |
| P3 | A | A-EPIC-08 | Rank / Decay / Conflict Execution | 1 | 1 | 3 | 1 | 1 | 6 |
| P3 | A | A-EPIC-09 | Context Assembly | 1 | 2 | 5 | 3 | 2 | 12 |
| P3 | A | A-EPIC-10 | Recall Observability, SLO, Audit & Acceptance | 1 | 4 | 11 | 8 | 6 | 29 |
| P3 | A | **小计** |  | 10 | 23 | 55 | 33 | 25 | **136** |
| P3 | B | B-EPIC-01 | Ingest & Fact Formation | 1 | 3 | 9 | 5 | 3 | 20 |
| P3 | B | B-EPIC-02 | Lifecycle & Policy Versioning | 1 | 4 | 10 | 5 | 3 | 22 |
| P3 | B | B-EPIC-03 | Durable Content & Capacity Guard | 1 | 3 | 7 | 5 | 3 | 18 |
| P3 | B | B-EPIC-04 | Memoryize / Compression & Background Capacity | 1 | 5 | 12 | 7 | 4 | 28 |
| P3 | B | B-EPIC-05 | Make Recallable & Projection Health | 1 | 4 | 10 | 6 | 4 | 24 |
| P3 | B | B-EPIC-06 | MemoryRead Capability | 1 | 3 | 6 | 4 | 3 | 16 |
| P3 | B | B-EPIC-07 | MemorySignal Capability | 1 | 2 | 5 | 3 | 2 | 12 |
| P3 | B | B-EPIC-08 | Recovery / Reconciliation & Retry Governance | 1 | 5 | 10 | 8 | 5 | 28 |
| P3 | B | **小计** |  | 8 | 29 | 69 | 43 | 27 | **168** |
| P3 | A / B | **合计** |  | 18 | 52 | 124 | 76 | 52 | **304** |

### 1.3 汇总口径

- 开头的功能与工作量两张表用于公司汇总；其他模块按同一列结构追加，使用 Project、Owner、Epic ID、Feature ID 标识归属。
- 功能表的时间采用 **Epic 内独立计时**：每个 Epic 从 D001 起算，只累计本 Epic 的有效投入日，具体阶段见对应负责人填报。它不表示项目日历日期或所有功能同时开工。
- 模块整体周期在各负责人“人员与工作周期”章节中说明。Recall 与 Remember 分别采用 **3 人、90 个工作日**，按人员和接口依赖并行推进；不同 Epic 的局部周期不能顺次相加作为模块工期。
- 工作量表统计设计、开发、测试和联调的功能投入。Recall 合计 **136 人天**，三人分工为 A1 48、A2 44、A3 44 人天；Remember 合计 **168 人天**，三人分工为 B1 54、B2 62、B3 52 人天；已收录模块合计 **304 人天**。团队人数乘周期表示可用容量，不直接当成功能工作量。
- Owner 表示业务责任归属，执行岗位在各模块独立章节中说明；外部团队的实施投入不重复计入本模块。
- 公司汇总按已收录模块计算。跨模块日期按各自启动日和依赖关系对齐，共享人员按实际分配去重，各模块的工期不直接相加。

## 2. Recall 负责人 — Person A / Context Serving

### 2.1 责任目标与功能范围

**Primary Outcome**：从 Query 到最终 Context Pack 的完整读路径正确、可降级、可追踪；Recall 最终结果错误找 A。

**10 个 Epic、10 项功能，工作量估算 136 人天。** 每个 Epic 对应一个 Feature 和一套填报内容，详细输入输出、流程、验收及分阶段计划见[负责人填报](AetherStore_P3_Recall_功能负责人填报_V0.1.md)。

### 2.2 Epic 结构与责任范围

| Epic ID | 最终 Epic | Epic 范围 | 关联 PRR Action | Epic 级完成标准 |
| --- | --- | --- | --- | --- |
| A-EPIC-01 | Semantic Compute & Resource Isolation | passage/query 两类 Embedding；model contract/version/dimension；真实向量；吞吐与资源隔离；模型变更安全 | A-P0-2、A-P0-3、A-P0-5 | passage/query 语义清晰；query 不被 passage/后台抢占；隔离态与混合负载 Profile 均满足吞吐目标；模型/维度变化可 Shadow/Canary/Rollback |
| A-EPIC-02 | Vector Projection Mechanism | VectorProjectionPort；五元组幂等；ProviderResult 五态；写入/query/delete/version/schema | — | ProviderResult=ACCEPTED/PENDING/READY/FAILED/UNKNOWN 均有明确语义；A 不写 ProjectionState；版本/维度错误不误报 Ready |
| A-EPIC-03 | Vector Backend Simulator | 有状态向量模拟；index_ready；幂等；版本/维度；超时/失败/stale/partial/unknown 故障注入 | — | 可独立验证 VectorProjection / Search 契约与失败链；不依赖真实 P2；不实现真实 ANN |
| A-EPIC-04 | Query Runtime & Ingress Protection | Query→Query Embedding；契约校验；入口限流；Query Embedding 队列上限；Deadline | A-P0-1 | 突发流量下有明确 admission；429/Busy 语义稳定；队列有上限；限流时前台 P99 不失控 |
| A-EPIC-05 | Candidate Retrieval & Search Degradation | VectorSearchPort；TopK；filter；稳定引用；partial/degraded；Search 后端半故障/不可用降级 | A-P1-3 | Search 超时/不可用不冒充完整；降级阈值明确；结果携带稳定引用和版本；可输出 recall_availability |
| A-EPIC-06 | Candidate Validation & Reference Integrity | 调用 B 的 MemoryReadCapability；校验 Memory state/version/scope/conflict/evidence；悬空引用检测 | A-P1-2 | 只使用当前有效版本；object_ref / graph_node_ref 等引用悬空可识别并显式处理；不因脏候选产生完整结果 |
| A-EPIC-07 | Canonical Load | 经 B 的 mapping + ContentStorePort 读取原文；checksum/not_found/partial | — | canonical payload 加载失败可定位；checksum/not_found/partial 不被静默吞掉；A 不拥有物理 payload 事实 |
| A-EPIC-08 | Rank / Decay / Conflict Execution | 消费 B 提供的语义属性、衰减/冲突/证据规则执行排序 | — | B 定义 Memory Domain 属性/规则，A 对 Recall 执行结果负责；排序可解释、可追踪输入来源 |
| A-EPIC-09 | Context Assembly | Context Pack；Token Budget；complete/degraded/failed；truncated；missing_sources | — | Context Pack 状态正确；缺失源与预算截断显式；不能把 degraded 冒充 complete |
| A-EPIC-10 | Recall Observability, SLO, Audit & Acceptance | Recall trace；degraded/缺失源告警；E2E SLO/availability；结果重放审计；Benchmark/Acceptance Evidence | A-P0-4、A-P1-1、A-P1-4（并承接 A-P0-5 的发布验收证据） | Query→Context Pack trace_id 贯通；complete/degraded/failed、缺失源、projection/model/version 可查；SLO/availability 有口径；告警可触发；同 trace 可定位/重放；Lane 2 与 Lane 3 验收证据分离 |

### 2.3 人员与工作周期

- **3 人团队，整体周期 90 个工作日，功能投入 136 人天。** 每个 Epic 的阶段从 D001 独立计算，分别为 28、20、7、9、8、8、9、6、12、29 个有效投入日。
- A1、A2、A3 是 Recall 团队的三个执行岗位；Owner A 对整体结果负责。同一岗位承担的 Epic 可以穿插推进，但同一工作日不重复占用。
- 局部周期说明工作量与阶段先后，模块周期包含并行组织及接口衔接；具体开始执行时间取决于人员可用性和前置能力。

| 执行岗位 | 主要职责 | 负责 Epic | 功能投入（人天） |
| --- | --- | --- | --- |
| A1 | 共享 Embedding、资源隔离、模型发布与投影机制 | A-EPIC-01、A-EPIC-02 | 48 |
| A2 | Recall 入口、检索、正文、排序与 Context 交付 | A-EPIC-04、A-EPIC-05、A-EPIC-07、A-EPIC-08、A-EPIC-09 | 44 |
| A3 | 模拟器、资格校验、Trace/SLO/审计及端到端验收 | A-EPIC-03、A-EPIC-06、A-EPIC-10 | 44 |
| 合计 | 3 人 | 10 个 Epic | 136 |

### 2.4 并行开发与交接

| 可并行的工作 | 执行分工 | 前置条件与交接 |
| --- | --- | --- |
| Embedding、Recall 入口、向量模拟器 | A1：共享向量；A2：入口；A3：模拟器 | 先统一输入输出、模型/版本和接口契约；基础 Query 能力可用后进行真实入口联调。 |
| 投影、候选检索、资格校验和正文加载 | A1：投影；A2：检索/正文；A3：资格校验 | 使用模拟器和明确标识的契约样例独立开发；真实联调消费 P2/B 的对应能力。 |
| 排序与 Context、Trace/SLO/审计工具 | A2：排序/Context；A3：观测与验证 | 按固定样例和阶段事件契约开发；Trace 从前期接入，最终复核和可靠提交就绪后完成整链验证。 |
| 性能、模型发布与完整链路验收 | A1：性能/模型发布；A2：主链路支持；A3：验收与证据 | 相关模块具备交付条件后汇合，分别记录模拟验证和真实 P2 联调证据。 |

### 2.5 跨模块依赖

| 依赖方 | 需要提供的能力 | 主要影响的 Epic |
| --- | --- | --- |
| B / Remember | Working、Memory 权威事实、正式内容映射、语义规则、Ready Guard 和最终复核 | A-EPIC-02、04～09 |
| P2 / 内容 Provider | 向量写入/搜索/操作查询/目标核验/删除，以及准确版本的正文读取 | A-EPIC-02、05、07 |
| RF / 网关 / 实际调用方 | 身份与授权、可靠持久化/事件摄取、请求接入、注入模板和实际使用证据 | A-EPIC-01、02、04、09、10 |
| C / Operate | 可选预热表示、动作/副本证据及访问事件消费 | A-EPIC-07、10 |

接口字段、异常处理和接入条件见[负责人填报](AetherStore_P3_Recall_功能负责人填报_V0.1.md)及[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。本汇总保留责任、交付、资源和依赖要点，逐接口及逐阶段细节维护在负责人文档。

## 3. Remember 负责人 — Person B / Memory Formation

### 3.1 责任目标与功能范围

**Primary Outcome**：Memory 可靠形成、可追踪、可版本化、可被 Recall 使用；Memory 没形成找 B。

**8 个 Epic、8 项功能，工作量估算 168 人天。** 每个 Epic 对应一个 Feature 和一套填报内容，详细输入输出、流程、验收及分阶段计划见[负责人填报](AetherStore_P3_Remember_功能填报_V1.0.md)。

### 3.2 Epic 结构与责任范围

| Epic ID | 最终 Epic | Epic 范围 | 关联 PRR Action | Epic 级完成标准 |
| --- | --- | --- | --- | --- |
| B-EPIC-01 | Ingest & Fact Formation | MemoryEvent；Fact-First；幂等；Scope / Provenance；MemoryRecord；Success 与 Accepted 的区别 | — | 必要正文和主事实可靠提交、合法 Type 已发布后，按本次请求承诺判定结果；未完成的承诺输出只能如实返回 Accepted / Partial，重复事件不产生重复主事实 |
| B-EPIC-02 | Lifecycle & Policy Versioning | Working / Episodic / Semantic 逻辑语义；Active / Archived / Superseded / Expired / Deleted；分类、版本、冲突和衰减相关策略版本 | B-P0-5（衰减 / 策略部分） | 生命周期状态机可测，uncertain → Working，冲突不静默；分类/衰减策略及证据可追踪，发布可以回退 |
| B-EPIC-03 | Durable Content & Capacity Guard | Memory → content_ref mapping；ContentStorePort；Content Store Simulator；durable / checksum / head；容量 guard 与写超时确认 | B-P0-3、B-P1-3（checksum / head 部分） | 容量满时明确拒绝并告警；写超时通过原操作及精确版本 head/get 等证据收敛；checksum 可校验。B 维护业务映射，物理 payload 权威仍属于 Durable Store Provider |
| B-EPIC-04 | Memoryize / Compression & Background Capacity | 异步 Memoryize、压缩 Artifact、持久 Task、后台 Worker 并发/队列预算、前台恶化时节流、压缩策略受控发布 | B-P0-1、B-P0-5（压缩部分）、B-P1-3（Compression checksum 部分） | 压缩达到合同目标且质量合格；后台不拖垮 Working 前台，越线可降速/暂停；任务可恢复，失败保留 Original；压缩策略可灰度及回退 |
| B-EPIC-05 | Make Recallable & Projection Health | ProjectionState Building → Ready；调用 A 的 Embedding / Vector Projection；ProviderResult 五态、版本校验、Stale / Rebuild 和投影健康告警 | B-P0-4 | 只有 B 写入领域 ProjectionState；Provider 未 READY 或证据不足不转 Ready；Pending / Stale 积压可观测和告警，重启及版本不匹配可恢复 |
| B-EPIC-06 | MemoryRead Capability | 面向 A 的 scope / filter / version / state / confidence / stability / decay input / conflict / evidence；承接详细设计要求的当前 Working 物化、WorkingRead 与复核 | —（为 B-P0-1、B-P1-1 / 2 提供 Working 前台验证入口） | A 不感知 B 内部 Redis / DB / Schema；返回权威 Memory facts，partial / degraded / 无法核验语义明确；Working 当前能力独立于长期投影 |
| B-EPIC-07 | MemorySignal Capability | B → C MemorySignal；Signal Schema / Version；Submitted、补发及消费确认语义 | — | Submitted 不等于已消费；重启后未完成 Signal 可恢复/补发；C 通过契约消费，不理解 B 内部存储 |
| B-EPIC-08 | Recovery / Reconciliation & Retry Governance | 未完成 Task、Projection Pending、内容写 unknown、重启/重复/旧版本恢复；Retry Budget；Redis 半故障；Working × migration 共存和双 Owner 窗口 | B-P0-2、B-P1-1、B-P1-2、B-P1-3（删除级联 / 超时收敛部分） | 重试有预算、指数退避和 jitter；半故障有进入/退出阈值；任务可恢复且不重复主事实；unknown 可查询，旧版本不覆盖；迁移和双 Owner 窗口中 Working P99 与事实正确性可验收 |

### 3.3 人员与工作周期

- **3 人团队，整体周期目标 90 个工作日，功能投入 168 人天。** 每个 Epic 的阶段从 D001 独立计算，分别为 20、22、18、28、24、16、12、28 个有效投入日。
- B1、B2、B3 是 Remember 团队的三个规划执行岗位；Owner B 对整体结果负责。同一岗位承担的 Epic 可以穿插推进，但同一工作日不重复占用。
- 局部周期说明工作量与阶段先后，模块周期包含并行组织及接口衔接；具体开始执行时间取决于人员可用性和前置能力。以下 P 序号表示项目全局工作日，与各 Epic 的 D 投入序号分别计算。

| 执行岗位 | 主要职责 | 负责 Epic | 功能投入（人天） |
| --- | --- | --- | --- |
| B1 | 主事实、生命周期/证据/策略、Signal 交接 | B-EPIC-01、B-EPIC-02、B-EPIC-07 | 54 |
| B2 | 内容、Working 读写、异步加工与后台容量 | B-EPIC-03、B-EPIC-04、B-EPIC-06 | 62 |
| B3 | 投影领域、统一恢复/重试、故障与集成验收 | B-EPIC-05、B-EPIC-08 | 52 |
| 合计 | 3 人 | 8 个 Epic | 168 |

### 3.4 并行开发与交接

| 可并行的工作 | 执行分工 | 前置条件与交接 |
| --- | --- | --- |
| 主事实、内容 Port/Simulator 与投影契约 | B1：主事实/分类；B2：内容；B3：投影 | 先统一输入输出、身份/版本、提交边界和 A/P2 能力契约；按契约样例分别开发，关键契约目标 P015 就绪。 |
| 生命周期/Signal、Task/Working 与投影实现 | B1：领域策略/Signal；B2：Task/Working/压缩；B3：投影 | B2 优先交付内容与 Task/租约骨架；B1/B3 复用能力，不重复建设执行机制；P040 前基础链路可验证。 |
| 压缩、读取交接、恢复与功能闭环 | B1：状态/事件；B2：内容/读写/压缩；B3：恢复/验证 | 按固定样例推进，P065 前形成 Working、压缩、Ready、Signal 和恢复闭环，完成 L1/L2 验证，并具备真实环境联调条件。 |
| 真实联调、迁移/半故障与持续验收 | B1：A/C 语义交接；B2：内容/前台/后台容量；B3：恢复/验收 | P066～P080 完成真实 A/C/P2/Redis 联调及故障演练；P081～P090 完成持续验收、缺陷复核、证据归档与运行交接。 |

### 3.5 跨模块依赖

| 依赖方 | 需要提供的能力 | 主要影响的 Epic |
| --- | --- | --- |
| A / Recall 与共享向量能力 | 真实 Passage Embedding、模型/空间契约、VectorProjectionPort 逐项五态与可查询性证明；消费 MemoryRead 并在发出前复核 | B-EPIC-02、05、06、08 |
| P2 / 内容与向量 Provider | 精确版本的 durable/checksum/head/get、稳定操作查询、容量观察、删除及残留证明；向量机制经 A 对接 | B-EPIC-01、03～05、08 |
| RF / Infra / Redis / P4 / Auth | 可信身份与 Scope、事务/幂等/CAS、Task/Outbox 和执行通道、独立 Working/Prewarm 实例、共享遥测与恢复环境 | B-EPIC-01～08 |
| C / Operate | Signal 幂等持久消费及匹配 Ack、版本/删除失效处理、在途预热与清理范围证据 | B-EPIC-02、07、08 |
| 产品 / 测试 / IS / 验收负责人 | 分类/证据/衰减和压缩质量规则、性能/容量/重试 Profile、迁移故障矩阵、持续验收环境与口径 | B-EPIC-02、04～06、08 |

接口字段、异常处理和接入条件见[负责人填报](AetherStore_P3_Remember_功能填报_V1.0.md)。关键契约、质量及资源 Profile 目标 P015 就绪，真实 A/C/P2 与独立 Redis 环境目标 P065 前就绪；这些是排期条件，实际交付由对应负责人确认。本汇总保留责任、交付、资源和依赖要点，逐接口及逐阶段细节维护在负责人文档。
