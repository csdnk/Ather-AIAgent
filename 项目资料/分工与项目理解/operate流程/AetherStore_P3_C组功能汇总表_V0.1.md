# AetherStore P3 · C 组功能汇总与负责人说明 V0.4

更新：2026-09-08。状态：功能规划。当前收录：P3 / C（Operate / Optimize）。

本文按照《AetherStore P3功能汇总表 V0.1》的汇总格式整理 C 组功能。汇总层保留 10 个 Epic 和 10 个 Feature；原有 20 个原子子功能保留在[负责人功能填报](AetherStore_P3_C组功能负责人填报_V0.6.md)中，用于追溯 WBS 和实现范围。

## 1. 汇总

### 1.1 功能汇总

| Project | Epic ID | Epic Name | Feature ID | Feature Name | User Story 摘要 | 功能描述摘要 | Owner | 开始时间（Epic 内） | 完成时间（Epic 内） | 总人天 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| P3 | C-EPIC-01 | Signal / Trace Consumption | [C-FEAT-01](AetherStore_P3_C组功能负责人填报_V0.6.md#c-epic-01--signal--trace-consumption) | Signal / Trace 接入与消费 | 作为调度决策方，我希望可靠、幂等地消费 MemorySignal 和真实 AccessTrace，从而及时感知业务变化，并基于真实访问进行调度计算。 | 接入并归一化 MemorySignal、AccessTrace，完成校验、去重、乱序处理、重放和有效访问归一化；不拥有两类事实的生产权。 | C | D001 | D010 | 10 | 功能规划 |
| P3 | C-EPIC-02 | Representation Hotness | [C-FEAT-02](AetherStore_P3_C组功能负责人填报_V0.6.md#c-epic-02--representation-hotness) | Representation 热度计算 | 作为调度策略方，我希望每个 Representation 都有独立、可解释的热度，从而避免把同一 Memory 的所有表示统一升层或降层。 | 基于有效访问和业务属性计算表示级热度，输出升层、降层或 Keep 的决策输入；热度不改变 MemoryRecord 生命周期。 | C | D001 | D008 | 8 | 功能规划 |
| P3 | C-EPIC-03 | RepresentationPlacementPlan | [C-FEAT-03](AetherStore_P3_C组功能负责人填报_V0.6.md#c-epic-03--representationplacementplan) | PlacementPlan 对象与构建运行时 | 作为调度系统，我希望把观察事实、目标层级、热度和决策依据保存到同一个 PlacementPlan 中，从而让每次调度都能被校验、审计和重新计算。 | 定义 C 侧 PlacementPlan 领域对象和构建运行时，将输入事实转换为可审计计划；Plan 表达期望状态，不表达物理执行结果。 | C | D001 | D018 | 18 | 功能规划 |
| P3 | C-EPIC-04 | Actuation Target Resolve | [C-FEAT-04](AetherStore_P3_C组功能负责人填报_V0.6.md#c-epic-04--actuation-target-resolve) | ActuationTarget 解析与不透明目标消费 | 作为 P3 调度方，我希望根据 Representation、目标层级和 generation 获得稳定的不透明执行目标，从而不理解 P2 内部结构也能提交统一动作。 | 对接 ActuationTarget 解析能力，校验并整体转发 P2 返回的 opaque target；不设计、解析或拼装 Provider-specific target。 | C | D001 | D012 | 12 | 功能规划 |
| P3 | C-EPIC-05 | Scheduling View & Capacity Observation | [C-FEAT-05](AetherStore_P3_C组功能负责人填报_V0.6.md#c-epic-05--scheduling-view--capacity-observation) | 调度视图与容量观测 | 作为调度决策方，我希望读取 P2 提供的真实层级、generation、容量和压力观测，从而区分计划状态与实际状态，并保护在线服务。 | 消费 Placement、Segment 和 ResourceState，保存带来源和新鲜度的观察副本，为决策和容量保护提供可靠输入。 | C | D001 | D014 | 14 | 功能规划 |
| P3 | C-EPIC-06 | Placement Decision, Capacity Guard & Admission Control | [C-FEAT-06](AetherStore_P3_C组功能负责人填报_V0.6.md#c-epic-06--placement-decision-capacity-guard--admission-control) | 放置决策、容量保护与准入 | 作为调度平台负责人，我希望在提交动作前统一检查真实层级、支持动作、容量、并发、冷却和配额，从而避免调度震荡和资源超载。 | 根据 observed tier、hotness、supported_operations 和资源事实计算 desired tier，并执行 Capacity Guard、Cooldown 和 Admission Control；条件不足时输出 Keep / No-op。 | C | D001 | D020 | 20 | 功能规划 |
| P3 | C-EPIC-07 | TierAction Runtime & Operator Control | [C-FEAT-07](AetherStore_P3_C组功能负责人填报_V0.6.md#c-epic-07--tieraction-runtime--operator-control) | TierAction 运行时与人工控制 | 作为调度执行方和运维人员，我希望动作状态、Provider 映射以及暂停、恢复、取消、Pin 和 Force Keep 有统一规则，从而不把已接收误认为已完成，并能在异常时安全接管。 | 管理 C 拥有的 TierAction 状态、幂等和审计，映射 P2 执行状态，并管理人工控制意图；不拥有 P2 的物理执行状态。 | C | D001 | D018 | 18 | 功能规划 |
| P3 | C-EPIC-08 | Storage Control Simulator | [C-FEAT-08](AetherStore_P3_C组功能负责人填报_V0.6.md#c-epic-08--storage-control-simulator) | 统一存储控制模拟器与故障套件 | 作为 C 的开发、测试和验收人员，我希望多个控制能力共享一个可恢复的模拟存储状态，并能重复注入典型故障，从而在真实 P2 尚未就绪时验证调度闭环。 | 建设 SimulatedStorageState 和统一 Control Port 视图，覆盖正常成功、失败、超时、反馈丢失、重启、重复动作和 observed/desired 不一致。 | C | D001 | D020 | 20 | 功能规划 |
| P3 | C-EPIC-09 | Feedback / Reconciliation & Migration Safety | [C-FEAT-09](AetherStore_P3_C组功能负责人填报_V0.6.md#c-epic-09--feedback--reconciliation--migration-safety) | 反馈、对账与迁移安全 | 作为调度闭环负责人，我希望反馈丢失、Placement 过期或 generation 不一致时能够核实真实状态，并按预算安全重试，从而避免错误成功和重复物理动作。 | 建立 ReconciliationTask 和 Query/Retry 运行时，对 TierAction、Plan、Feedback 与 Placement 进行查询、比较和收敛。 | C | D001 | D018 | 18 | 功能规划 |
| P3 | C-EPIC-10 | Optimize / Predict & Controlled Release | [C-FEAT-10](AetherStore_P3_C组功能负责人填报_V0.6.md#c-epic-10--optimize--predict--controlled-release) | 优化、预测与受控发布 | 作为调度优化负责人，我希望预测策略有稳定基线、版本和完整回退链，并能用真实访问和执行结果评估收益，从而控制发布风险。 | 冻结 Heuristic 基线，管理 feature、label、policy、model 版本，接入预测、回退和效果测量，支持 Shadow、Canary、Progressive 和 Rollback。 | C | D001 | D014 | 14 | 功能规划 |

### 1.2 工作量汇总

| Project | Owner | Epic ID | Epic Name | Feature 数 | 设计 | 开发 | 测试 | 联调 | 总人天 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
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
| P3 | C | **小计** |  | **10** | **34** | **55** | **37** | **26** | **152** |

### 1.3 汇总口径

- C 组按 10 个 Epic 汇总为 10 个公司可阅读的 Feature；每个 Epic 下仍保留原子子功能和 WBS 映射。
- **每个 Epic 独立从 `D001` 起算。** 设计、开发、测试、联调依次累计本 Epic 的有效投入日，不接续其他 Epic 的时间。
- 表中的 D 序号是 Epic 内部的投入序号，不表示所有 Epic 在项目首日同时开工，也不用于将不同 Epic 串行相加。
- **团队配置为 3 人，项目周期目标待 PM 确认。** 各 Epic 依据接口与人员条件并行实施，局部投入序号与项目全局日期分别表达。
- **C 组功能工作量为 152 人天 = 设计 34 + 开发 55 + 测试 37 + 联调 26。** 每项局部阶段以执行岗位的有效投入核算；跨 Epic 并行缩短总体周期，不改变既有功能工作量。
- Owner C 对 Operate / Optimize 整体交付负责；A、B、P2、Provider 和 Shared Runtime 保留各自领域职责，外部团队实施投入和设备采购不包含在本表。
- 统一输入、Plan、Action、状态和模拟器能力只计算一次；其他 Feature 只计算自身增量工作。

## 2. C 组负责人 — Person C / Operate / Optimize

### 2.1 责任目标与功能范围

**Primary Outcome**：把 MemorySignal、AccessTrace、真实 Placement 和资源事实转换为调度决策、TierAction、反馈和对账闭环；TierAction 长时间 Unknown 时由 C 负责收敛。

C 负责调度控制面：消费运行事实，按 Representation 计算热度，生成 PlacementPlan，解析并消费不透明 ActuationTarget，执行容量准入，提交 TierAction，接收执行反馈，重新读取真实 Placement，并完成对账和受控优化。

C 不负责：MemoryRecord 主事实和生命周期、AccessTrace/MemorySignal 的生产、P2 的物理存储和物理迁移、Provider-specific target 内部结构。

### 2.2 Epic 结构与责任范围

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

### 2.3 人员与工作周期

#### 计划与人天口径

- **每个 Epic 独立从 `D001` 起算。** 设计、开发、测试、联调依次累计本 Epic 的有效投入日，不接续其他 Epic 的时间。
- 例如 C-EPIC-01 的局部周期为 `D001～D010`；C-EPIC-02 重新从 `D001` 起算，到 `D008` 结束；C-EPIC-10 同样从 `D001` 起算，到 `D014` 结束。
- 表中的 D 序号是 Epic 内部的投入序号，用于说明各阶段工作量；不表示所有 Epic 在项目首日同时开工，也不用于将不同 Epic 串行相加。
- **团队配置为 3 人，项目周期目标待 PM 确认。** 各 Epic 依据接口与人员条件并行实施，局部投入序号与项目全局日期分别表达。
- **C 组功能工作量为 152 人天 = 设计 34 + 开发 55 + 测试 37 + 联调 26。** 每项局部阶段以执行岗位的有效投入核算；跨 Epic 并行缩短总体周期，不改变既有功能工作量。
- Owner C 对 Operate / Optimize 整体交付负责；A、B、P2、Provider 和 Shared Runtime 保留各自领域职责，外部团队实施投入和设备采购不包含在本表。

#### 三人分工与并行关系

以下 C1、C2、C3 为执行岗位代号，具体人员姓名由 PM 补充。各岗位按完整 Epic 归属计算工作量，不重复计算跨组公共工作。

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

### 2.4 并行开发与交接

| 可并行的工作 | 执行分工 | 前置条件与交接 |
| --- | --- | --- |
| Signal/Trace 消费、模拟器和基础对象 | C 同时建立输入消费、统一状态和模拟故障用例 | 先冻结统一输入信封、对象标识、版本和状态语义；模拟器用于前置联调。 |
| 热度、PlacementPlan、Placement/Resource 观察 | C 建立热度和计划逻辑；P2 提供真实观察契约 | 明确 observed 与 desired 分离，观察过期时不得生成新的非 Keep 动作。 |
| ActuationTarget、TierAction 和人工控制 | C 对接目标解析和动作提交；P2 承担物理执行 | C 只保存和转发 opaque target，不解析 Provider-specific 结构；Accepted 不等于完成。 |
| Feedback、对账、重试和预测发布 | C 在基础动作链路上补齐恢复和受控优化 | QueryActionStatus、GetPlacement、版本和审计字段稳定后完成端到端验证。 |

### 2.5 跨模块依赖

| 依赖方 | 需要提供的能力 | 主要影响的 Epic |
| --- | --- | --- |
| B / Remember | MemoryRecord 主事实、版本、生命周期、语义属性和 MemorySignal | C-EPIC-01、C-EPIC-02、C-EPIC-03、C-EPIC-10 |
| A / Recall | 真实 AccessTrace，区分检索命中、加载、上下文选择和实际使用 | C-EPIC-01、C-EPIC-02、C-EPIC-10 |
| P2 / Provider | PlacementObservation、ResourceState、ActuationTarget、ExecutionFeedback 和动作查询 | C-EPIC-04～C-EPIC-09 |
| Shared Runtime | 统一 Schema、可靠投递、补发、重放、消费游标、持久任务和审计能力 | C-EPIC-01、C-EPIC-07、C-EPIC-09 |
| PM / 验收方 | 人员排期、全局日历、接口签收和模拟/真实环境验收边界 | 全部 Epic |
