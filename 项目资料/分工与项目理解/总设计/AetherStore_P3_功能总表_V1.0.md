# AetherStore P3 · 功能总表 V0.1

更新：2026-09-08。状态：功能规划。收录范围：A（Recall / Context Serving）、B（Remember / Memory Formation）、C（Operate / Optimize）。

**28 个 Epic、28 项功能、9 个执行岗位，功能投入共 456 人天。** 功能、工作量和团队信息集中在第一章，三个模块的职责、人员分工、并行安排及依赖分别列在后续章节。

## 1. 汇总

### 1.1 功能汇总

| Project | Epic ID | Epic Name | Feature ID | Feature Name | User Story 摘要 | 功能描述摘要 | Owner | 开始时间（Epic 内） | 完成时间（Epic 内） | 总人天 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P3 | A-EPIC-01 | Semantic Compute & Resource Isolation | [A-FEAT-01-01](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-01--semantic-compute--resource-isolation) | 共享向量计算、资源隔离与模型发布 | 作为 Remember 模块、Recall Runtime 和模型服务维护者，我希望获得兼容、可追踪且有资源保障的正文/查询向量服务，并能安全升级和回退模型，从而支撑记忆写入与在线检索，控制混合负载及模型变更风险。 | 统一提供 Passage/Query 真实向量生成、模型与检索空间绑定、完成结果复用、资源隔离、性能验证，以及 Shadow/Canary/Rollback 受控发布能力。 | A | D001 | D028 | 28 | 功能规划 |
| P3 | A-EPIC-02 | Vector Projection Mechanism | [A-FEAT-02-01](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-02--vector-projection-mechanism) | 向量投影写入、确认、恢复与删除 | 作为 Remember 投影构建与维护模块，我希望将获准向量写入存储、查询真实完成状态，并在超时、版本更新和删除时安全收敛，从而为领域 Ready 判定提供可信证据，避免重复写入、旧版本覆盖和删除后复活。 | 统一提供 VectorProjectionPort 契约、五元组幂等、ProviderResult 五态处理、写入及操作/目标查询、有界恢复、版本更新与准确目标删除。 | A | D001 | D020 | 20 | 功能规划 |
| P3 | A-EPIC-03 | Vector Backend Simulator | [A-FEAT-03-01](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-03--vector-backend-simulator) | 向量后端模拟与故障验证 | 作为 Recall 与 Remember 开发者，我希望在真实 P2 尚未就绪时验证向量写入、查询和故障恢复契约，从而提前开发并发现接口和状态处理问题。 | 提供有状态 Vector Backend Simulator，支撑向量写读契约与可重现故障验证。 | A | D001 | D007 | 7 | 功能规划 |
| P3 | A-EPIC-04 | Query Runtime & Ingress Protection | [A-FEAT-04-01](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-04--query-runtime--ingress-protection) | 召回查询接入与流量保护 | 作为 Agent 调用方，我希望提交问题、授权范围和预算后获得自动选路、兼容 Query 向量和有界准入的召回服务，从而简化调用并避免突发流量压垮在线链路。 | 完成 Recall 入口、内部来源选择、Query 调用、幂等执行和有界准入。 | A | D001 | D009 | 9 | 功能规划 |
| P3 | A-EPIC-05 | Candidate Retrieval & Search Degradation | [A-FEAT-05-01](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-05--candidate-retrieval--search-degradation) | 候选检索与搜索降级 | 作为 Recall 调用方，我希望按查询条件获得当前 Working 和相关长期记忆候选，并明确获知来源缺失及部分故障，从而正确判断本次检索是否完整。 | 按既定模式读取 Working 和长期候选，统一记录来源覆盖、稳定引用和搜索故障。 | A | D001 | D008 | 8 | 功能规划 |
| P3 | A-EPIC-06 | Candidate Validation & Reference Integrity | [A-FEAT-06-01](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-06--candidate-validation--reference-integrity) | 候选有效性与引用完整性校验 | 作为 Recall 调用方，我希望只有当前版本、权限及状态有效且引用可解析的候选进入后续流程，从而避免过期、已删除、越权或悬空引用导致错误上下文。 | 消费 B 的权威 MemoryRead 事实校验候选，并提供可供最终复核复用的校验适配。 | A | D001 | D008 | 8 | 功能规划 |
| P3 | A-EPIC-07 | Canonical Load | [A-FEAT-07-01](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-07--canonical-load) | 可信原文加载与预热回退 | 作为 Recall 调用方，我希望读取与获准 Memory 版本及范围一致的完整正文，并在可选热副本不可用时回退至正式内容，从而获得可信内容和可定位的加载结果。 | 通过 B 正式映射加载 Working 内联内容或 canonical 原文，校验完整性并控制读取资源。 | A | D001 | D009 | 9 | 功能规划 |
| P3 | A-EPIC-08 | Rank / Decay / Conflict Execution | [A-FEAT-08-01](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-08--rank--decay--conflict-execution) | 排序、衰减与冲突规则执行 | 作为上下文使用方，我希望优先获得相关且可解释的记忆，减少重复内容并完整保留必要冲突，从而避免排序或裁剪掩盖重要证据和分歧。 | 对已可信加载的候选去重、融合来源排名，消费 B 的语义/衰减/冲突策略。 | A | D001 | D006 | 6 | 功能规划 |
| P3 | A-EPIC-09 | Context Assembly | [A-FEAT-09-01](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-09--context-assembly) | 上下文组装、预算控制与可靠交付 | 作为 Agent 调用方，我希望获得预算内、引用可追踪且状态准确的 Context Pack，从而安全使用内容，并区分完整有内容、正常空、降级和失败结果。 | 实现最终复核、ContextPack 装配、四终态判定及可靠提交/结果重放。 | A | D001 | D012 | 12 | 功能规划 |
| P3 | A-EPIC-10 | Recall Observability, SLO, Audit & Acceptance | [A-FEAT-10-01](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md#a-epic-10--recall-observability-slo-audit--acceptance) | 召回可观测性、SLO、审计与验收 | 作为运维人员、Operate 调度模块和验收负责人，我希望查询完整 Recall 访问过程、监控服务指标和故障告警，并凭同一 trace 审计回放关键决策及验证预热使用事实，从而定位错误、评估服务表现并确认交付质量。 | 贯通 Query→Context Pack 的 Trace 与业务访问事件，提供可靠投递、可选预热使用核验、E2E SLO/availability 指标与告警、审计回放及模拟/真实环境分离的验收证据。 | A | D001 | D029 | 29 | 功能规划 |
| P3 | B-EPIC-01 | Ingest & Fact Formation | [B-FEAT-01-01](../remeber流程/AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-01--ingest--fact-formation) | 记忆事件接入与可靠主事实形成 | 作为提交对话、任务结果或显式记忆的上游系统，我希望每次写入都能获得可追踪的事实身份和准确的完成状态，从而在超时重试、服务重启或后台尚未完成时，仍能确认内容是否已可靠保存并继续查询。 | 建立从可信 MemoryEvent 到可靠 MemoryRecord 的接入、幂等、提交和响应闭环，区分主事实已形成、当前 Working 可用与长期派生完成。 | B | D001 | D020 | 20 | 功能规划 |
| P3 | B-EPIC-02 | Lifecycle & Policy Versioning | [B-FEAT-02-01](../remeber流程/AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-02--lifecycle--policy-versioning) | 记忆分类、生命周期与策略版本管理 | 作为需要持续记住当前任务、历史事件和稳定知识的业务系统，我希望记忆能按证据、时间和明确策略分类、巩固、更正或失效，从而让新旧事实、冲突和过期内容具有可解释的处理结果。 | 统一类型、生命周期、证据依赖和版本规则，可靠形成 Semantic，向 A 提供版本化衰减与冲突语义，向后续表示和 Signal 提供资格变化。 | B | D001 | D022 | 22 | 功能规划 |
| P3 | B-EPIC-03 | Durable Content & Capacity Guard | [B-FEAT-03-01](../remeber流程/AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-03--durable-content--capacity-guard) | 可靠正文存储、内容映射与容量保护 | 作为 Remember 和读取可信正文的 Recall，我希望每条 Memory 都能定位到获准、完整、持久的精确内容版本，并在容量不足或写入超时后得到明确结果，从而避免空引用、错版本和错误重写。 | 交付 OBJ-001 消费契约、内容模拟器、不可变内容绑定与真实 Provider 适配，补齐容量保护、完整性核验和写入未知的查询能力。 | B | D001 | D018 | 18 | 功能规划 |
| P3 | B-EPIC-04 | Memoryize / Compression & Background Capacity | [B-FEAT-04-01](../remeber流程/AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-04--memoryize--compression--background-capacity) | 异步记忆加工、压缩与后台容量治理 | 作为需要将长文本和长期记忆可靠加工的业务系统，我希望耗时操作能由可查询、可恢复的后台任务完成，并在保护当前交互的同时生成可信压缩表示，从而兼顾内容质量、存储效率和前台响应。 | 提供 Task/Outbox 到 Worker 的可靠执行骨架、可选压缩及质量门槛、Artifact 发布和后台资源预算，供压缩、投影重建及恢复任务复用。 | B | D001 | D028 | 28 | 功能规划 |
| P3 | B-EPIC-05 | Make Recallable & Projection Health | [B-FEAT-05-01](../remeber流程/AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-05--make-recallable--projection-health) | 长期记忆可召回构建与投影健康管理 | 作为需要检索长期记忆的 Recall，我希望每个可召回标记都对应当前有效正文和完整、可查询的向量集合，从而避免部分构建、错模型或旧版本被当成已经可用的记忆。 | 统一内联/后台投影构建，固定完整输入，消费 A 的机制证据，执行 Ready Guard、条件发布、失效重建及积压告警。 | B | D001 | D024 | 24 | 功能规划 |
| P3 | B-EPIC-06 | MemoryRead Capability | [B-FEAT-06-01](../remeber流程/AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-06--memoryread-capability) | 当前 Working 读写与权威记忆读取能力 | 作为组装当前上下文和长期记忆的 Recall，我希望通过稳定能力获取当前 Working、Memory 资格和合法正文映射，并在发出前重新核对，从而正确处理空结果、不可核验、旧版本及失效内容。 | 交付当前 Working 物化与有界读取、MemoryRead 快照、语义属性与 Canonical 映射交接，并提供初查、发出前和重放前所需复核能力。 | B | D001 | D016 | 16 | 功能规划 |
| P3 | B-EPIC-07 | MemorySignal Capability | [B-FEAT-07-01](../remeber流程/AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-07--memorysignal-capability) | 记忆变化信号、可靠投递与消费确认 | 作为接收记忆变化并决定驻留与调度的 Operate，我希望获得有来源、有版本、可重放的 MemorySignal，并能明确反馈消费结果，从而在重复、乱序和中断时继续处理真实业务变化。 | 实现不可变变化载荷、事实与事件可靠提交、至少一次投递、匹配 C 持久消费 Ack 和有界补发，区分事件、传输与 C 动作状态。 | B | D001 | D012 | 12 | 功能规划 |
| P3 | B-EPIC-08 | Recovery / Reconciliation & Retry Governance | [B-FEAT-08-01](../remeber流程/AetherStore_P3_Remember_功能填报_V1.0.md#b-epic-08--recovery--reconciliation--retry-governance) | 记忆恢复对账、全局重试与故障治理 | 作为维护 Remember 连续服务的运行人员，我希望在进程、存储、网络和迁移发生故障后，能根据真实证据恢复任务、修复派生结果和清理失效表示，并控制重试压力，从而恢复服务且不丢事实、不复活旧数据。 | 建立横切各 Epic 的统一对账扫描、业务重试预算、Redis 半故障准入、精确删除/恢复屏障和故障矩阵，汇总 B 侧真实集成及生产验收证据。 | B | D001 | D028 | 28 | 功能规划 |
| P3 | C-EPIC-01 | Signal / Trace Consumption | [C-FEAT-01](../operate流程/AetherStore_P3_C组功能负责人填报_V0.1.md#c-epic-01--signal--trace-consumption) | Signal / Trace 接入与消费 | 作为调度决策方，我希望可靠、幂等地消费 MemorySignal 和真实 AccessTrace，从而及时感知业务变化，并基于真实访问进行调度计算。 | 接入并归一化 MemorySignal、AccessTrace，完成校验、去重、乱序处理、重放和有效访问归一化；不拥有两类事实的生产权。 | C | D001 | D010 | 10 | 功能规划 |
| P3 | C-EPIC-02 | Representation Hotness | [C-FEAT-02](../operate流程/AetherStore_P3_C组功能负责人填报_V0.1.md#c-epic-02--representation-hotness) | Representation 热度计算 | 作为调度策略方，我希望每个 Representation 都有独立、可解释的热度，从而避免把同一 Memory 的所有表示统一升层或降层。 | 基于有效访问和业务属性计算表示级热度，输出升层、降层或 Keep 的决策输入；热度不改变 MemoryRecord 生命周期。 | C | D001 | D008 | 8 | 功能规划 |
| P3 | C-EPIC-03 | RepresentationPlacementPlan | [C-FEAT-03](../operate流程/AetherStore_P3_C组功能负责人填报_V0.1.md#c-epic-03--representationplacementplan) | PlacementPlan 对象与构建运行时 | 作为调度系统，我希望把观察事实、目标层级、热度和决策依据保存到同一个 PlacementPlan 中，从而让每次调度都能被校验、审计和重新计算。 | 定义 C 侧 PlacementPlan 领域对象和构建运行时，将输入事实转换为可审计计划；Plan 表达期望状态，不表达物理执行结果。 | C | D001 | D018 | 18 | 功能规划 |
| P3 | C-EPIC-04 | Actuation Target Resolve | [C-FEAT-04](../operate流程/AetherStore_P3_C组功能负责人填报_V0.1.md#c-epic-04--actuation-target-resolve) | ActuationTarget 解析与不透明目标消费 | 作为 P3 调度方，我希望根据 Representation、目标层级和 generation 获得稳定的不透明执行目标，从而不理解 P2 内部结构也能提交统一动作。 | 对接 ActuationTarget 解析能力，校验并整体转发 P2 返回的 opaque target；不设计、解析或拼装 Provider-specific target。 | C | D001 | D012 | 12 | 功能规划 |
| P3 | C-EPIC-05 | Scheduling View & Capacity Observation | [C-FEAT-05](../operate流程/AetherStore_P3_C组功能负责人填报_V0.1.md#c-epic-05--scheduling-view--capacity-observation) | 调度视图与容量观测 | 作为调度决策方，我希望读取 P2 提供的真实层级、generation、容量和压力观测，从而区分计划状态与实际状态，并保护在线服务。 | 消费 Placement、Segment 和 ResourceState，保存带来源和新鲜度的观察副本，为决策和容量保护提供可靠输入。 | C | D001 | D014 | 14 | 功能规划 |
| P3 | C-EPIC-06 | Placement Decision, Capacity Guard & Admission Control | [C-FEAT-06](../operate流程/AetherStore_P3_C组功能负责人填报_V0.1.md#c-epic-06--placement-decision-capacity-guard--admission-control) | 放置决策、容量保护与准入 | 作为调度平台负责人，我希望在提交动作前统一检查真实层级、支持动作、容量、并发、冷却和配额，从而避免调度震荡和资源超载。 | 根据 observed tier、hotness、supported_operations 和资源事实计算 desired tier，并执行 Capacity Guard、Cooldown 和 Admission Control；条件不足时输出 Keep / No-op。 | C | D001 | D020 | 20 | 功能规划 |
| P3 | C-EPIC-07 | TierAction Runtime & Operator Control | [C-FEAT-07](../operate流程/AetherStore_P3_C组功能负责人填报_V0.1.md#c-epic-07--tieraction-runtime--operator-control) | TierAction 运行时与人工控制 | 作为调度执行方和运维人员，我希望动作状态、Provider 映射以及暂停、恢复、取消、Pin 和 Force Keep 有统一规则，从而不把已接收误认为已完成，并能在异常时安全接管。 | 管理 C 拥有的 TierAction 状态、幂等和审计，映射 P2 执行状态，并管理人工控制意图；不拥有 P2 的物理执行状态。 | C | D001 | D018 | 18 | 功能规划 |
| P3 | C-EPIC-08 | Storage Control Simulator | [C-FEAT-08](../operate流程/AetherStore_P3_C组功能负责人填报_V0.1.md#c-epic-08--storage-control-simulator) | 统一存储控制模拟器与故障套件 | 作为 C 的开发、测试和验收人员，我希望多个控制能力共享一个可恢复的模拟存储状态，并能重复注入典型故障，从而在真实 P2 尚未就绪时验证调度闭环。 | 建设 SimulatedStorageState 和统一 Control Port 视图，覆盖正常成功、失败、超时、反馈丢失、重启、重复动作和 observed/desired 不一致。 | C | D001 | D020 | 20 | 功能规划 |
| P3 | C-EPIC-09 | Feedback / Reconciliation & Migration Safety | [C-FEAT-09](../operate流程/AetherStore_P3_C组功能负责人填报_V0.1.md#c-epic-09--feedback--reconciliation--migration-safety) | 反馈、对账与迁移安全 | 作为调度闭环负责人，我希望反馈丢失、Placement 过期或 generation 不一致时能够核实真实状态，并按预算安全重试，从而避免错误成功和重复物理动作。 | 建立 ReconciliationTask 和 Query/Retry 运行时，对 TierAction、Plan、Feedback 与 Placement 进行查询、比较和收敛。 | C | D001 | D018 | 18 | 功能规划 |
| P3 | C-EPIC-10 | Optimize / Predict & Controlled Release | [C-FEAT-10](../operate流程/AetherStore_P3_C组功能负责人填报_V0.1.md#c-epic-10--optimize--predict--controlled-release) | 优化、预测与受控发布 | 作为调度优化负责人，我希望预测策略有稳定基线、版本和完整回退链，并能用真实访问和执行结果评估收益，从而控制发布风险。 | 冻结 Heuristic 基线，管理 feature、label、policy、model 版本，接入预测、回退和效果测量，支持 Shadow、Canary、Progressive 和 Rollback。 | C | D001 | D014 | 14 | 功能规划 |

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
| P3 | A | 小计 |  | 10 | 23 | 55 | 33 | 25 | 136 |
| P3 | B | B-EPIC-01 | Ingest & Fact Formation | 1 | 3 | 9 | 5 | 3 | 20 |
| P3 | B | B-EPIC-02 | Lifecycle & Policy Versioning | 1 | 4 | 10 | 5 | 3 | 22 |
| P3 | B | B-EPIC-03 | Durable Content & Capacity Guard | 1 | 3 | 7 | 5 | 3 | 18 |
| P3 | B | B-EPIC-04 | Memoryize / Compression & Background Capacity | 1 | 5 | 12 | 7 | 4 | 28 |
| P3 | B | B-EPIC-05 | Make Recallable & Projection Health | 1 | 4 | 10 | 6 | 4 | 24 |
| P3 | B | B-EPIC-06 | MemoryRead Capability | 1 | 3 | 6 | 4 | 3 | 16 |
| P3 | B | B-EPIC-07 | MemorySignal Capability | 1 | 2 | 5 | 3 | 2 | 12 |
| P3 | B | B-EPIC-08 | Recovery / Reconciliation & Retry Governance | 1 | 5 | 10 | 8 | 5 | 28 |
| P3 | B | 小计 |  | 8 | 29 | 69 | 43 | 27 | 168 |
| P3 | C | C-EPIC-01 | Signal / Trace Consumption | 1 | 2 | 4 | 2 | 2 | 10 |
| P3 | C | C-EPIC-02 | Representation Hotness | 1 | 2 | 3 | 2 | 1 | 8 |
| P3 | C | C-EPIC-03 | RepresentationPlacementPlan | 1 | 5 | 7 | 4 | 2 | 18 |
| P3 | C | C-EPIC-04 | Actuation Target Resolve | 1 | 3 | 4 | 2 | 3 | 12 |
| P3 | C | C-EPIC-05 | Scheduling View & Capacity Observation | 1 | 3 | 5 | 3 | 3 | 14 |
| P3 | C | C-EPIC-06 | Placement Decision, Capacity Guard & Admission Control | 1 | 5 | 7 | 5 | 3 | 20 |
| P3 | C | C-EPIC-07 | TierAction Runtime & Operator Control | 1 | 4 | 6 | 4 | 4 | 18 |
| P3 | C | C-EPIC-08 | Storage Control Simulator | 1 | 3 | 8 | 6 | 3 | 20 |
| P3 | C | C-EPIC-09 | Feedback / Reconciliation & Migration Safety | 1 | 4 | 6 | 5 | 3 | 18 |
| P3 | C | C-EPIC-10 | Optimize / Predict & Controlled Release | 1 | 3 | 5 | 4 | 2 | 14 |
| P3 | C | 小计 |  | 10 | 34 | 55 | 37 | 26 | 152 |
| P3 | A / B / C | 合计 |  | 28 | 86 | 179 | 113 | 78 | 456 |

### 1.3 团队与周期汇总

| Project | Owner | 模块 | 团队人数 | 模块周期（工作日） | Epic 数 | Feature 数 | 功能投入（人天） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P3 | A | Recall / Context Serving | 3 | 90 | 10 | 10 | 136 |
| P3 | B | Remember / Memory Formation | 3 | 90 | 8 | 8 | 168 |
| P3 | C | Operate / Optimize | 3 | 90 | 10 | 10 | 152 |
| P3 | 合计 | 三个模块 | 9 | 90 | 28 | 28 | 456 |

C 组模块周期栏保留空值。合计行不累计模块工期；三个模块的整体交付日期取决于并行关系、接口交接和实际人员分配。

### 1.4 汇总口径

- 每个 Epic 独立从 D001 起算，表中 D 表示本 Epic 的有效投入日。开始时间相同不表示所有功能在项目首日同时开工，各 Epic 的局部周期不能顺次相加作为项目工期。
- A、B 的模块整体周期为 90 个工作日；C 的整体周期本表不填写。B 章节中的 P 序号沿用该组项目全局工作日，与 Epic 内的 D 序号分别计算。
- 功能投入合计为 **456 人天＝设计 86＋开发 179＋测试 113＋联调 78**。A 为 136 人天，B 为 168 人天，C 为 152 人天。
- 三组各配置 3 个执行岗位，合计 9 个岗位；各组人员分工在本组章节中列明。共享人员按实际分配去重，同一人在同一工作日不重复占用；人数乘周期表示容量，不直接作为功能投入。
- 每组以自身汇总文档为数据来源。Remember 文件中的 10 条 Recall 记录只保留一份；C 的 20 个原子子功能由该组负责人文档追踪，本表按其 10 个汇总 Feature 计数。
- Owner 表示业务责任归属，A/B/C 的接口协作不改变领域所有权。P2、RF、网关和其他外部团队列为依赖，其实施工作量不计入本表。
- 三组按统一列结构追加和维护数据，以 Project、Epic ID、Feature ID 标识唯一条目。模拟环境通过与真实接口验收分别记录。

### 1.5 数据来源

| Owner | 模块汇总来源 | 详细填报 |
| --- | --- | --- |
| A | [Recall 汇总](../Recall流程/AetherStore_P3功能汇总表_V0.1.md) | [Recall 负责人填报](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md) |
| B | [Remember 汇总](../remeber流程/AetherStore_P3功能汇总表_含Remember_V0.1.md) | [Remember 负责人填报](../remeber流程/AetherStore_P3_Remember_功能填报_V1.0.md) |
| C | [C 组汇总](../operate流程/AetherStore_P3_C组功能汇总表_V0.1.md) | [C 组负责人填报](../operate流程/AetherStore_P3_C组功能负责人填报_V0.1.md) |

---

## 2. Recall 负责人 — Person A / Context Serving

### 2.1 责任目标与功能范围

**Primary Outcome**：从 Query 到最终 Context Pack 的完整读路径正确、可降级、可追踪；Recall 最终结果错误找 A。

**10 个 Epic、10 项功能，工作量估算 136 人天。** 每个 Epic 对应一个 Feature 和一套填报内容，详细输入输出、流程、验收及分阶段计划见[负责人填报](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md)。

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

接口字段、异常处理和接入条件见[负责人填报](../Recall流程/AetherStore_P3_Recall_功能负责人填报_V0.1.md)及[接口依赖清单](../Recall流程/运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。本汇总保留责任、交付、资源和依赖要点，逐接口及逐阶段细节维护在负责人文档。

---

## 3. Remember 负责人 — Person B / Memory Formation

### 3.1 责任目标与功能范围

**Primary Outcome**：Memory 可靠形成、可追踪、可版本化、可被 Recall 使用；Memory 没形成找 B。

**8 个 Epic、8 项功能，工作量估算 168 人天。** 每个 Epic 对应一个 Feature 和一套填报内容，详细输入输出、流程、验收及分阶段计划见[负责人填报](../remeber流程/AetherStore_P3_Remember_功能填报_V1.0.md)。

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

接口字段、异常处理和接入条件见[负责人填报](../remeber流程/AetherStore_P3_Remember_功能填报_V1.0.md)。关键契约、质量及资源 Profile 目标 P015 就绪，真实 A/C/P2 与独立 Redis 环境目标 P065 前就绪；这些是排期条件，实际交付由对应负责人确认。本汇总保留责任、交付、资源和依赖要点，逐接口及逐阶段细节维护在负责人文档。

---

## 4. C 组负责人 — Person C / Operate / Optimize

### 4.1 责任目标与功能范围

**Primary Outcome**：把 MemorySignal、AccessTrace、真实 Placement 和资源事实转换为调度决策、TierAction、反馈和对账闭环；TierAction 长时间 Unknown 时由 C 负责收敛。

C 负责调度控制面：消费运行事实，按 Representation 计算热度，生成 PlacementPlan，解析并消费不透明 ActuationTarget，执行容量准入，提交 TierAction，接收执行反馈，重新读取真实 Placement，并完成对账和受控优化。

C 不负责：MemoryRecord 主事实和生命周期、AccessTrace/MemorySignal 的生产、P2 的物理存储和物理迁移、Provider-specific target 内部结构。

### 4.2 Epic 结构与责任范围

| Epic ID | 最终 Epic | Epic 范围 | 关联 PRR Action | Epic 级完成标准 |
| --- | --- | --- | --- | --- |
| C-EPIC-01 | Signal / Trace Consumption | 消费 B 的 MemorySignal 和 A/B 的 AccessTrace，完成校验、去重、重放和有效访问归一化。 | — | 重复信号和 Trace 不重复计数或调度；游标可恢复；输入事实可追踪、可重放。 |
| C-EPIC-02 | Representation Hotness | 按 representation_id 计算独立热度，综合有效访问、时间衰减、上下文使用、重要度和资源价值。 | — | 同一 Memory 的不同 Representation 可以得到不同热度；热度可追溯、可重算，且不成为 B 的生命周期状态。 |
| C-EPIC-03 | RepresentationPlacementPlan | 定义 PlacementPlan 领域对象，并将 Signal、AccessTrace、Placement 和资源事实转换为可审计计划。 | — | Plan 字段完整；区分 observed 和 desired；计划可审计、可重算；计划失效时不直接创建 TierAction。 |
| C-EPIC-04 | Actuation Target Resolve | 根据 Representation 和目标层级解析 P2 可执行的不透明 ActuationTarget，并在动作生成时整体消费该目标。 | — | P3 不按 P2 的 provider-specific target_type 分支；目标稳定关联 Representation、Provider 和 generation；目标失效时不提交动作。 |
| C-EPIC-05 | Scheduling View & Capacity Observation | 消费真实 Placement、Segment 观测和资源状态，形成调度所需的观察视图。 | C-P1-3 | C 只消费真实观察，不伪造 current_tier；过期或未知观察会触发安全降级；段级统计不反推对象级事实。 |
| C-EPIC-06 | Placement Decision, Capacity Guard & Admission Control | 计算 desired tier，执行阈值、冷却、容量、带宽、并发、预算和 Scope 配额检查，决定动作是否准入。 | C-P0-1、C-P0-2、C-P1-4 | 容量不足不发动作；线上 P99 恶化能够节流或暂停后台动作；规则有版本、有原因、有界且可解释。 |
| C-EPIC-07 | TierAction Runtime & Operator Control | 管理 TierAction 状态、幂等、Provider 状态映射和人工控制，区分动作已接收与动作已完成。 | C-P0-3、C-P0-4、C-P1-5 | Generated、Submitted、Succeeded、Failed、Unknown 五态完整；Accepted 不等于成功；人工控制有权限、幂等、有效期和审计。 |
| C-EPIC-08 | Storage Control Simulator | 提供共享 SimulatedStorageState，统一模拟 Placement、ActuationTarget、TierAction、Feedback 和 Health 视图，并覆盖 Case 1～6 故障场景。 | — | 正常模式下各控制视图共享同一状态；六类故障可重复注入；模拟器验证状态机、幂等和对账，但不替代真实 P2 验收。 |
| C-EPIC-09 | Feedback / Reconciliation & Migration Safety | 处理反馈丢失、Unknown、stale Placement、generation mismatch、P2 重启和重试恢复，确保动作先核实再收敛。 | C-P0-5、C-P1-1、C-P1-2、C-P1-5 | Unknown 先查询不盲目重试；Action、Plan、Feedback 和 Placement 能对账；重启后不重复动作。 |
| C-EPIC-10 | Optimize / Predict & Controlled Release | 建立启发式基线、特征与策略版本契约，接入预测、回退和效果测量，支持 Shadow、Canary、Progressive 和 Rollback。 | C-P0-6 | 同版本同输入的 Heuristic 决策可复现；预测异常按稳定版本、Heuristic、Keep/No-op 回退；收益和资源影响可审计。 |

### 4.3 人员与工作周期

#### 计划与人天口径

- **每个 Epic 独立从 `D001` 起算。** 设计、开发、测试、联调依次累计本 Epic 的有效投入日，不接续其他 Epic 的时间。
- 例如 C-EPIC-01 的局部周期为 `D001～D010`；C-EPIC-02 重新从 `D001` 起算，到 `D008` 结束；C-EPIC-10 同样从 `D001` 起算，到 `D014` 结束。
- 表中的 D 序号是 Epic 内部的投入序号，用于说明各阶段工作量；不表示所有 Epic 在项目首日同时开工，也不用于将不同 Epic 串行相加。
- **团队配置为 3 人。** 各 Epic 依据接口与人员条件并行实施，局部投入序号与项目全局日期分别表达。
- **C 组功能工作量为 152 人天 = 设计 34 + 开发 55 + 测试 37 + 联调 26。** 每项局部阶段以执行岗位的有效投入核算；跨 Epic 并行缩短总体周期，不改变既有功能工作量。
- Owner C 对 Operate / Optimize 整体交付负责；A、B、P2、Provider 和 Shared Runtime 保留各自领域职责，外部团队实施投入和设备采购不包含在本表。

#### 三人分工与并行关系

C1、C2、C3 为执行岗位代号。各岗位按完整 Epic 归属计算工作量，不重复计算跨组公共工作。

| 执行岗位 | 主要职责 | 负责 Epic | 功能投入（人天） |
| --- | --- | --- | ---: |
| C1 | 输入事实、Representation 热度、PlacementPlan、优化与预测发布 | C-EPIC-01、C-EPIC-02、C-EPIC-03、C-EPIC-10 | 50 |
| C2 | ActuationTarget、Placement/Resource 观察、准入、TierAction 和人工控制 | C-EPIC-04、C-EPIC-05、C-EPIC-06、C-EPIC-07 | 64 |
| C3 | 存储控制模拟器、故障套件、反馈对账和重试恢复 | C-EPIC-08、C-EPIC-09 | 38 |
| 合计 | 3 人 | 10 个 Epic | **152** |

- C1 的输入消费、C2 的 P2 契约和 C3 的模拟器可以并行启动；先统一对象、版本和状态语义。
- C2 在 Placement、ResourceState 和 ActuationTarget 契约稳定后完成真实 P2 联调；C3 用模拟器提前验证动作和故障收敛。
- C1 的预测与受控发布依赖热度、Plan、Action 和反馈数据，相关基础能力就绪后汇合验证。
- 同一岗位承担的 Epic 可以穿插推进，但同一工作日不重复占用；并行缩短总体周期，不改变 152 人天工作量。

### 4.4 并行开发与交接

| 可并行的工作 | 执行分工 | 前置条件与交接 |
| --- | --- | --- |
| Signal/Trace 消费、模拟器和基础对象 | C 同时建立输入消费、统一状态和模拟故障用例 | 先冻结统一输入信封、对象标识、版本和状态语义；模拟器用于前置联调。 |
| 热度、PlacementPlan、Placement/Resource 观察 | C 建立热度和计划逻辑；P2 提供真实观察契约 | 明确 observed 与 desired 分离，观察过期时不得生成新的非 Keep 动作。 |
| ActuationTarget、TierAction 和人工控制 | C 对接目标解析和动作提交；P2 承担物理执行 | C 只保存和转发 opaque target，不解析 Provider-specific 结构；Accepted 不等于完成。 |
| Feedback、对账、重试和预测发布 | C 在基础动作链路上补齐恢复和受控优化 | QueryActionStatus、GetPlacement、版本和审计字段稳定后完成端到端验证。 |

### 4.5 跨模块依赖

| 依赖方 | 需要提供的能力 | 主要影响的 Epic |
| --- | --- | --- |
| B / Remember | MemoryRecord 主事实、版本、生命周期、语义属性和 MemorySignal | C-EPIC-01、C-EPIC-02、C-EPIC-03、C-EPIC-10 |
| A / Recall | 真实 AccessTrace，区分检索命中、加载、上下文选择和实际使用 | C-EPIC-01、C-EPIC-02、C-EPIC-10 |
| P2 / Provider | PlacementObservation、ResourceState、ActuationTarget、ExecutionFeedback 和动作查询 | C-EPIC-04～C-EPIC-09 |
| Shared Runtime | 统一 Schema、可靠投递、补发、重放、消费游标、持久任务和审计能力 | C-EPIC-01、C-EPIC-07、C-EPIC-09 |
| PM / 验收方 | 人员排期、全局日历、接口签收和模拟/真实环境验收边界 | 全部 Epic |
