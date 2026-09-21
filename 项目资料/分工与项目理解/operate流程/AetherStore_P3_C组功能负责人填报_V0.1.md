# AetherStore P3 · Operate / Optimize 负责人功能填报 V0.6

## Person C — Operate / Optimize

**Primary Outcome**：把 MemorySignal、AccessTrace、真实 Placement 和资源事实转换为调度决策、TierAction、反馈和对账闭环；TierAction 长时间 Unknown 时由 C 负责收敛。

本版参照 A 组填报文档的呈现方式，按 `C-EPIC-01`～`C-EPIC-10` 组织为 **10 项汇总功能**，每个 Epic 对应一个汇总 Feature。原有 20 个原子子功能保留在各 Epic 的“子功能映射”中，用于追溯 WBS 和实现范围。

### 编号规则

- `C-FEAT-01`～`C-FEAT-10`：公司汇报口径的汇总 Feature，每个 Epic 对应一个。
- `C-SUB-01`～`C-SUB-20`：Epic 下的原子子功能，对应原有 WBS 和实现范围。
- 下方“基本信息”的 `Feature ID` 使用汇总 Feature 编号；“子功能映射”使用子功能编号。

## C 的最终 Epic 结构

| Epic ID | 最终 Epic | Feature ID | Feature Name | 子功能编号 | 估算人天 | Epic 内独立计划 |
|---|---|---|---|---|---:|---|
| C-EPIC-01 | Signal / Trace Consumption | C-FEAT-01 | Signal / Trace 接入与消费 | C-SUB-01、C-SUB-02 | 10 | D001～D010 |
| C-EPIC-02 | Representation Hotness | C-FEAT-02 | Representation 热度计算 | C-SUB-03 | 8 | D001～D008 |
| C-EPIC-03 | RepresentationPlacementPlan | C-FEAT-03 | PlacementPlan 对象与构建运行时 | C-SUB-04、C-SUB-05 | 18 | D001～D018 |
| C-EPIC-04 | Actuation Target Resolve | C-FEAT-04 | ActuationTarget 解析与不透明目标消费 | C-SUB-06、C-SUB-07 | 12 | D001～D012 |
| C-EPIC-05 | Scheduling View & Capacity Observation | C-FEAT-05 | 调度视图与容量观测 | C-SUB-08、C-SUB-09 | 14 | D001～D014 |
| C-EPIC-06 | Placement Decision, Capacity Guard & Admission Control | C-FEAT-06 | 放置决策、容量保护与准入 | C-SUB-10、C-SUB-11 | 20 | D001～D020 |
| C-EPIC-07 | TierAction Runtime & Operator Control | C-FEAT-07 | TierAction 运行时与人工控制 | C-SUB-12、C-SUB-13 | 18 | D001～D018 |
| C-EPIC-08 | Storage Control Simulator | C-FEAT-08 | 统一存储控制模拟器与故障套件 | C-SUB-14、C-SUB-15 | 20 | D001～D020 |
| C-EPIC-09 | Feedback / Reconciliation & Migration Safety | C-FEAT-09 | 反馈、对账与迁移安全 | C-SUB-16、C-SUB-17 | 18 | D001～D018 |
| C-EPIC-10 | Optimize / Predict & Controlled Release | C-FEAT-10 | 优化、预测与受控发布 | C-SUB-18、C-SUB-19、C-SUB-20 | 14 | D001～D014 |
| **合计** |  |  |  |  | **152** | — |

## 计划与人天口径

- **每个 Epic 独立从 `D001` 起算。** 设计、开发、测试、联调依次累计本 Epic 的有效投入日，不接续其他 Epic 的时间。
- 例如 C-EPIC-01 的局部周期为 `D001～D010`；C-EPIC-02 重新从 `D001` 起算，到 `D008` 结束；C-EPIC-10 同样从 `D001` 起算，到 `D014` 结束。
- 本文的 D 序号是 Epic 内部的投入序号，用于说明各阶段工作量；不表示所有 Epic 在项目首日同时开工，也不用于将不同 Epic 串行相加。
- **团队配置为 3 人，项目周期目标待 PM 确认。** 各 Epic 依据接口与人员条件并行实施，局部投入序号与项目全局日期分别表达。
- **C 组功能工作量为 152 人天 = 设计 34 + 开发 55 + 测试 37 + 联调 26。** 每项局部阶段以执行岗位的有效投入核算；跨 Epic 并行缩短总体周期，不改变既有功能工作量。
- Owner C 对 Operate / Optimize 整体交付负责；A、B、P2、Provider 和 Shared Runtime 保留各自领域职责，外部团队实施投入和设备采购不包含在本表。
- 统一对象、状态、动作和模拟器能力只计算一次；其他 Feature 只计算自身的增量工作。

### 三人分工与并行关系

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

### 公共工作归属

| 公共工作 | 计入位置 | 处理原则 |
|---|---|---|
| C 组统一输入、Plan、Action 和对账边界 | C-FEAT-01、C-FEAT-03、C-FEAT-07、C-FEAT-09 | 公共语义只设计一次，各子功能只实现自身适配 |
| 共享 `SimulatedStorageState` 和控制 Port 视图 | C-FEAT-08 | 模拟器框架只开发一次，故障用例复用同一状态 |
| 各接口合同验证和 P2 联调 | 对应 Epic | 不同接口的实际验证仍分别计算，不能全部合并成一次联调 |

### C 组阶段人天汇总

| 阶段 | 人天 | 说明 |
|---|---:|---|
| 设计 | 34 | 公共对象/状态设计和各 Epic 的增量规则设计 |
| 开发 | 55 | 公共运行能力一次实现和各 Epic 的具体逻辑 |
| 测试 | 37 | 公共测试能力和各 Epic 的业务、故障用例 |
| 联调 | 26 | A/B/P2 接口验证、故障联调和端到端验证 |
| **合计** | **152** | 与本次复评后的 C 组规划总人天一致 |

### 本次人天复评原则

| 复评原则 | 本次处理 |
|---|---|
| 共享能力只计算一次 | 统一输入信封、状态语义、审计和消费约定不在多个 Epic 重复计入。 |
| C 只实现控制面 | P2 / Provider 的物理存储、Copy、Verify、Cutover、Reclaim 和底层迁移不计入 C。 |
| 模拟器按共用底座估算 | 一个 `SimulatedStorageState` 支撑全部控制能力，Case 1～6 作为测试场景，不按每个动作重复建设。 |
| MVP 预测范围收敛 | 只实现 Heuristic 基线、版本、回退和测量，不包含模型训练和复杂预测算法研发。 |
| 联调按真实边界计入 | 只保留 C 与 A/B、P2、Shared Runtime 的必要契约验证和端到端联调。 |

复评后保留全部 10 个 Epic 和 20 个原子子功能，调整的是重复建设和超出 MVP 的估算，不是删除 C 的职责范围。

---

## C-EPIC-01 · Signal / Trace Consumption

**Epic 范围**：消费 B 的 `MemorySignal` 和 A/B 的 `AccessTrace`，完成校验、去重、重放和有效访问归一化。

**关联 PRR Action**：—。

**Epic 级完成标准**：重复信号和 Trace 不重复计数或重复调度；消费游标可恢复；Submitted 不被误认为已完成消费；输入事实可追踪、可重放。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-01 | MemorySignal 消费 |
| C-SUB-02 | AccessTrace 消费 |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-01 |
| Epic Name | Signal / Trace Consumption |
| Feature ID | C-FEAT-01 |
| Feature Name | Signal / Trace 接入与消费 |
| Owner | C（Operate / Optimize） |
| Contributor | B（MemorySignal 生产）；A/B（AccessTrace 生产）；Shared Runtime（投递与 Schema） |
| 目标里程碑 | MVP |

### 2. User Story

作为调度决策方，我希望可靠、幂等地消费 MemorySignal 和真实 AccessTrace，从而及时感知业务变化，并基于真实访问而不是搜索命中进行热度和调度计算。

### 3. 功能描述

#### 3.1 功能目标

接入并归一化 C 的两类输入事实：MemorySignal 用于感知记忆版本、生命周期或表示资格变化；AccessTrace 用于记录检索、加载、上下文选择和实际使用。C 不拥有这两类事实的生产权。

#### 3.2 核心输入

```text
event_id
event_type
schema_version
source
memory_id
memory_version
representation_id
signal_version
event_stage
source_provider
occurred_at
observed_at
scope
trace_id
idempotency_key
payload_summary
```

#### 3.3 核心处理

1. 校验来源、Schema、对象标识、版本、Scope 和时间。
2. 按 `event_id` 或幂等键记录消费结果，重复事件不重复生成动作或统计。
3. 按来源版本处理乱序 Signal，旧版本不得覆盖新输入。
4. 按事件阶段归一化 AccessTrace，只把正文加载、上下文选中和实际使用计入有效访问。
5. 保存各来源消费游标，支持补发、重放和服务重启恢复。
6. 将有效变化和访问事实提供给 Representation Hotness，不直接命令 P2 迁移。

#### 3.4 核心输出

```text
signal_consumption_record
normalized_access_trace
consumption_status
last_consumed_version
aggregation_window
effective_access
replay_cursor
plan_recompute_trigger
trace_id
```

### 4. 正常流程

```text
接收 MemorySignal / AccessTrace
↓
校验事件身份、版本和时间
↓
登记幂等消费记录
↓
按事件阶段归一化访问事实
↓
保存游标和统计窗口
↓
触发表示级热度和 Plan 重算
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| 重复 Signal 或 Trace | 按事件身份去重 | 不重复生成动作或访问统计 |
| 缺少稳定对象标识、版本或时间 | 拒绝进入调度计算并记录原因 | 不产生新的 TierAction |
| Trace Schema 版本漂移 | 拒绝不兼容记录并告警 | 不使用不确定数据驱动调度 |
| 投递或消费暂时不可用 | 保留游标和待处理记录，等待补发 | 已提交事实不被改写 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| MemorySignal | 记忆版本、生命周期和表示资格变化通知 | B |
| AccessTrace | 真实访问阶段事实 | A/B |
| Delivery / Replay | 补发、重放和消费游标 | Shared Runtime |
| Hotness Runtime | 消费归一化后的输入 | C |

### 7. 验收标准

1. 相同 `event_id` 或幂等键重复投递时只产生一次有效消费结果。
2. Search Hit 不会被单独计为实际使用。
3. 缺少对象标识、版本或时间的事件不会驱动物理调度。
4. 旧版本 Signal 不会覆盖 C 已保存的新版本输入。
5. 消费游标能够保存、恢复和重放。
6. MemorySignal 消费完成不会被错误标记为 TierAction 成功。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
|---|---|---|---|
| 设计 | D001 | D002 | 统一输入信封、去重、重放和访问计数口径 |
| 开发 | D003 | D006 | Signal/Trace 消费、游标和归一化实现 |
| 测试 | D007 | D008 | 重复、乱序、版本漂移和补发测试 |
| 联调 | D009 | D010 | 与 A/B 生产方和 Shared Runtime 联调 |

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 2 |
| 开发 | 4 |
| 测试 | 2 |
| 联调 | 2 |
| **总人天** | **10** |

### 10. 备注

```text
- 原子子功能：C-SUB-01、C-SUB-02；WBS：C-EPIC-01-T1、C-EPIC-01-T2。
- AccessTrace 的 Schema/Ingest 由 Shared Runtime 负责，C 负责消费和业务归一化。
- 该 Feature 的设计人天包含 C 组统一输入消费约定，其他 Epic 不重复计算这部分公共设计。
```

---

## C-EPIC-02 · Representation Hotness

**Epic 范围**：按 `representation_id` 计算独立热度，综合有效访问、时间衰减、上下文使用、重要度和资源价值。

**关联 PRR Action**：—。

**Epic 级完成标准**：同一 Memory 的不同 representation 可以得到不同热度；热度可追溯、可重算；热度不是 B 的生命周期状态。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-03 | Per-Representation Hotness |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-02 |
| Epic Name | Representation Hotness |
| Feature ID | C-FEAT-02 |
| Feature Name | Representation 热度计算 |
| Owner | C（Operate / Optimize） |
| Contributor | B（重要度等语义属性）；A/B（访问事实） |
| 目标里程碑 | MVP |

### 2. User Story

作为调度策略方，我希望每个 representation 都有独立、可解释的热度，从而避免把同一 Memory 的所有表示统一升层或降层。

### 3. 功能描述

#### 3.1 功能目标

基于有效访问和业务属性计算表示级 `hotness_score`，输出升层、降层或 Keep 的决策输入。热度是 C 的派生事实，不改变 MemoryRecord 的生命周期状态。

#### 3.2 核心输入

```text
memory_id
representation_id
effective_access_count
access_stability
temporal_decay
context_use
importance
resource_value
observation_window
policy_version
```

#### 3.3 核心处理

1. 按 `representation_id` 聚合有效访问和上下文使用。
2. 对频次、稳定性、时间衰减和业务属性进行归一化。
3. 按当前 `policy_version` 计算 `hotness_score`。
4. 保存统计窗口、输入指纹、分量和计算时间。
5. 根据连续窗口和阈值输出目标方向；输入不足时输出 Keep / No-op。

#### 3.4 核心输出

```text
representation_id
hotness_score
score_components
observation_window
policy_version
input_fingerprint
recommended_direction
decision_reason
calculated_at
```

### 4. 正常流程

```text
读取有效 AccessTrace 和业务属性
↓
按 representation 聚合统计窗口
↓
计算频次、衰减、使用和资源价值
↓
生成 hotness_score
↓
应用阈值和连续窗口
↓
输出给 PlacementPlan
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| 只有 Memory 级热度 | 不强行复制到所有 representation | 保守 Keep / No-op |
| 访问窗口不完整 | 标记输入不完整并降低决策可信度 | 不发起激进调度 |
| 策略版本缺失 | 使用上一稳定策略或 Keep / No-op | 不产生无版本决策 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| AccessTrace | 实际访问和使用统计 | A/B、C-EPIC-01 |
| MemoryRecord 语义属性 | importance、confidence 等业务输入 | B |
| PolicyContext | 权重、阈值、窗口和版本 | C |

### 7. 验收标准

1. 同一 Memory 的不同 representation 可以得到不同热度。
2. 搜索命中不会单独被当作有效使用。
3. 热度结果能够追溯到统计窗口、输入指纹和 `policy_version`。
4. 输入不足时自动降级为 Keep / No-op 或保守候选。
5. 相同版本、相同输入得到可复现的热度结果。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
|---|---|---|---|
| 设计 | D001 | D002 | 热度分量、权重、阈值和窗口规则 |
| 开发 | D003 | D005 | 表示级热度计算和结果保存 |
| 测试 | D006 | D007 | 多表示、衰减、阈值和边界测试 |
| 联调 | D008 | D008 | 与 AccessTrace、B 语义属性联调 |

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 2 |
| 开发 | 3 |
| 测试 | 2 |
| 联调 | 1 |
| **总人天** | **8** |

### 10. 备注

```text
- 原子子功能：C-SUB-03；WBS：C-EPIC-02-T1。
- hotness_score 是 C 的派生决策输入，不是 MemoryRecord 的业务状态。
- 权重、阈值或窗口变化必须产生新的 policy_version。
```

---

## C-EPIC-03 · RepresentationPlacementPlan

**Epic 范围**：定义 `RepresentationPlacementPlan` 领域对象，并将 Signal、AccessTrace、Placement 和资源事实转换为可审计计划。

**关联 PRR Action**：—。

**Epic 级完成标准**：Plan 字段完整；能区分 observed 和 desired；计划可审计、可重算；计划失效时不直接创建 TierAction。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-04 | Placement Plan Domain Object |
| C-SUB-05 | Plan Build Runtime |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-03 |
| Epic Name | RepresentationPlacementPlan |
| Feature ID | C-FEAT-03 |
| Feature Name | PlacementPlan 对象与构建运行时 |
| Owner | C（Operate / Optimize） |
| Contributor | B（Memory/Representation 关联）；P2/Provider（观察事实）；C 组热度模块 |
| 目标里程碑 | MVP |

### 2. User Story

作为调度系统，我希望把观察事实、目标层级、热度和决策依据保存到同一个 PlacementPlan 中，从而让每次调度都能被校验、审计和重新计算。

### 3. 功能描述

#### 3.1 功能目标

冻结 C 侧 Plan 领域对象及其构建运行时。Plan 表达期望状态，不表达物理执行结果，也不修改 P2 的真实 current_tier。

#### 3.2 核心输入

```text
memory_id
representation_id
representation_type
provider_ref
observed_tier
current_generation
hotness_score
decision_reason
policy_version
input_fingerprint
manual_override
generated_at
valid_until
MemorySignal
AccessTrace
ResourceState
```

#### 3.3 核心处理

1. 校验表示对象、Provider 引用、观察层级和 generation 的关联。
2. 合并有效 Signal、访问事实、Placement 观察和资源输入。
3. 生成 `observed_tier`、`desired_tier`、`target_generation` 和有效期。
4. 记录热度、策略版本、输入指纹、原因和人工控制影响。
5. 输入不足、资源过期或存在冲突时输出 Keep / No-op，不伪造目标。
6. 新事实改变目标时创建新 Plan，旧 Plan 保留用于审计。

#### 3.4 核心输出

```text
plan_id
memory_id
representation_id
entries
observed_tier
desired_tier
target_generation
decision_reason
policy_version
input_fingerprint
generated_at
valid_until
supersedes_plan_id
keep_noop_decision
```

### 4. 正常流程

```text
接收热度和真实观察输入
↓
校验对象、版本、新鲜度和控制条件
↓
生成 observed_tier 与 desired_tier
↓
记录原因、策略、输入指纹和目标代际
↓
保存新的 PlacementPlan
↓
交给前置检查和 ActuationTarget 解析
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| Plan 字段缺失 | 拒绝保存或标记不可执行 | 不生成 TierAction |
| generation 与最新观察不一致 | 使当前 Plan 失效并重新读取 | 不使用旧 Plan 调度 |
| 目标层级与当前层级一致 | 记录 Keep / No-op | 不创建物理动作 |
| 关键输入 stale/unknown | 保存降级原因，等待下一轮 | 不提交新的非 Keep 动作 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| Representation Hotness | 表示级热度和决策方向 | C-EPIC-02 |
| PlacementObservation | 当前真实层级、generation 和可读性 | P2 / Provider |
| ResourceState | 容量、压力和迁移资源事实 | P2 / Provider |
| MemorySignal / AccessTrace | 业务变化和实际访问输入 | B、A/B、C-EPIC-01 |

### 7. 验收标准

1. Plan 至少包含表示标识、observed_tier、desired_tier、target_generation、policy_version 和有效期。
2. Plan 能明确区分目标计划与真实物理观察。
3. Plan 失效时不会直接创建 TierAction。
4. 新 Plan 不覆盖旧 Plan，旧 Plan 可用于审计和解释。
5. 相同版本和相同输入能够重算出一致的 Plan。
6. 缺少关键输入时输出 Keep / No-op，不伪造 Placement。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
|---|---|---|---|
| 设计 | D001 | D005 | Plan 字段、有效性、输入合并和重算规则 |
| 开发 | D006 | D012 | Plan 持久化、校验和构建运行时 |
| 测试 | D013 | D016 | 字段完整性、缺失输入、冲突和重算测试 |
| 联调 | D017 | D018 | 与 A/B 输入、P2 Placement 和 ResourceState 联调 |

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 5 |
| 开发 | 7 |
| 测试 | 4 |
| 联调 | 2 |
| **总人天** | **18** |

### 10. 备注

```text
- 原子子功能：C-SUB-04、C-SUB-05；WBS：C-EPIC-03-T1、C-EPIC-03-T2。
- Plan 是 C 的 Desired State，不是 P2 的 current_tier，也不是物理迁移任务。
- 该 Feature 的设计人天包含 C 组统一 Plan 对象边界，其他 Epic 不重复设计同一套 Plan 语义。
```

---

## C-EPIC-04 · Actuation Target Resolve

**Epic 范围**：根据 representation 和目标层级解析 P2 可执行的不透明 `ActuationTarget`，并在动作生成时整体消费该目标。

**关联 PRR Action**：—。

**Epic 级完成标准**：P3 不按 P2 的 provider-specific `target_type` 分支；目标能稳定关联到 representation、Provider 和 generation；目标失效时不提交动作。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-06 | ActuationTarget Resolve Contract |
| C-SUB-07 | Opaque Target Consumption |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-04 |
| Epic Name | Actuation Target Resolve |
| Feature ID | C-FEAT-04 |
| Feature Name | ActuationTarget 解析与不透明目标消费 |
| Owner | C（Operate / Optimize） |
| Contributor | P2 / Provider（目标解析）；Shared Runtime（契约治理） |
| 目标里程碑 | MVP |

### 2. User Story

作为 P3 调度方，我希望根据 representation、目标层级和 generation 获得稳定的不透明执行目标，从而不理解 P2 内部 Segment、Object 或物理路径也能提交统一动作。

### 3. 功能描述

#### 3.1 功能目标

实现 `ActuationTargetResolvePort` 及其消费规则。C 负责逻辑对象到 Provider 引用的关联和契约校验，但不设计或解析 P2 的内部目标结构。

#### 3.2 核心输入

```text
representation_id
desired_tier
target_generation
plan_id
valid_until
trace_id
request_id
action_type
```

#### 3.3 核心处理

1. 校验 representation、Plan、目标层级和 generation 的一致性。
2. 请求 P2/Provider 解析 opaque ActuationTarget。
3. 保存 target_id、provider_ref、支持动作、目标代际、route_epoch 和有效期。
4. 检查目标是否仍在计划有效期内，且支持当前动作。
5. 将目标作为整体写入 TierAction，不解析、拼装或按内部类型分支。

#### 3.4 核心输出

```text
target_id
representation_id
provider_ref
provider_name
source_tier
supported_operations
target_generation
route_epoch
resolved_at
valid_until
target_resolution_status
target_validation_result
```

### 4. 正常流程

```text
读取有效 PlacementPlan
↓
提交 representation、目标层级和 generation
↓
P2 / Provider 返回 opaque ActuationTarget
↓
校验目标关联、支持动作和有效期
↓
保存并整体转发目标
↓
进入 TierAction 生成流程
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| 目标无法解析 | 记录 unresolvable 原因 | 不创建 TierAction |
| representation、Provider 或 generation 不一致 | 拒绝结果并要求重新读取 | 当前 Plan 不可执行 |
| 目标已过期或不支持动作 | 重新解析或记录 Keep / No-op | 不提交旧目标 |
| 代码尝试按 target_type 分支 | 阻止提交并记录架构违规 | 不产生物理动作 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| PlacementPlan | 提供目标层级和目标代际 | C-EPIC-03 |
| ActuationTargetResolvePort | 抽象表示到执行目标解析 | P2 / Provider |
| ACT-001 | 目标解析契约和错误语义 | P2 / Integration |
| TierAction Runtime | 保存和提交统一动作 | C-EPIC-07 |

### 7. 验收标准

1. C 能以 representation、desired_tier 和 target_generation 请求目标解析。
2. 解析结果至少能关联 target_id、provider_ref、支持动作、目标代际和有效期。
3. C 的业务逻辑不按 P2 的 provider-specific `target_type` 编写分支。
4. 目标失效、对象不存在或版本冲突时不会生成 TierAction。
5. opaque target 能完整关联到 Plan 和 representation，并可供后续查询、反馈和对账使用。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
|---|---|---|---|
| 设计 | D001 | D003 | 目标解析输入输出、有效期和失效规则 |
| 开发 | D004 | D007 | 目标解析适配和 opaque target 消费 |
| 测试 | D008 | D009 | 合同、过期、版本冲突和架构约束测试 |
| 联调 | D010 | D012 | 与 P2/Provider 目标解析联调 |

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 3 |
| 开发 | 4 |
| 测试 | 2 |
| 联调 | 3 |
| **总人天** | **12** |

### 10. 备注

```text
- 原子子功能：C-SUB-06、C-SUB-07；WBS：C-EPIC-04-T1、C-EPIC-04-T2。
- C 负责 memory_id → representation_id → provider_ref 的逻辑关联；P2/Provider 负责真实目标解析。
- ActuationTarget 解析成功不等于动作执行成功。
```

---

## C-EPIC-05 · Scheduling View & Capacity Observation

**Epic 范围**：消费真实 Placement、Segment 观测和资源状态，形成调度所需的观察视图。

**关联 PRR Action**：C-P1-3。

**Epic 级完成标准**：C 只消费真实观察，不伪造 current_tier；过期或未知观察会被标记并触发安全降级；段级统计不反推对象级事实。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-08 | Placement State Consumption |
| C-SUB-09 | Segment Introspection Consumption |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-05 |
| Epic Name | Scheduling View & Capacity Observation |
| Feature ID | C-FEAT-05 |
| Feature Name | 调度视图与容量观测 |
| Owner | C（Operate / Optimize） |
| Contributor | P2 / Provider（Placement、Segment、ResourceState）；Shared Runtime（观察契约） |
| 目标里程碑 | MVP |

### 2. User Story

作为调度决策方，我希望读取 P2 提供的真实层级、generation、容量和压力观测，从而区分“计划要放哪里”和“现在实际在哪里”，并在资源不足时保护在线服务。

### 3. 功能描述

#### 3.1 功能目标

消费 `PlacementObservation`、`SegmentIntrospection` 和 `ResourceState`，保存带来源和新鲜度的观察副本，向决策和 Capacity Guard 提供可靠输入。

#### 3.2 核心输入

```text
representation_id
provider_ref
target_id
resource_scope_id
provider_name
segment_ref
trace_id
request_id
```

#### 3.3 核心处理

1. 按 representation、Provider、目标或资源范围请求真实观察。
2. 校验对象标识、generation、route_epoch、版本、来源和观察时间。
3. 根据 `observed_at` 判断 Placement 和资源快照是否新鲜。
4. 保存 current_tier、可读性、支持动作、容量、压力、迁移和延迟指标。
5. 将段级观测用于容量和诊断，不反推具体 Memory 或 representation 的层级。
6. 对 stale、unknown、not_found 和冲突结果执行安全降级。

#### 3.4 核心输出

```text
PlacementObservation
ResourceState
segment_observation
current_tier
generation
route_epoch
supported_operations
readable
serving_ready
capacity_snapshot
pressure_snapshot
freshness
observation_source
```

### 4. 正常流程

```text
计划前或动作前请求真实观察
↓
接收 P2 返回的 Placement、Segment 和资源快照
↓
校验对象、版本和新鲜度
↓
保存最新观察副本
↓
生成 SchedulingView 输入
↓
供 Placement Decision、Capacity Guard 和 Reconciliation 使用
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| current_tier 或 generation 缺失 | 标记观察不可用于非 Keep 动作 | 等待补齐或重查 |
| 观察版本低于已保存版本 | 丢弃旧观察 | 不覆盖当前事实 |
| Provider 不可用或观察过期 | 保留旧快照并标记 stale/unknown | 只允许查询、对账和 Keep |
| Segment not_found | 记录资源观测缺失 | 不根据缺失信息发动作 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| PlacementStatePort | 获取真实 current_tier 和 generation | P2 / Provider |
| SegmentIntrospectionPort | 获取段级容量、压力和健康观测 | P2 / Provider |
| ResourceState | 获取资源范围、预算和迁移状态 | P2 / Provider |
| PLC-001 / SEG-001 | 观察契约和错误语义 | P2 / Integration |

### 7. 验收标准

1. C 能获取并保存 current_tier、generation、route_epoch 和 observed_at。
2. stale、unknown 或 not_found 结果不会驱动新的非 Keep 动作。
3. 旧观察不会覆盖更高版本的观察事实。
4. C 不根据自己的动作记录推算 current_tier 或真实容量。
5. 段级统计不会被当作对象级 Placement 事实。
6. 观察能够关联到 representation、provider_ref 和资源范围。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
|---|---|---|---|
| 设计 | D001 | D003 | Placement、Segment、ResourceState 的消费边界 |
| 开发 | D004 | D008 | 观察适配、快照保存和 SchedulingView 输入 |
| 测试 | D009 | D011 | stale、unknown、not_found 和乱序测试 |
| 联调 | D012 | D014 | 与 P2 Placement、Segment 和资源接口联调 |

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 3 |
| 开发 | 5 |
| 测试 | 3 |
| 联调 | 3 |
| **总人天** | **14** |

### 10. 备注

```text
- 原子子功能：C-SUB-08、C-SUB-09；WBS：C-EPIC-05-T1、C-EPIC-05-T2。
- PlacementObservation 和 ResourceState 的权威方是 P2/Provider，C 只保存消费快照和决策证据。
- “计划目标”和“真实层级”必须保持分离。
```

---

## C-EPIC-06 · Placement Decision, Capacity Guard & Admission Control

**Epic 范围**：计算 desired tier，执行阈值、冷却、容量、带宽、并发、预算和 Scope 配额检查，决定动作是否准入。

**关联 PRR Action**：C-P0-1、C-P0-2、C-P1-4。

**Epic 级完成标准**：目标层容量不足不发动作；在线 P99 恶化能够节流或暂停后台动作；规则有版本、有原因、有界且可解释。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-10 | Desired Tier and Supported Operations |
| C-SUB-11 | Cooldown, Capacity Guard and Admission |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-06 |
| Epic Name | Placement Decision, Capacity Guard & Admission Control |
| Feature ID | C-FEAT-06 |
| Feature Name | 放置决策、容量保护与准入 |
| Owner | C（Operate / Optimize） |
| Contributor | P2 / Provider（资源事实和支持动作）；B（业务属性）；Shared Runtime（策略与配额） |
| 目标里程碑 | MVP |

### 2. User Story

作为调度平台负责人，我希望在提交动作前统一检查真实层级、支持动作、容量、并发、冷却和配额，从而避免调度震荡、资源超载和后台动作挤占 Working 资源。

### 3. 功能描述

#### 3.1 功能目标

根据 observed tier、hotness、supported_operations 和资源事实计算 desired tier，并在动作生成前执行 Capacity Guard、Cooldown 和 Admission Control。条件不足时输出 Keep / No-op。

#### 3.2 核心输入

```text
representation_id
current_tier
hotness_score
supported_operations
target_generation
policy_version
cooldown_context
manual_override
resource_scope_id
available_capacity
working_reserved_capacity
prewarm_budget
prewarm_used
pin_budget
pin_used
active_migration_count
max_concurrent_migration
migration_bandwidth
read_latency_p99
write_latency_p99
action_cost
priority
```

#### 3.3 核心处理

1. 校验 Placement 和 ResourceState 的新鲜度及 Provider 健康状态。
2. 根据 current tier、hotness 和支持动作计算 desired tier。
3. 计算 Working、Prewarm 和 Pin 的可用余量。
4. 检查冷却、阈值、并发、带宽、优先级、Scope 配额和人工控制。
5. 输出 Allow、Throttle、Pause、Reject New 或 Keep / No-op。
6. 保存 policy_version、decision_reason、资源快照引用和拒绝原因。

#### 3.4 核心输出

```text
desired_tier
decision_reason
policy_version
input_fingerprint
supported_operation_check
cooldown_result
capacity_guard_result
budget_check_result
admission_decision
throttle_level
resource_snapshot_reference
```

### 4. 正常流程

```text
读取 Placement、Hotness 和 ResourceState
↓
计算 desired_tier 和动作成本
↓
执行支持动作、冷却、容量、并发和配额检查
↓
判断 Allow / Throttle / Reject / Keep
↓
记录准入证据
↓
允许通过的候选进入 TierAction.Generated
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| Working 预留不足 | 停止新增低优先级后台动作 | 保护 Working 读写 |
| 迁移并发或带宽达到上限 | Throttle 或 Keep | 不提交超预算动作 |
| ResourceState stale/unknown | 禁止新的非 Keep 动作 | 等待状态恢复或人工处置 |
| 目标动作不被支持 | 记录不支持原因 | 不创建该动作 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| PlacementObservation | current_tier、generation 和可读性 | P2 / Provider |
| ResourceState | 容量、压力、迁移和延迟事实 | P2 / Provider |
| Representation Hotness | 表示级热度 | C-EPIC-02 |
| PolicyContext | 阈值、冷却、预算和配额规则 | C / Shared Runtime |

### 7. 验收标准

1. 每个决策都能说明 current_tier、desired_tier、policy_version 和 decision_reason。
2. 不支持的动作不会进入 TierAction。
3. 观察过期、generation 不匹配或人工控制阻止时输出 Keep / No-op 或等待。
4. Working、Prewarm 和 Pin 预算能够分别判断。
5. 并发、带宽和 P99 影响能够触发限流或暂停。
6. 同一对象在冷却窗口内不会反复生成相同方向动作。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
|---|---|---|---|
| 设计 | D001 | D005 | 目标层级、准入、预算、冷却和保护规则 |
| 开发 | D006 | D012 | Decision、Capacity Guard 和 Admission 实现 |
| 测试 | D013 | D017 | 超载、冷却、配额、降级和 Keep 测试 |
| 联调 | D018 | D020 | 与 P2 Placement、ResourceState 和支持动作联调 |

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 5 |
| 开发 | 7 |
| 测试 | 5 |
| 联调 | 3 |
| **总人天** | **20** |

### 10. 备注

```text
- 原子子功能：C-SUB-10、C-SUB-11；WBS：C-EPIC-06-T1、C-EPIC-06-T2。
- C 计算 desired_tier，但不修改 P2 的 current_tier。
- C 不能根据自己的动作记录推算真实容量，容量事实必须来自 P2/Provider。
```

---

## C-EPIC-07 · TierAction Runtime & Operator Control

**Epic 范围**：管理 TierAction 的状态、幂等、Provider 状态映射和人工控制，区分动作已接收与动作已完成。

**关联 PRR Action**：C-P0-3、C-P0-4、C-P1-5。

**Epic 级完成标准**：Generated、Submitted、Succeeded、Failed、Unknown 五态完整；Accepted 不等于成功；人工控制有权限、幂等、有效期和审计。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-12 | TierAction State Machine |
| C-SUB-13 | Provider State Mapping and Operator Control |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-07 |
| Epic Name | TierAction Runtime & Operator Control |
| Feature ID | C-FEAT-07 |
| Feature Name | TierAction 运行时与人工控制 |
| Owner | C（Operate / Optimize） |
| Contributor | P2 / Provider（执行状态和控制能力）；Shared Runtime（状态目录、权限和审计） |
| 目标里程碑 | MVP |

### 2. User Story

作为调度执行方和运维人员，我希望动作状态、Provider 映射以及暂停、恢复、取消、Pin 和 Force Keep 有统一规则，从而不把已接收误认为已完成，并能在异常时安全接管。

### 3. 功能描述

#### 3.1 功能目标

管理 C 拥有的 `TierAction.ActionState`，映射 P2 的 ACCEPTED、RUNNING、SUCCEEDED、FAILED、UNKNOWN，并管理 `OperatorControlTask` 的控制意图和审计。C 不拥有 P2 的物理执行状态。

#### 3.2 核心输入

```text
action_id
idempotency_key
plan_id
representation_id
provider_ref
actuation_target
action_type
source_tier
desired_tier
expected_generation
policy_version
priority
deadline
provider_task_id
provider_status
actual_tier
result_generation
operator_control_id
control_type
scope
reason
operator_id
effective_from
effective_until
permission_context
```

#### 3.3 核心处理

1. 由有效 Plan 和准入结果生成 TierAction。
2. 按允许转换记录 Generated、Submitted、Succeeded、Failed 或 Unknown。
3. 将 Provider 状态映射到 C 状态，明确 Accepted/Submitted 不等于 Succeeded。
4. 校验 actual_tier、generation、对象引用和结果证据。
5. 校验人工控制人的权限、作用域、有效期和原因。
6. 重试创建新的 action_id，并保存与旧动作的关联。

#### 3.4 核心输出

```text
TierAction
action_id
action_state
provider_task_id
provider_status
actual_tier
result_generation
error_code
operator_control_task
control_state
audit_record
created_at
submitted_at
completed_at
```

### 4. 正常流程

```text
PlacementPlan 通过准入和目标校验
↓
生成 TierAction
↓
提交 P2，进入 Submitted
↓
接收或查询 Provider 状态
↓
校验实际层级和 generation
↓
收口为 Succeeded、Failed 或进入 Unknown 对账
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| P2 返回 ACCEPTED/RUNNING | 保持 Submitted | 不宣告成功 |
| 提交响应丢失或结果无法判断 | 进入 Unknown | 交给 C-EPIC-09 对账 |
| 重复 action_id 或幂等键 | 复用已有动作事实 | 不重复执行 |
| Cancel 不被当前物理阶段支持 | 保留待确认或失败事实 | 不伪造已取消 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| PlacementPlan | 提供动作目标和前置版本 | C-EPIC-03 |
| SubmitTierAction | 接收抽象动作并返回任务状态 | P2 / Provider |
| QueryActionStatus / Feedback | 查询和反馈动作结果 | P2 / Provider |
| State Catalog / Audit | 统一状态、权限和审计 | Shared Runtime |

### 7. 验收标准

1. Generated、Submitted、Succeeded、Failed、Unknown 五态语义和转换完整。
2. ACCEPTED/RUNNING 只能映射为 Submitted。
3. Succeeded 必须同时满足 Provider 成功、actual_tier 正确和 generation 校验通过。
4. Unknown 能关联 action_id 并进入对账。
5. 重复 action_id 或幂等键不会重复执行物理动作。
6. 人工控制必须校验权限、作用域、有效期和原因，并生成审计记录。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
|---|---|---|---|
| 设计 | D001 | D004 | TierAction 状态、Provider 映射和人工控制规则 |
| 开发 | D005 | D010 | 动作记录、状态机、控制任务和审计 |
| 测试 | D011 | D014 | 状态转换、幂等、权限和控制限制测试 |
| 联调 | D015 | D018 | 与 P2 提交、反馈和 Freeze/Unfreeze/Cancel 联调 |

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 4 |
| 开发 | 6 |
| 测试 | 4 |
| 联调 | 4 |
| **总人天** | **18** |

### 10. 备注

```text
- 原子子功能：C-SUB-12、C-SUB-13；WBS：C-EPIC-07-T1、C-EPIC-07-T2。
- C 拥有 TierAction.ActionState；P2 拥有 Provider 任务和物理执行状态。
- Operator Control 主要影响新的调度准入，已提交动作仍需按反馈和对账规则收敛。
```

---

## C-EPIC-08 · Storage Control Simulator

**Epic 范围**：提供共享 `SimulatedStorageState`，统一模拟 Placement、ActuationTarget、TierAction、Feedback 和 Health 视图，并覆盖 Case 1～6 故障场景。

**关联 PRR Action**：—。

**Epic 级完成标准**：正常模式下各控制视图共享同一状态；六类故障可重复注入；模拟器能验证状态机、幂等和对账，但不替代真实 P2 迁移验收。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-14 | Unified Storage Control Simulator |
| C-SUB-15 | Simulator Case 1-6 Fault Suite |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-08 |
| Epic Name | Storage Control Simulator |
| Feature ID | C-FEAT-08 |
| Feature Name | 统一存储控制模拟器与故障套件 |
| Owner | C（Operate / Optimize） |
| Contributor | C 组测试；P2/Provider 模拟适配；项目验收方 |
| 目标里程碑 | MVP |

### 2. User Story

作为 C 的开发、测试和验收人员，我希望多个控制能力共享一个可恢复的模拟存储状态，并能重复注入典型故障，从而在真实 P2 尚未就绪时验证调度闭环。

### 3. 功能描述

#### 3.1 功能目标

建设测试环境的 `SimulatedStorageState` 和统一 Control Port 视图，覆盖正常成功、失败、反馈丢失、超时、重启、重复动作和 desired/observed 不一致。

#### 3.2 核心输入

```text
representation_id
provider_ref
current_tier
generation
action_id
idempotency_key
desired_tier
provider_task_id
feedback_status
fault_mode
restart_marker
case_id
```

#### 3.3 核心处理

1. 保存对象当前层级、generation、动作记录、反馈记录和查询记录。
2. 让 Placement、Target、Action、Feedback 和 Health 视图读取同一模拟状态。
3. 正常模式下成功动作更新 current_tier 和 generation。
4. Fault Mode 注入延迟、失败、反馈丢失、重启和重复提交。
5. 驱动 C 的 Query、Reconciliation 和 Retry 逻辑，验证最终收口。
6. 输出可重放的测试证据，不把模拟结果当成真实 P2 事实。

#### 3.4 核心输出

```text
SimulatedStorageState
PlacementObservation
ActuationTarget
SubmitResult
ExecutionFeedback
ActionStatus
resource_health_snapshot
case_execution_record
query_history
assertion_result
```

### 4. 正常流程

```text
测试提交抽象 TierAction
↓
模拟器记录 action_id 和幂等键
↓
返回 ACCEPTED / RUNNING
↓
模拟执行并更新层级与 generation
↓
返回 SUCCEEDED 和反馈
↓
C 查询 Placement 并完成断言
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| 真实 P2 尚未就绪 | 使用共享模拟状态验证控制面 | Lane 2 可先行 |
| 故障注入器不可用 | 标记测试未完成并保存现场 | 该 Case 需要重跑 |
| 模拟器重启 | 从持久化或独立进程状态恢复 | 验证动作不重复 |
| 模拟反馈与 Placement 矛盾 | 保留矛盾证据并进入对账 | 验证 C 不盲信单一反馈 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| C Control Ports | 被模拟的统一控制接口 | C |
| SimulatedStorageState | 共享模拟世界状态 | C 测试组件 |
| TierAction / Reconciliation | 被验证的状态机和收口能力 | C-EPIC-07、C-EPIC-09 |
| Test Driver | 故障注入、重启和断言 | C 测试 |

### 7. 验收标准

1. Placement、Target、Action、Feedback 和 Health 视图读取同一模拟状态。
2. 正常成功路径会更新 current_tier 和 generation。
3. 重复 action_id 不重复执行。
4. 模拟状态可以在重启后恢复。
5. Case 1～6 能被重复注入、执行和复盘。
6. 模拟器只验证 C 控制面，不被当作真实 P2 或物理迁移验收依据。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
|---|---|---|---|
| 设计 | D001 | D003 | 共享状态、Control Port 视图和故障模型 |
| 开发 | D004 | D011 | Simulator、状态持久化和测试驱动 |
| 测试 | D012 | D017 | 正常路径和 Case 1～6 Fault Test |
| 联调 | D018 | D020 | 与 C 状态机、查询和对账能力联调 |

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 3 |
| 开发 | 8 |
| 测试 | 6 |
| 联调 | 3 |
| **总人天** | **20** |

### 10. 备注

```text
- 原子子功能：C-SUB-14、C-SUB-15；WBS：C-EPIC-08-T1、C-EPIC-08-T2。
- SimulatedStorageState 仅用于测试环境；生产 current_tier 和 generation 由 P2/Provider 提供。
- 共享模拟器框架只开发一次，六类故障用例复用同一状态。
```

---

## C-EPIC-09 · Feedback / Reconciliation & Migration Safety

**Epic 范围**：处理反馈丢失、Unknown、stale Placement、generation mismatch、P2 重启和重试恢复，确保动作先核实再收敛。

**关联 PRR Action**：C-P0-5、C-P1-1、C-P1-2、C-P1-5。

**Epic 级完成标准**：Unknown 先查询不盲重试；Action、Plan、Feedback 和 Placement 能对账；重启后不重复动作；只有确认原动作未产生物理效果后才新建 action_id。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-16 | Unknown / Stale / Missing Feedback Reconciliation |
| C-SUB-17 | Query / Retry Policy |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-09 |
| Epic Name | Feedback / Reconciliation & Migration Safety |
| Feature ID | C-FEAT-09 |
| Feature Name | 反馈、对账与迁移安全 |
| Owner | C（Operate / Optimize） |
| Contributor | P2 / Provider（状态查询和反馈）；Shared Runtime（持久任务、重试和对账机制） |
| 目标里程碑 | MVP |

### 2. User Story

作为调度闭环负责人，我希望反馈丢失、Placement 过期或 generation 不一致时能够核实真实状态，并按预算安全重试，从而避免错误成功和重复物理动作。

### 3. 功能描述

#### 3.1 功能目标

建立 `ReconciliationTask` 和 Query/Retry 运行时，针对 TierAction Unknown、反馈丢失、stale Placement、generation mismatch 和 Action/Observation 不一致进行查询、比较和收敛。

#### 3.2 核心输入

```text
reconciliation_task_id
action_id
plan_id
provider_task_id
provider_ref
expected_generation
actual_tier
desired_tier
route_epoch
provider_status
observation_id
feedback_id
retry_of_action_id
retry_count
retry_budget
backoff_context
restart_marker
reconciliation_reason
```

#### 3.3 核心处理

1. 发现 Unknown、反馈丢失、观察过期或版本冲突时创建对账任务。
2. 优先按 action_id 查询 P2 的原动作，并重新读取 Placement。
3. 比较 Action、Plan、Feedback 和 Observation 的对象、层级、generation、route_epoch。
4. 已确认达到目标时收口为 Succeeded；明确未执行时进入 Failed 或允许重新计划。
5. 证据不足时保持对账中，不直接发起新的物理动作。
6. 只有确认原动作未产生物理效果后，才消耗重试预算并创建新的 action_id。

#### 3.4 核心输出

```text
ReconciliationTask
reconciliation_state
query_result
latest_placement_observation
comparison_result
convergence_decision
retry_decision
retry_budget_remaining
next_retry_at
new_action_id
retry_of_action_id
evidence
```

### 4. 正常流程

```text
TierAction 进入 Unknown 或发现观察不一致
↓
创建 ReconciliationTask
↓
查询原 action_id 和最新 Placement
↓
比较层级、generation、route_epoch 和对象引用
↓
收口原 TierAction
↓
确认未执行后才创建新 action_id
↓
新动作重新走完整准入和提交流程
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| Provider 查询仍无结论 | 保持 Waiting / Running | TierAction 继续 Unknown |
| Feedback 与 Placement 冲突 | 保留冲突证据并继续核验 | 不宣告成功 |
| 重试预算用尽 | 停止自动重试并告警 | 等待人工或下一轮计划 |
| C 或 P2 重启 | 恢复未终态任务并先查询 | 不重复提交原动作 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| QueryActionStatus | 按 action_id 查询原动作 | P2 / Provider |
| PlacementObservation | 获取真实层级和版本 | P2 / Provider |
| ExecutionFeedback | 获取动作过程和结果证据 | P2 / Provider |
| Durable Task / Retry | 持久恢复、退避和预算 | Shared Runtime |
| TierAction | 被对账和重试的动作记录 | C-EPIC-07 |

### 7. 验收标准

1. Unknown、反馈丢失、stale Placement 和 generation mismatch 都能创建对账任务。
2. 对账先查询原 action_id，不盲目创建新的 action_id。
3. 实际层级达到目标且 generation 校验通过时，原动作可收口为 Succeeded。
4. 证据不足时不错误宣告成功或失败。
5. 每次重试都有 retry_of_action_id、预算、退避和原因记录。
6. C 重启后未终态动作可恢复，且不重复执行。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
|---|---|---|---|
| 设计 | D001 | D004 | 对账条件、收口规则、查询和重试预算 |
| 开发 | D005 | D010 | ReconciliationTask、查询、重试和恢复运行时 |
| 测试 | D011 | D015 | Unknown、stale、冲突、重启和预算测试 |
| 联调 | D016 | D018 | 与 P2 查询、Placement 和 Feedback 联调 |

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 4 |
| 开发 | 6 |
| 测试 | 5 |
| 联调 | 3 |
| **总人天** | **18** |

### 10. 备注

```text
- 原子子功能：C-SUB-16、C-SUB-17；WBS：C-EPIC-09-T1、C-EPIC-09-T2。
- ReconciliationTask 负责查询、比较和收敛，不因查询本身直接发起迁移。
- 重试不是直接再次提交的授权；旧 TierAction 历史不可被原地改写。
```

---

## C-EPIC-10 · Optimize / Predict & Controlled Release

**Epic 范围**：建立启发式基线、特征与策略版本契约，接入预测、回退和效果测量，支持 Shadow、Canary、Progressive 和 Rollback。

**关联 PRR Action**：C-P0-6。

**Epic 级完成标准**：同版本同输入的 Heuristic 决策可复现；预测异常时按稳定版本、Heuristic、Keep/No-op 回退；命中率和资源影响可审计；不直接跳到 RL 或 Provider-specific action。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-18 | Frozen Heuristic Baseline |
| C-SUB-19 | Feature Contract and Policy Version |
| C-SUB-20 | Prediction, Fallback and Measurement |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-10 |
| Epic Name | Optimize / Predict & Controlled Release |
| Feature ID | C-FEAT-10 |
| Feature Name | 优化、预测与受控发布 |
| Owner | C（Operate / Optimize） |
| Contributor | A/B（访问和记忆事实）；P2/Provider（资源和执行反馈）；Shared Runtime（版本和发布治理）；项目验收方 |
| 目标里程碑 | MVP |

### 2. User Story

作为调度优化负责人，我希望预测策略有稳定基线、版本和完整回退链，并能用真实访问和执行结果评估收益，从而控制发布风险并判断策略是否带来实际调度效果。

### 3. 功能描述

#### 3.1 功能目标

冻结第一版 Heuristic，定义 feature/label/policy/model 版本，接入预测并建立“上一稳定版本 → Heuristic → Keep / No-op”的回退链，测量命中率、预热收益、资源影响和收敛情况。

#### 3.2 核心输入

```text
representation_id
feature_snapshot_reference
label_snapshot_reference
prediction_result
prediction_score
prediction_window
model_version
policy_version
heuristic_result
previous_stable_result
current_tier
observed_tier
access_outcome
hit_after_prefetch
resource_impact
release_stage
release_scope
```

#### 3.3 核心处理

1. 固定基线输入、权重、阈值、窗口和冷却规则。
2. 校验特征、标签、模型、策略版本和预测窗口。
3. 按 Shadow → Canary → Progressive 控制策略放量。
4. 对预测结果执行资源、版本、冲突和动作可执行性检查。
5. 预测异常时回退到上一稳定版本，再回退到 Heuristic，最后 Keep / No-op。
6. 将可用结果交给 PlacementPlan，不直接调用 Provider-specific action。
7. 使用真实 AccessTrace、ExecutionFeedback 和 Placement 评估命中率、预热收益、资源影响和对账收敛。

#### 3.4 核心输出

```text
heuristic_decision
prediction_decision
fallback_level
selected_policy_version
selected_model_version
policy_release_record
decision_reason
hit_rate_measurement
prewarm_effectiveness
resource_impact_measurement
evaluation_report
rollback_reason
```

### 4. 正常流程

```text
准备版本化特征、标签、模型和策略
↓
执行 Heuristic 或预测并校验结果
↓
进入 Shadow，再按范围进入 Canary / Progressive
↓
通过准入检查后生成 PlacementPlan
↓
预测异常时回退到上一稳定版本
↓
上一稳定版本不可用时回退到 Heuristic
↓
全部不可用时 Keep / No-op
↓
用真实访问、反馈和 Placement 评估效果
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| 特征、标签或策略版本不匹配 | 阻止发布或决策 | 不产生无版本结果 |
| 模型或预测服务异常 | 回退到上一稳定版本 | 不因模型异常发新动作 |
| 上一稳定版本不可用 | 使用 Frozen Heuristic | 保持可解释基线决策 |
| 所有决策输入不足 | 输出 Keep / No-op | 不进行不确定调度 |
| Canary 指标回归 | 停止放量并回退 | 不扩大影响范围 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| Representation Hotness / PlacementPlan | 热度、目标和决策承载 | C-EPIC-02、C-EPIC-03 |
| AccessTrace / MemorySignal | 访问和业务变化事实 | A/B、C-EPIC-01 |
| Placement / Feedback / ResourceState | 真实结果和资源影响 | P2 / Provider |
| Policy / Model Registry | 版本保存、读取和发布 | C / Shared Runtime |
| Heuristic Baseline | 可复现的安全回退 | C |

### 7. 验收标准

1. 相同 policy_version 和相同输入得到一致的 Heuristic 决策。
2. 每个预测决策能够关联 feature、label、model 和 policy 版本。
3. 预测异常时能按“上一稳定版本 → Heuristic → Keep / No-op”顺序回退。
4. 回退不会删除历史 Plan、TierAction 或审计记录。
5. 预测不会绕过 PlacementPlan、ActuationTarget 和准入检查直接提交动作。
6. 命中率、预热命中、资源影响和调度结果能够关联到版本和 action_id。
7. Shadow、Canary、Progressive 和 Rollback 均有可执行步骤和证据。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
|---|---|---|---|
| 设计 | D001 | D003 | Heuristic、特征/标签、版本、回退和测量口径 |
| 开发 | D004 | D008 | 基线、版本治理、预测接入和评估能力 |
| 测试 | D009 | D012 | 复现、版本漂移、模型异常和效果测量测试 |
| 联调 | D013 | D014 | 与访问、资源、反馈和端到端调度联调 |

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 3 |
| 开发 | 5 |
| 测试 | 4 |
| 联调 | 2 |
| **总人天** | **14** |

### 10. 备注

```text
- 原子子功能：C-SUB-18、C-SUB-19、C-SUB-20；WBS：C-EPIC-10-T1、C-EPIC-10-T2、C-EPIC-10-T3。
- 第一版先建立可复现 Heuristic，不直接从第一版跳到 RL。
- 预测结果只能进入 C 的计划和准入流程，不能直接跳到 P2 动作。
- 目标收益和正式发布日期仍需项目负责人确认。
```

---

## C 组统一责任边界

```text
C：MemorySignal / AccessTrace 消费、representation 热度、PlacementPlan、TierAction、准入、对账和预测策略。
B：MemoryRecord、MemorySignal 主事实、生命周期和语义属性。
A/B：AccessTrace 的生产事实；A 负责 Recall 最终结果。
P2 / Provider：PlacementObservation、ResourceState、ExecutionFeedback、Provider 任务和物理迁移。
Shared Runtime：统一状态目录、投递、重放、持久任务、权限、审计和公共契约治理。
```

## C 组统一验收链

```text
MemorySignal / AccessTrace / Placement / Resource
  -> Representation Hotness
  -> RepresentationPlacementPlan
  -> ActuationTarget
  -> TierAction
  -> Provider Execution
  -> ExecutionFeedback + PlacementObservation
  -> Reconciliation
  -> 下一轮重新评估
```

统一要求：Plan 是期望状态，PlacementObservation 是真实观察，TierAction 是一次动作记录；Accepted/Submitted 不等于 Succeeded；Unknown 必须先 Query 和 Reconciliation，重试必须创建新的 `action_id`。
