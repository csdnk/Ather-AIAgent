# AetherStore P2 / P3 · 公司功能汇总与负责人说明 V0.1

更新：2026-09-08。状态：功能规划。当前收录：P3 / A（Recall / Context Serving）。

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

### 1.3 汇总口径

- 开头的功能、工作量、团队与周期三张表用于公司汇总；其他模块按同一列结构追加，使用 Project、Owner、Epic ID、Feature ID 标识归属。
- 功能表的时间采用 **Epic 内独立计时**：每个 Epic 从 D001 起算，只累计本 Epic 的有效投入日，具体阶段见对应负责人填报。它不表示项目日历日期或所有功能同时开工。
- 模块整体周期单列在团队与周期表中。Recall 采用 **3 人、90 个工作日**，按人员和接口依赖并行推进；不同 Epic 的局部周期不能顺次相加作为模块工期。
- 工作量表统计设计、开发、测试和联调的功能投入。Recall 合计 **136 人天**，三人分工为 A1 48、A2 44、A3 44 人天；团队人数乘周期表示可用容量，不直接当成功能工作量。
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
