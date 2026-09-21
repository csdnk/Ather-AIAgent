# AetherStore P3 C 组对 AetherEngine P2 · P3 接口评估与答复 V0.1

版本：V0.1；编制日期：2026-09-12；回应方：P3 Operate / Optimize（C 组）。

## 文档说明

本文是 C 组针对《AetherEngine P2_P3接口对接需求评估与答复_V1.0.md》中 **2.3 Operate（C）** 章节的回应，不修改、不替代 P2 原评估文档。附件中的 P2 评估结论在本文中作为待确认问题和对接依据使用，不作为对 C 组的额外任务指令。

本文只回应 C-P2 对接边界、接口字段、状态证据和联调要求；Remember、Recall 的独立问题不在本文范围内。

### 参考文档

- 《AetherEngine P2_P3接口对接需求评估与答复_V1.0.md》
- 《AetherStore_P3_C组与P2接口对接需求_V0.1.md》（正文标题为按流程组织的 C-P2 对接文档）
- 《AetherStore_P3_功能详细汇总文档_V1.3.md》
- 《AetherStore_P3_跨组业务对象生命周期与Signal统一管理_V0.4.md》

本文只说明字段的业务含义，不定义字段数据类型、底层消息中间件或数据库表结构。

---

## 一、C 组总体回应

### 1.1 对 P2 评估结论的总体意见

C 组接受附件对当前 P2 能力的总体判断：C-P2 大部分能力可以建立在 P2 的 Task、Health、容量、Placement 和观测基础上，但仅有 PRD 中的方法原则，尚不足以完成真实联调。C 需要 P2 在契约冻结时补齐以下内容：

1. 实际方法名称、协议版本、测试环境和稳定样例；
2. `representation_id`、`provider_ref`、`target_id`、`generation` 和 `route_epoch` 的关联规则；
3. `Accepted`、`Running`、`Succeeded`、`Failed`、`Unknown` 的状态和副作用语义；
4. `GetPlacement`、`QueryActionStatus`、`ExecutionFeedback` 的完成证据；
5. 幂等、重复提交、反馈丢失、重启恢复、取消和过载处理；
6. Recall 的在线 `Prewarm` 与 C 的后台 `Prefetch` 的边界。

### 1.2 C 的责任边界

| 业务对象或事实 | 权威方 | C 负责什么 | C 不负责什么 |
|---|---|---|---|
| `MemoryRecord`、`MemorySignal` | Remember / B | 消费记忆变化信号 | 不修改 B 的主记录和版本事实 |
| `AccessTrace` | Recall / 实际观测组件 | 消费真实访问阶段并归一化为热度输入 | 不把候选命中自动解释为已加载或已使用 |
| `PlacementObservation` | P2 / Provider | 消费真实放置结果并参与对账 | 不生成或修改 `current_tier`、`generation`、`route_epoch` |
| `ResourceState` | P2 / Provider | 判断容量、健康和后台动作准入 | 不用自己提交的动作反推资源状态 |
| `PlacementPlan` | C | 计算热度、目标层级、策略和计划 | 不把计划目标当成真实放置事实 |
| `ActuationTarget` | P2 解析，C 消费 | 保存并转发不透明目标 | 不拼接 Provider-specific action，不解析 `target_id` |
| `TierAction` | C 生成，P2 执行 | 生成抽象动作、提交、跟踪和收口 | 不直接调用 Provider 内部迁移命令 |
| `ExecutionFeedback` | P2 / Provider | 消费反馈并触发查询、对账或重计划 | 不把 `Accepted` 或 `Running` 直接判为成功 |
| `ReconciliationTask` | Shared Runtime 承载，C 负责业务收口 | 比较 Action 与 Placement，决定收口或重计划 | 不替 P2 证明物理动作已经完成 |

### 1.3 C-P2 的最小闭环

```text
MemorySignal / AccessTrace
  -> C 计算 Representation 热度
  -> C 生成 PlacementPlan
  -> C 读取 Placement / ResourceState
  -> P2 解析不透明 ActuationTarget
  -> C 提交 TierAction
  -> P2 / Provider 执行 Copy / Verify / Cutover / Reclaim
  -> P2 返回 ExecutionFeedback
  -> C 重新读取 PlacementObservation
  -> C 完成业务对账，收口或重新计划
```

其中：

- `PlacementPlan` 表示期望状态；
- `PlacementObservation` 表示当前真实状态；
- `TierAction` 表示一次调度意图和动作记录；
- `ExecutionFeedback` 表示 P2 / Provider 的执行事实；
- `Succeeded` 必须由反馈和真实 Placement 共同确认；
- `Unknown` 必须先查询和对账，不能直接盲目重试。

---

## 二、C 对 14 项 C-P2 问题的逐项回应

### 2.1 六个还是八个逻辑能力，以及实际 API、版本和环境

**C 组回应：** 需要先统一编号口径。附件第 2.3 节第 1 项称“六个逻辑接口”，但现行 C-P2 对接文档定义的是 `CP2-01`～`CP2-08`：

1. `GetPlacement`；
2. `GetResourceState`；
3. `ResolveActuationTarget`；
4. `SubmitTierAction`；
5. `QueryActionStatus`；
6. `WatchExecutionFeedback`；
7. 物理迁移 `Copy / Verify / Cutover / Reclaim`；
8. `Freeze / Unfreeze / Cancel`。

因此，C 建议按“8 项逻辑能力”进行验收；如果 P2 只把前 6 项称为接口，则应明确后 2 项是由 P2 承担的执行与控制能力，不能从验收范围中删除。

**P2 需要补齐：** 每项能力的实际 SDK、RPC 或 HTTP 方法名，契约版本，请求和响应样例，错误码，测试环境，以及模拟环境和真实 Provider 环境的差异。

**C 的验收标准：** C 能使用固定版本的契约驱动器完成 8 项能力的调用和状态验证；真实联调阶段能把相同调用映射到 P2 / Provider，不需要修改 C 的业务对象语义。

### 2.2 `representation_id` 与 `provider_ref` 的稳定关系和失效规则

**C 组回应：** C 只把 `representation_id` 作为调度域的抽象表示标识，把 `provider_ref` 作为 P2 / Provider 侧的稳定对象引用。C 不根据 `memory_id`、显示名称或猜测的路径生成 `provider_ref`。

**规则建议：**

- 同一表示在未换版、未删除、未重新绑定时，`provider_ref` 应保持稳定；
- 表示换版、删除、映射重建或 Provider 目标变化时，P2 必须通过映射代次、对象代次或明确的冲突结果告知 C；
- `representation_id`、`provider_ref`、版本或 `target_id` 不一致时，P2 应拒绝执行或返回明确的 Conflict；
- 失效引用不能被静默指向另一个物理对象；
- C 只保存、比较和转发映射事实，不维护 Provider 内部路径。

**P2 需要补齐：** 稳定引用的生成方、失效条件、查询入口、换版和删除后的保留规则，以及 `provider_ref` 与 Provider 实际对象的对应证据。

### 2.3 `target_id` 是否由 P2 统一解析

**C 组回应：** 是。`target_id` 应由 P2 根据 C 提供的抽象信息统一解析，并作为不透明的 `ActuationTarget` 返回给 C。C 不解析 `target_id`，不把 `provider_ref` 直接当成 `target_id`，也不从 Plan 直接跳到 Provider-specific action。

C 发送的最小语义是：

- `representation_id`；
- `desired_tier`；
- 当前计划绑定的 `target_generation` 或等价的版本约束；
- `plan_id`；
- `trace_id`；
- `valid_until`（适用时）。

P2 返回目标后，C 只保存和转发 `target_id`、`provider_ref`、目标版本、路由代次和有效期。目标过期、映射变化或版本不一致时，C 重新解析，不继续使用旧目标。

### 2.4 `generation`、`mapping_generation`、`target_generation` 的区别

C 要求在契约中将以下字段分开，不能用一个字段覆盖全部含义：

| 字段 | 业务含义 | 产生或维护方 | C 的使用方式 |
|---|---|---|---|
| `memory_version` | B 侧业务记忆版本，表示内容或语义版本 | Remember / B | 用于版本关联，不当作 Provider 物理版本 |
| `mapping_generation` | 抽象表示到 Provider 对象的映射版本 | 当前映射维护方，按现有约定由 B / E2 维护 | 判断 `provider_ref` 是否仍对应当前表示 |
| `generation` | Provider 对象或内容的实际代次 | P2 / Provider | 防止旧观察、旧动作覆盖新事实 |
| `target_generation` | `ActuationTarget` 所绑定的目标代次 | P2 / Provider | 提交动作时作为目标版本约束 |
| `route_epoch` | 读取路由或服务副本的切换代次 | P2 / Provider | 判断 Cutover 前后路由是否变化 |

**C 组原则：** C 不自行递增上述字段，不用 `desired_tier` 覆盖 `current_tier`，不把 `memory_version` 当成 `generation`，也不把 `target_generation` 当成动作成功证明。P2 需要在返回中说明各字段的来源、更新时机和不一致时的处理。

### 2.5 `route_epoch` 在 Copy、Cutover、Reclaim 哪一步变化

**C 组回应：** 建议并要求按以下规则签收：

| 阶段 | 是否改变 `route_epoch` | C 的判断 |
|---|---|---|
| Copy | 否 | 只是建立目标副本，原读取路由仍有效 |
| Verify | 否 | 只是验证目标副本，不代表已经切换服务来源 |
| Cutover | 是，在路由切换成功提交并可观测后变化 | 代表服务路由已经切换 |
| Reclaim | 否 | 只回收不再承担服务的旧副本，不重新定义路由 |

如果 Cutover 失败或结果未知，P2 不能提前返回新的路由代次；C 应通过 `GetPlacement` 确认当前 `route_epoch` 和实际可读副本。具体是“切换提交时”还是“切换完成观测时”递增，需要在 RC 契约中固定为唯一规则。

### 2.6 `current_tier`、`actual_tier`、`desired_tier` 的使用规则

| 字段 | 所在事实 | 含义 | 责任方 |
|---|---|---|---|
| `desired_tier` | `PlacementPlan` / `TierAction` | C 希望达到的目标层级 | C 产生 |
| `current_tier` | `PlacementObservation` | P2 根据真实对象和可读副本观察到的当前层级 | P2 / Provider 返回 |
| `actual_tier` | `ExecutionFeedback` / `ActionStatus` | 某次动作执行后实际达到的层级 | P2 / Provider 返回 |

C 只有在 `actual_tier`、版本、路由和对象引用与计划一致，并重新获取到匹配的 `current_tier` 后，才把 Action 收口为 `Succeeded`。P2 不应根据 `desired_tier` 生成 `current_tier`，C 也不修改 P2 的真实状态字段。

### 2.7 `GetPlacement` 与 `QueryActionStatus` 是否合并

**C 组回应：** 两者可以由同一个服务或 RPC 承载，但必须保留两类独立事实：

- `GetPlacement` 回答“对象现在真实在哪里、是否可读、版本和路由是什么”；
- `QueryActionStatus` 回答“某一次 Action 当前执行到哪一步、是否有副作用、P2 是否仍能确认结果”。

“任务存在”“任务已经受理”或“任务已结束”不能单独替代当前 Placement。C 在成功反馈后仍需重新调用 `GetPlacement`；反馈丢失、超时、重启或 Unknown 时，至少需要先查询原 Action，再查询最新 Placement。

### 2.8 `ExecutionFeedback` 的回调、事件流和查询方式

**C 组回应：** MVP 可以以 `QueryActionStatus` 轮询为主，但必须保留可恢复的查询能力。事件流或回调可以作为增强方式，不能成为唯一的完成证据。

若采用反馈流，至少需要：

- `feedback_id`；
- `action_id`；
- `provider_task_id`；
- `provider_status`；
- `actual_tier`；
- `pre_generation`、`post_generation`；
- `route_epoch`；
- `migration_stage`；
- `observed_at`；
- `next_feedback_cursor`；
- `error_code`、`retryable`；
- `evidence`。

反馈应支持重复投递去重、断点续传、重启后继续查询和结果缺口识别。若没有事件流，P2 必须保证 `QueryActionStatus` 能按 `action_id`、`provider_task_id` 或原幂等键恢复原动作事实。

### 2.9 同一 `action_id` 或幂等键重复提交

**C 组回应：** 同一动作的重复提交必须幂等：

- 同一 `action_id` 和相同动作载荷：返回原动作事实，不产生第二次物理执行；
- 同一幂等键和相同载荷：返回原动作及当前状态；
- 同一幂等键但载荷不同：返回 Conflict，不能覆盖原动作；
- 首次响应丢失：C 使用原 `action_id`、幂等键和 `provider_task_id` 查询，不生成一个看似新的重复动作；
- 原动作已明确未执行且需要重新计划时，C 才创建新的 `action_id`，并保留与原动作的关联。

P2 需要明确幂等键作用域、保留时间、原动作查询入口以及重启后的可查询时间窗口。

### 2.10 过载、超时、取消、重启和 Provider `Unknown`

C 采用以下状态映射原则：

| P2 / Provider 状态 | C 侧处理 |
|---|---|
| `ACCEPTED` | 记为已受理，C 的 Action 仍为 `Submitted` |
| `RUNNING` | 记为执行中，C 的 Action 仍为 `Submitted` |
| `SUCCEEDED` | 先重新查询 Placement，确认层级、对象、版本和路由后才收口 |
| `FAILED` 且明确没有物理副作用 | 收口为 `Failed`，按策略重新计划 |
| `FAILED` 但副作用或最终状态不清楚 | 收口为 `Unknown`，进入查询和对账 |
| `UNKNOWN` | 保留原 Action，先 Query、GetPlacement 和 Reconciliation，不盲目重试 |
| 过载、超时、暂不可用 | 根据 `error_code` 和 `retryable` 进入等待、退避或对账，不能直接判成功 |

P2 重启后应继续提供原 `action_id`、`provider_task_id` 和幂等键的查询结果。若需要重试，C 必须根据原动作的真实结果创建新的 `action_id`；P2 不能用隐式重试掩盖原动作的未知状态。

### 2.11 `Freeze`、`Unfreeze`、`Cancel` 的支持范围

**C 组回应：** C 接受 P2 需要明确这三类控制能力的边界，但不能假设所有动作、所有阶段都支持取消或冻结。

| 能力 | C 需要 P2 明确的内容 |
|---|---|
| `Freeze` | 冻结对象、目标、写入或路由中的哪一层；从哪个阶段开始生效；关联哪个 `action_id` 和 `generation` |
| `Unfreeze` | 成功、失败、取消和重启后何时恢复；恢复结果如何查询 |
| `Cancel` | 在 Copy、Verify、Cutover、Reclaim 哪些阶段有效；取消后是否仍可能有物理副作用；真实层级和副本状态是什么 |

当前 PRD 可直接支撑 Task 取消和部分 Segment freeze/seal，但 E2 迁移级 Freeze / Unfreeze 的语义仍需 P2 另行签收。没有明确取消结果时，C 将动作保留为 `Unknown` 或进入对账，不提前修改 Placement。

### 2.12 Recall 读取来源、放置版本和动作绑定证据

**C 组回应：** 如果 P2 能提供，读取响应或关联查询应至少给出：

- `actual_provider`；
- `observed_tier`；
- `placement_generation`；
- `route_epoch`；
- `producing_action_id`（适用时）；
- `observed_at`；
- `evidence`。

这些字段用于区分“本次读取确实来自哪个 Provider / 层级”和“只是系统存在一个调度动作”。C 不会从读取延迟、缓存命中或时间相近关系中自行推断动作来源。

如果 P2 当前读取接口无法返回上述证据，C 允许 Recall 继续返回内容，但该次放置归因必须标记为不可验证，不能伪造 `provider_ref`、`generation` 或 `producing_action_id`。

### 2.13 Recall `Prewarm` 与 C `Prefetch` 是否分开

**C 组回应：** 必须分开，至少在发起方、预算、动作记录和结果语义上分开。

| 项目 | Recall `RP2-04 Prewarm` | C `Prefetch` |
|---|---|---|
| 发起方 | A / Recall | C / Operator |
| 触发时机 | 在线读取当前正文时 | C 根据热度、预测和预算生成后台计划后 |
| 目的 | 尝试加速当前读取 | 提前建立后续访问可用的目标副本 |
| 是否创建 C 的 `TierAction` | 否 | 是，走 `CP2-04 SubmitTierAction` |
| 是否占用同一预算 | 不计入 C 的后台调度预算 | 使用 P2 返回的 C 后台预取预算 |
| 结果确认 | 随读取来源和放置观察返回 | `ExecutionFeedback` 加 `GetPlacement` 对账 |

附件评估已说明 P2 本期不提供 RP2-04 在线 `Prewarm` 能力，C 接受该结论。该结论不影响 C 的 `Prefetch`：C 的后台预取仍需要通过 P2 / Provider 建立热副本。

同时，`Prefetch`、`Promote` 和释放源副本不能混为一个动作：`Prefetch` 通常只建立目标副本并保留源副本；`Promote` 表示按计划建立或切换目标服务放置，也不自动等于删除源副本；是否执行 `Demote`、`Release` 或 `Reclaim`，必须由独立动作和独立证据确认。

### 2.14 在线读取命中热副本时不得隐式创建 C 调度动作

**C 组回应：** 必须保证在线读取是无调度副作用的只读路径：

- 命中热副本不会隐式创建 `Prefetch`、`Promote` 或 `Release`；
- 不会自动增加 C 的 `action_id`、计划记录或后台预算消耗；
- Recall 可以记录读取来源和 `AccessTrace`，但不能把读取事实反向解释成 C 已经执行调度；
- 如果产品将来确实需要“读取触发后台预取”，应作为显式新能力单独定义触发规则、动作来源、预算和审计，不得隐藏在现有读取接口中。

---

## 三、C 需要 P2 提供的最小字段集

以下字段是 C 完成调度、反馈和对账所需的最小业务字段。字段名最终以 RC 契约为准，但不能删除其业务含义。

### 3.1 公共请求与响应字段

| 字段 | C 使用目的 |
|---|---|
| `request_id` | 关联一次业务请求 |
| `trace_id` | 关联调用链和日志 |
| `schema_version` | 识别报文语义版本 |
| `representation_id` | 定位 C 的抽象调度对象 |
| `provider_ref` | 定位 Provider 稳定对象引用 |
| `target_id` | 传递 P2 解析出的不透明执行目标 |
| `plan_id` | 关联 PlacementPlan |
| `action_id` | 关联 C 生成的一次动作 |
| `provider_task_id` | 关联 P2 / Provider 执行任务 |
| `idempotency_key` | 防止重复执行 |
| `generation` | 关联 Provider 对象代次 |
| `route_epoch` | 关联路由切换代次 |
| `occurred_at` | 记录业务事实发生时间 |
| `observed_at` | 记录 P2 观察或反馈时间 |
| `error_code` | 说明失败、冲突或未知原因 |
| `retryable` | 说明 P2 对等待或重试的建议 |
| `evidence` | 指向支持状态结论的证据 |

### 3.2 `GetPlacement`（CP2-01）

**C 发送：** `representation_id`、`provider_ref`（适用时）、`target_id`（适用时）、`request_id`、`trace_id`。

**P2 返回：**

| 字段 | C 的使用目的 |
|---|---|
| `observation_id` | 观测去重和对账 |
| `representation_id` | 校验观测对象一致 |
| `provider_ref` | 校验稳定引用 |
| `target_id` | 关联已解析目标（适用时） |
| `provider_name` | 识别真实 Provider |
| `current_tier` | 获取当前真实层级 |
| `generation` | 防止旧观测覆盖新观测 |
| `route_epoch` | 判断路由是否变化 |
| `observed_at` | 判断观测新鲜度 |
| `observation_source` | 识别事实来自 P2、Executor 还是 Provider |
| `supported_operations` | 判断是否允许提交动作 |
| `readable` | 判断当前副本是否可读 |
| `serving_ready` | 判断是否可以作为服务副本 |
| `replica_count` | 判断释放副本后的可用性 |
| `state_version` | 处理乱序观测 |

### 3.3 `GetResourceState`（CP2-02）

**C 发送：** `resource_scope_id`、`provider_name`（适用时）、`request_id`、`trace_id`。

**P2 返回：**

| 字段 | C 的使用目的 |
|---|---|
| `resource_scope_id` | 确认资源事实适用范围 |
| `provider_name` | 识别资源来源 |
| `state_version` | 处理乱序资源状态 |
| `observed_at` | 判断资源快照新鲜度 |
| `freshness` | 判断是否允许正常准入 |
| `backend_health` | 判断 Provider 是否可接收动作 |
| `total_capacity_bytes` | 计算总容量 |
| `used_capacity_bytes` | 计算已用容量 |
| `available_capacity_bytes` | 判断动作成本是否可容纳 |
| `pressure_ratio` | 判断资源压力和释放优先级 |
| `working_reserved_capacity_bytes` | 保护在线 Working 资源 |
| `prewarm_budget_bytes` | 限制 C 后台预取 / 预热预算 |
| `prewarm_used_bytes` | 计算后台预取剩余额度 |
| `pin_budget_bytes` | 限制固定驻留 |
| `pin_used_bytes` | 计算固定驻留占用 |
| `active_migration_count` | 判断当前迁移压力 |
| `max_concurrent_migration` | 限制迁移并发 |
| `migration_bandwidth_bytes_per_sec` | 估算当前迁移能力 |
| `migration_bandwidth_budget_bytes_per_sec` | 保护在线服务带宽 |
| `read_latency_p99_ms` | 判断在线读取影响 |
| `write_latency_p99_ms` | 判断在线写入影响 |

### 3.4 `ResolveActuationTarget`（CP2-03）

**C 发送：** `representation_id`、`desired_tier`、`target_generation`（或契约约定的等价版本约束）、`plan_id`、`trace_id`、`valid_until`（适用时）。

**P2 返回：**

| 字段 | C 的使用目的 |
|---|---|
| `target_id` | 保存并转发的不透明目标 |
| `representation_id` | 校验目标对象一致 |
| `provider_ref` | 关联真实 Provider 对象 |
| `provider_name` | 识别执行 Provider |
| `source_tier` | 记录解析时的真实源层级 |
| `supported_operations` | 判断目标支持的动作 |
| `target_generation` | 校验目标代次 |
| `route_epoch` | 判断目标是否仍适用 |
| `resolved_at` | 判断目标解析时间 |
| `valid_until` | 判断目标是否过期 |

### 3.5 `SubmitTierAction`（CP2-04）

**C 发送：** `action_id`、`idempotency_key`、`plan_id`、`representation_id`、`provider_ref`、`actuation_target`、`action_type`、`source_tier`、`desired_tier`、`expected_generation`、`policy_version`、`priority`（适用时）、`deadline`（适用时）、`request_id`、`trace_id`。

**P2 返回：**

| 字段 | C 的使用目的 |
|---|---|
| `action_id` | 校验原动作标识没有被替换 |
| `provider_task_id` | 查询 Provider 执行任务 |
| `provider_status` | 判断受理、执行或失败状态 |
| `observed_at` | 判断返回状态新鲜度 |
| `error_code` | 解释提交失败或冲突 |
| `retryable` | 提供等待或重试建议 |

`ACCEPTED` 和 `RUNNING` 只代表 P2 已受理或开始执行，不代表目标层级已经达到。

### 3.6 `QueryActionStatus`（CP2-05）

**C 发送：** `action_id`、`provider_task_id`（已知时）、`idempotency_key`（适用时）、`request_id`、`trace_id`。

**P2 返回：**

| 字段 | C 的使用目的 |
|---|---|
| `action_id` | 关联原动作 |
| `provider_task_id` | 关联 P2 任务 |
| `provider_status` | 获取当前执行状态 |
| `actual_tier` | 获取动作实际层级 |
| `pre_generation` | 获取动作执行前代次 |
| `post_generation` | 获取动作执行后代次 |
| `route_epoch` | 校验完成时路由 |
| `migration_stage` | 判断 Copy、Verify、Cutover、Reclaim 进度 |
| `completion_time` | 记录 P2 的完成判断时间 |
| `error_code` | 分类失败和未知原因 |
| `retryable` | 获取处理建议 |
| `observed_at` | 判断查询结果新鲜度 |
| `evidence` | 支持动作结果和副作用判断 |

### 3.7 `ExecutionFeedback`（CP2-06）及迁移控制（CP2-07 / CP2-08）

**反馈字段：** `feedback_id`、`action_id`、`provider_task_id`、`provider_status`、`actual_tier`、`pre_generation`、`post_generation`、`route_epoch`、`migration_stage`、`completion_time`、`error_code`、`retryable`、`observed_at`、`next_feedback_cursor`、`evidence`。

**迁移和控制需要可观测：** `action_id`、`provider_task_id`、`provider_ref`、`generation`、`migration_stage`、`readable`、`serving_ready`、`replica_count`、`route_epoch`、`error_code`、`observed_at`、`evidence`，以及契约定义的 `freeze_state`、`unfreeze_result`、`cancel_result`。

P2 不一定需要把每个内部执行步骤暴露成独立的 C API，但必须能让 C 查询到迁移阶段、实际结果、是否产生副作用和当前真实 Placement。

---

## 四、状态、幂等和异常收口规则

### 4.1 C 侧 Action 状态

```text
TierAction.Generated
  -> Submitted
  -> Succeeded
  -> Failed
  -> Unknown
```

补充规则：

- P2 的 `ACCEPTED` / `RUNNING` 在 C 侧均先归入 `Submitted`；
- P2 的 `SUCCEEDED` 只能触发“待 Placement 确认”，不能直接完成 C 的成功收口；
- 反馈丢失、P2 重启、Provider Unknown 或副作用不明时进入 `Unknown`；
- `Unknown` 先按原 `action_id` 查询，再读取最新 Placement；
- 原动作未执行且确需重新计划时，创建新的 `action_id`，并关联原动作；
- C 不修改 `current_tier`、`actual_tier`、`generation`、`route_epoch` 和 Provider 任务状态。

### 4.2 反馈丢失和 P2 重启

```text
SubmitTierAction 已发出但响应丢失
  -> C 保留原 action_id 和幂等键
  -> QueryActionStatus 查询原动作
  -> GetPlacement 查询当前真实放置
  -> 比较 Action、Feedback、Placement 和版本
  -> Succeeded / Failed / Unknown
```

P2 重启后，原动作至少应能按 `action_id`、`provider_task_id` 或幂等键查询。C 不因为自身或 P2 重启就重新提交相同动作。

### 4.3 版本冲突和重新计划

```text
expected_generation 与当前 generation 不一致
  -> P2 拒绝提交或返回冲突
  -> C 重新读取 Placement
  -> 使旧 Plan 失效
  -> 重新计算并解析新的 ActuationTarget
  -> 按新 action_id 提交
```

旧反馈迟到时，C 按 `generation`、`mapping_generation`、`target_generation` 和 `route_epoch` 判断其是否仍属于当前计划；不能用到达时间覆盖新事实。

### 4.4 Release / Reclaim 的安全规则

在释放高层或旧副本前，C 至少需要确认：

- 低层 Serving / Authoritative Copy 可读；
- 目标 `generation` 和 `route_epoch` 仍有效；
- 没有有效 Pin 或 Force Keep；
- 没有冲突的 `Submitted` / `Unknown` 动作；
- 目标副本已经完成写入和完整性校验。

Release、Demote 或 Reclaim 失败、反馈丢失或状态未知时，保留原副本，不提前修改 `current_tier`，转入查询和 `ReconciliationTask`。

---

## 五、C-P2 联调计划与责任分工

### 5.1 时间节点

| 时间 | C 组责任输出 | P2 需要提供或确认 | 主要责任人 |
|---|---|---|---|
| 2026-09-18 | 完成 C-P2 字段、状态、幂等和证据口径冻结 | 实际方法、版本、请求/响应样例、错误和测试环境清单 | C：沈家隆 / 谭旭梁；P2 公共底座：刘佳正 / 陈晔；P2 E1：张晋 / 胡孝阳；P2 E2：陈晔 / 杨文博；P2 可观测：杨文博 / 王广诚 |
| 2026-10-16 | 完成输入消费、热度、Plan、动作状态机和模拟器核心路径 | 提供 Placement、ResourceState、Task 查询的可运行基线 | C：沈家隆 / 谭旭梁；P2 公共底座和平台接入按功能负责人协作 |
| 2026-11-06 | 冻结调度输入、容量保护、准入、重试和对账规则 | 明确代次、层级、迁移阶段、控制能力和限额语义 | C：沈家隆 / 谭旭梁；P2 公共底座：刘佳正 / 陈晔；P2 E2：陈晔 / 杨文博；P2 平台接入：高琛 / 高旭 |
| 2026-11-13 | 完成 C-P2 提交、查询、反馈和人工控制接口核对，进入 RC | 提供 RC 接口版本、幂等窗口、重启查询和反馈恢复证据 | C：沈家隆 / 谭旭梁；P2 可观测与验收：杨文博 / 王广诚 |
| 2026-11-20 | C 组功能冻结，形成接口证据、回退方案和已知限制 | 冻结 P2 侧接口和异常语义，新增内容只进入缺陷修复 | C：沈家隆 / 谭旭梁；P2 各功能负责人 |
| 2026-11-23～11-27 | 完成真实 P2 联调、Unknown 对账、重启恢复、容量保护、性能和端到端验收 | 提供真实或明确标注的测试环境、Provider 反馈、Placement 观察和故障注入能力 | C：沈家隆 / 谭旭梁；环境搭建与验收：陈凯 / 肖宇；P2 各功能负责人 |
| 2026-11-28～11-30 | 完成缺陷复验、证据归档和 MVP 交付 | 关闭 P2 侧阻塞问题并确认最终接口版本 | C：沈家隆 / 谭旭梁；P2 各功能负责人 |

### 5.2 P2 对接材料清单

P2 在契约冻结和 RC 冻结节点至少需要提供：

1. 8 项逻辑能力对应的实际方法名称和契约版本；
2. 请求、响应和错误样例；
3. 每个字段的来源、转换规则和缺失处理；
4. `representation_id`、`provider_ref`、`target_id` 和各类代次的关联规则；
5. `action_id`、`provider_task_id`、幂等键的关联方式和保留时间；
6. `Accepted`、`Running`、`Succeeded`、`Failed`、`Unknown` 的状态、阶段和副作用语义；
7. 反馈回调、事件流或轮询查询的恢复方式；
8. P2 重启后动作和状态的查询方式；
9. 过载、超时、取消、冻结、解冻、Provider 不可用和 Unknown 的可重复用例；
10. 真实 Provider 环境、模拟环境和两者证据差异的说明；
11. Recall 读取来源与 Placement 归因字段的支持范围；
12. 在线读取不隐式创建 C `TierAction` 的契约声明。

---

## 六、C 组最终答复结论

1. C 接受 P2 当前“基础能力存在、接口契约尚不完整”的评估，不要求 P2 暴露 Provider 内部实现，但要求提供稳定的抽象接口和可验证的真实状态。
2. C 负责热度、PlacementPlan、调度意图、TierAction、准入、异常处理和调度域业务对账；P2 / Provider 负责目标解析、物理执行、真实 Placement、资源状态和执行反馈。
3. `ActuationTarget` 必须由 P2 解析为不透明目标；C 不直接从 Plan 调用 Provider-specific action。
4. `GetPlacement` 和 `QueryActionStatus` 可以共用承载服务，但不能混淆“对象当前事实”和“某次动作进展”两类语义。
5. `Accepted`、`Running` 不等于成功；C 必须用 `ExecutionFeedback + GetPlacement` 完成最终收口。
6. `Prewarm`、`Prefetch`、`Promote` 和 `Release / Reclaim` 必须在触发方、预算、动作记录和结果证据上分开；在线读取不产生 C 的调度动作。
7. 当前回应仍有待 P2 签收的事项：实际 API 和版本、代次规则、反馈方式、控制能力、幂等保留期、错误副作用和真实联调环境。以上事项在 2026-09-18、2026-11-06 和 2026-11-13 三个节点逐步冻结，2026-11-23～11-27 完成真实联调，2026-11-30 交付 MVP。

**C 组签收口径：** 只有当 P2 能按原动作查询、返回真实放置和版本证据、说明未知状态的副作用，并通过反馈丢失与重启恢复用例后，C 才认为 C-P2 调度闭环具备可验收条件。
