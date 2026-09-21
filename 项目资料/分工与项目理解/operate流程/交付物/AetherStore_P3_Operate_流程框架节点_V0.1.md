# operate流程框架节点

版本：V0.1  
整理日期：2026-09-14  
维护方：P3 C 组 / Operate  
对接方：P2 / Provider

本文按 Recall 接口对接文档的总览格式，整理 Operate 与 P2 的核心流程节点。接口内容以现有 C-P2 对接需求、P2/C 调度接口清单和 C 组项目基线为准。

## 核心接口总览

Operate 的目标是把 `MemorySignal`、`AccessTrace`、真实 `Placement` 和 `ResourceState` 转换为可执行的调度动作，并通过反馈、最新放置和对账确认动作是否真正完成。

| 所属流程 | 核心接口及原编号 | 功能 | 我们提供的核心输入 | 期望 P2 返回的数据 | 期望p2实现时间 |
|---|---|---|---|---|---|
| Operate | [实际放置查询 GetPlacement（CP2-01）](#cp2-01-getplacement) | 查询 representation 当前真实的 P3 逻辑层级、版本、路由和可读性，作为计划前与动作后的事实依据 | `PlacementQueryInput`：`representation_id`、`provider_ref` / `target_id`（已知时）、`memory_id` / `memory_version`（适用时）、`mapping_generation`、`request_id`、`trace_id` | `PlacementObservation`：`current_tier`、`provider_ref`、`generation`、`route_epoch`、`readable`、`serving_ready`、`replica_count`、`supported_operations`、`observed_at`、`evidence` | 2026.09.30  状态读取基线 |
| Operate | [资源状态查询 GetResourceState（CP2-02）](#cp2-02-getresourcestate) | 判断容量、健康、压力、预算、并发和带宽是否允许新的后台调度动作 | `ResourceStateQueryInput`：`resource_scope_id`、`provider_name`（适用时）、`request_id`、`trace_id` | `ResourceState`：`freshness`、`backend_health`、容量使用、压力、Working/Prewarm/Pin 预算、迁移并发、带宽、读写 P99、`observed_at` | 2026.09.30  资源准入基线 |
| Operate | [执行目标解析 ResolveActuationTarget（CP2-03）](#cp2-03-resolveactuationtarget) | 将 representation 与目标层级解析为 P2 可执行的不透明 `ActuationTarget`，C 不解析 Provider 内部结构 | `ActuationTargetResolveInput`：`representation_id`、`desired_tier`、`mapping_generation` / `target_generation`（已知时）、`plan_id`、`expected_route_epoch`、`valid_until`、`trace_id` | `ActuationTarget`：`target_id`、`provider_ref`、`source_tier`、`supported_operations`、`generation`、`target_generation`、`route_epoch`、有效期和冲突证据 | 2026.10.05  目标解析联调 |
| Operate | [调度动作提交 SubmitTierAction（CP2-04）](#cp2-04-submittieraction) | 将通过放置、资源、目标和冲突校验的抽象 `TierAction` 提交给 P2，保证幂等和版本冲突可识别 | `SubmitTierActionInput`：`action_id`、`idempotency_key`、`plan_id`、`representation_id`、`provider_ref`、`actuation_target`、`action_type`、源/目标层级、版本与路由约束、`policy_version`、`deadline`、`trace_id` | `SubmitResult`：`provider_task_id`、`provider_status`、`effect_status`、`side_effect`、`failure_stage`、`observed_at`、`error_code`、`retryable`、`evidence` | 2026.10.15  动作提交联调 |
| Operate | [动作状态查询 QueryActionStatus（CP2-05）](#cp2-05-queryactionstatus) | 在超时、反馈丢失、C/P2 重启或 `Unknown` 时按原动作查询真实结果，避免盲目重复提交 | `ActionStatusQueryInput`：`action_id`、`provider_task_id` / `idempotency_key`（已知时）、`request_id`、`trace_id` | `ActionStatus`：真实执行状态、`actual_tier`、前后版本、`route_epoch`、迁移阶段、效果/副作用、可读性、副本数、错误、重试建议、`observed_at`、`evidence` | 2026.10.20  原动作恢复 |
| Operate | [执行反馈 WatchExecutionFeedback（CP2-06）](#cp2-06-watchexecutionfeedback) | 提供动作执行期间和完成后的增量反馈，支持去重、断点续传和查询兜底 | `FeedbackWatchInput`：`action_id`、`feedback_cursor`（断点续传时）、`consumer_id`、`request_id`、`trace_id` | `ExecutionFeedback`：`feedback_id`、`action_id`、`provider_task_id`、`provider_status`、`actual_tier`、版本、路由、迁移阶段、效果/副作用、错误、`next_feedback_cursor`、`evidence` | 2026.10.20  反馈闭环 |
| Operate | [物理迁移执行 Copy/Verify/Cutover/Reclaim（CP2-07）](#cp2-07-physical-execution) | 由 P2/Provider 内部承接复制、校验、路由切换和旧副本回收，并向 C 暴露可核验阶段和副作用 | C 提交的抽象 `TierAction`、`ActuationTarget`、目标层级、版本与路由约束；不直接提交 Provider-specific 命令 | 迁移阶段、复制/校验/切换/回收结果、实际层级、版本、路由、可读性、服务就绪、副本数、效果/副作用和证据 | 2026.10.30  调度能力完成 |
| Operate | [冻结、解冻与取消 Freeze/Unfreeze/Cancel（CP2-08）](#cp2-08-freeze-unfreeze-cancel) | 按动作阶段提供一致性保护和安全控制，并明确不支持的动作、阶段和取消结果 | `action_id`、`provider_task_id`、`provider_ref`、`generation`、控制类型、原因、期望阶段、`request_id`、`trace_id` | `freeze_state`、`unfreeze_result`、`cancel_result`、当前层级、版本、路由、副作用、观测时间、错误和支持范围 | 2026.10.30  控制能力完成 |

> 时间列沿用 C 组项目基线，仅作 P2 联调排期参考，不代表 P2 已确认的正式承诺。正式日期以 P2 签收的接口契约和联调计划为准。

## 文档定位与责任边界

本文只整理 C/Operate 与 P2/Provider 的调度接口节点，不复制 Recall 的搜索、正文读取和在线 `Prewarm` 接口，也不冻结具体 SDK、RPC、HTTP 方法名、序列化格式或数据库结构。

### C / Operate 负责

- 消费 `MemorySignal`、`AccessTrace`、`PolicyContext` 和 P2 返回的真实观察；
- 判断为什么调度、什么时候调度、对哪个 `representation_id` 调度；
- 生成 `RepresentationPlacementPlan` 和抽象 `TierAction`；
- 处理版本冲突、反馈丢失、`Unknown`、冷却、准入和对账；
- 根据 `ExecutionFeedback` 与最新 `PlacementObservation` 完成动作收口。

### P2 / Provider 负责

- 将抽象目标解析为不透明的 `ActuationTarget`；
- 执行 `Copy`、`Verify`、`Cutover`、`Reclaim` 等物理步骤；
- 返回真实层级、对象版本、路由、资源和副本事实；
- 返回执行进度、最终结果、物理副作用和失败原因；
- 在 P2 或 Provider 重启后仍能按原动作查询和恢复。

## Operate 主流程

```text
MemorySignal / AccessTrace / PolicyContext
        ↓
读取真实 Placement 和 ResourceState
        ↓
C 生成或更新 RepresentationPlacementPlan
        ↓
ResolveActuationTarget
        ↓
C 生成 TierAction
        ↓
SubmitTierAction
        ↓
P2 / Provider 执行物理动作
        ↓
WatchExecutionFeedback / QueryActionStatus
        ↓
重新读取 Placement
        ↓
C 完成 Action 收口与 Reconciliation
```

必须区分两类查询：

```text
QueryActionStatus：这一次 action 执行到了哪里？
GetPlacement：这个对象当前真实处于什么状态？
```

`QueryActionStatus` 必须绑定原 `action_id` 或 `provider_task_id`；`GetPlacement` 必须返回当前真实 `current_tier`、`generation` 和 `route_epoch`。C 不能只凭 Action 状态代替最新 Placement。

## CP2-01 GetPlacement

### 功能与调用时机

`GetPlacement` 用于确认某个 `representation_id` 当前按 P3 定义属于哪个逻辑层级、对应哪个真实对象、是什么版本、路由是否发生变化，以及当前是否仍可读。

以下场景必须调用或重新调用：

- 生成新的 `PlacementPlan` 前；
- 提交动作前发现旧观察可能过期；
- 收到 `SUCCEEDED` 反馈后；
- `TierAction` 进入 `Unknown` 后；
- 执行 `Demote`、`Release` 或 `Reclaim` 前；
- P2 重启、路由变化或对账时。

逻辑签名：`GetPlacement(input) → PlacementObservation`。

### C 提供的核心输入

| 字段 | 含义 | 使用规则 |
|---|---|---|
| `representation_id` | C 的抽象调度对象标识 | 必填，用于定位表示对象 |
| `provider_ref` | 已知的 Provider 稳定引用 | 已知时提供，用于精确定位 |
| `target_id` | 已解析的不透明目标 | 已知时提供，用于缩小查询范围 |
| `mapping_generation` | 表示到 Provider 对象的映射版本 | 已知时提供，用于检查映射是否仍有效 |
| `memory_id` / `memory_version` | 业务记忆及其版本 | 适用时提供，不能替代 Provider `generation` |
| `request_id` / `trace_id` | 请求和调用链标识 | 用于关联日志、反馈和审计 |

### 期望 P2 返回的数据

| 字段 | C 的使用方式 |
|---|---|
| `observation_id` | 去重、审计和对账 |
| `representation_id` | 校验被观测对象一致 |
| `provider_ref` / `provider_name` | 关联真实对象和 Provider |
| `current_tier` | 与 Plan 的 `desired_tier` 比较；表示 P3 逻辑层级，不是物理介质名 |
| `mapping_generation` / `generation` | 防止旧观察覆盖新事实 |
| `target_generation` | 记录当前目标代次，适用时返回 |
| `route_epoch` | 判断读取路由或切换是否发生 |
| `readable` / `serving_ready` | 判断副本是否可以读取或服务 |
| `replica_count` | 判断释放后是否仍有安全副本 |
| `supported_operations` | 判断当前是否支持所需动作 |
| `observed_at` / `state_version` | 判断新鲜度并处理乱序 |
| `error_code` / `evidence` | 说明查询失败、冲突或事实来源 |

### 结果判断与异常

| P2 返回情况 | C 的处理 |
|---|---|
| 对象不存在 | 检查映射是否过期，不自行创建新的物理引用 |
| `current_tier`、`generation` 或 `observed_at` 缺失 | 不用于新的非 `Keep` 动作，重新查询或等待补齐 |
| 观察版本低于 C 已保存版本 | 丢弃旧观察，不覆盖当前事实 |
| Provider 暂时不可用 | 保留旧事实但标记过期，不提交新的非 `Keep` 动作 |

## CP2-02 GetResourceState

### 功能与调用时机

`GetResourceState` 用于判断 P2 当前是否有能力接收和执行新的后台动作。C 不根据自己已经提交的动作反推资源状态。

逻辑签名：`GetResourceState(input) → ResourceState`。

### C 提供的核心输入

`resource_scope_id`、`provider_name`（适用时）、`request_id`、`trace_id`。

### 期望 P2 返回的数据

| 数据组 | 关键字段 |
|---|---|
| 新鲜度与健康 | `state_version`、`observed_at`、`freshness`、`backend_health` |
| 容量与压力 | `total_capacity_bytes`、`used_capacity_bytes`、`available_capacity_bytes`、`pressure_ratio` |
| 预算保护 | `working_reserved_capacity_bytes`、`prewarm_budget_bytes`、`prewarm_used_bytes`、`pin_budget_bytes`、`pin_used_bytes` |
| 迁移能力 | `active_migration_count`、`max_concurrent_migration`、`migration_bandwidth_bytes_per_sec`、`migration_bandwidth_budget_bytes_per_sec` |
| 在线影响 | `read_latency_p99_ms`、`write_latency_p99_ms` |

当 `freshness` 为 `STALE` 或 `UNKNOWN`，或者 `backend_health` 不是 `READY` 时，C 默认只允许查询、对账和 `Keep / No-op`，不提交新的 `Promote`、`Demote`、`Prefetch` 或 `Release`。

## CP2-03 ResolveActuationTarget

### 功能与调用时机

`ResolveActuationTarget` 把 C 的抽象 `representation_id` 和目标层级解析为可以提交给 P2 的不透明执行目标。C 不需要知道 P2 内部使用的是 Segment、Object、Shard、Cache Key 还是物理路径。

逻辑签名：`ResolveActuationTarget(input) → ActuationTarget`。

### C 提供的核心输入

`representation_id`、`desired_tier`、`mapping_generation`（已知时）、`target_generation`（复用旧目标或校验时）、`plan_id`、`expected_route_epoch`（有路由约束时）、`valid_until`、`request_id`、`trace_id`。

### 期望 P2 返回的数据

| 字段 | C 的使用方式 |
|---|---|
| `target_id` | 保存和转发，不解析内容 |
| `representation_id` | 校验目标对象一致 |
| `provider_ref` / `provider_name` | 关联真实 Provider 对象 |
| `source_tier` | 记录解析时真实源层级 |
| `supported_operations` | 判断目标是否支持所需动作 |
| `mapping_generation` / `generation` | 与计划版本核对 |
| `target_generation` / `route_epoch` | 作为动作提交时的版本和路由约束 |
| `resolved_at` / `valid_until` | 判断解析结果新鲜度和有效期 |
| `error_code` / `evidence` | 说明目标不存在、过期或冲突原因 |

目标解析成功只代表得到可提交的目标，不代表物理动作已经执行成功。目标过期、映射变化、对象版本不一致或不支持目标动作时，P2 必须明确拒绝或返回冲突，不能静默指向另一个物理对象。

## CP2-04 SubmitTierAction

### 功能与调用时机

只有 Placement、ResourceState、ActuationTarget 和冲突检查全部通过后，C 才提交 `TierAction`。P2 接收的是 C 的抽象动作，不是 Provider 专属命令。

逻辑签名：`SubmitTierAction(input) → SubmitResult`。

### C 提供的核心输入

`action_id`、`idempotency_key`、`plan_id`、`representation_id`、`provider_ref`、`actuation_target`、`action_type`、`source_tier`、`desired_tier`、`expected_generation`、`expected_route_epoch`（适用时）、`policy_version`、`priority`（适用时）、`deadline`（适用时）、`request_id`、`trace_id`。

### 期望 P2 返回的数据

| 字段 | C 的处理 |
|---|---|
| `action_id` | 必须与请求一致，不能被替换 |
| `provider_task_id` | 后续查询和反馈关联 |
| `provider_status` | 映射为已受理、执行中或提交失败 |
| `effect_status` / `side_effect` | 判断提交阶段是否已经产生物理副作用 |
| `failure_stage` | 区分提交、Copy、Verify、Cutover 或 Reclaim 阶段 |
| `observed_at` | 判断接收结果新鲜度 |
| `error_code` / `retryable` | 分类冲突、过载、等待和重计划原因 |
| `evidence` | 支持提交结果的证据引用 |

同一 `action_id` 或相同幂等键重复提交时，P2 应返回原动作事实，不得产生第二次物理执行。相同幂等键但载荷不同必须返回冲突。`ACCEPTED` 或 `RUNNING` 只表示 P2 已接收或开始处理，不表示目标层级已经达到。

## CP2-05 QueryActionStatus

### 功能与调用时机

`QueryActionStatus` 用于反馈丢失、请求超时、C 或 P2 重启以及 Provider 返回 `Unknown` 的场景。

逻辑签名：`QueryActionStatus(input) → ActionStatus / ExecutionFeedback`。

### C 提供的核心输入

`action_id`、`provider_task_id`（已知时）、`idempotency_key`（适用时）、`request_id`、`trace_id`。

### 期望 P2 返回的数据

`provider_status`、`actual_tier`（适用时）、`pre_generation`、`post_generation`（适用时）、`route_epoch`、`migration_stage`、`effect_status`、`side_effect`、`failure_stage`、`readable`、`serving_ready`、`replica_count`、`completion_time`、`error_code`、`retryable`、`observed_at`、`evidence`。

### 结果判断

| 查询结果 | C 的处理 |
|---|---|
| 已达到目标，且对象、版本和路由匹配 | 重新读取 Placement 后，原 Action 收口为 `Succeeded` |
| 明确未执行 | 依据最新 Plan 创建新的 `action_id` |
| 明确执行失败 | Action 收口为 `Failed`，按策略判断是否重新计划 |
| 仍在执行 | 保持 `Submitted`，继续等待反馈 |
| 结果仍未知 | 创建或继续 `ReconciliationTask`，不能盲重试 |
| 普通 `not_found` | 不能直接解释为“未执行”，需按 P2 查询契约判断 |

“任务存在”“任务已受理”或“任务已结束”都不能单独代替当前 Placement。

## CP2-06 WatchExecutionFeedback

### 功能与调用时机

P2 应支持回调、事件流或可断点续传的反馈读取能力，使 C 能获得动作进度和最终结果；MVP 可以没有独立事件流，但必须有 CP2-05 轮询等价能力。

逻辑签名：`WatchExecutionFeedback(input) → ExecutionFeedback`。

### C 提供的核心输入

`action_id`、`feedback_cursor`（断点续传时）、`consumer_id`（适用时）、`request_id`、`trace_id`。

### 期望 P2 返回的数据

`feedback_id`、`action_id`、`provider_task_id`、`representation_id`、`provider_ref`、`provider_status`、`actual_tier`（适用时）、`mapping_generation`、`target_generation`、`pre_generation`、`post_generation`（适用时）、`expected_route_epoch`、`route_epoch`、`migration_stage`、`effect_status`、`side_effect`、`failure_stage`、`readable`、`serving_ready`、`replica_count`、`completion_time`（适用时）、`error_code`、`retryable`、`observed_at`、`next_feedback_cursor`（适用时）、`evidence`。

### 结果判断

- `ACCEPTED` / `RUNNING`：C 的 Action 保持 `Submitted`；
- `SUCCEEDED`：仍需调用 CP2-01，核验层级、对象、版本和路由；
- `FAILED`：只有在明确无物理副作用或已完成对账后才能收口；
- 反馈丢失、超时或结果无法确认：进入 `Unknown`，调用 CP2-05 并对账；
- 重复反馈：按 `feedback_id` 或 `action_id` 去重，不重复执行物理动作。

## CP2-07 Physical Execution

CP2-07 不是要求 C 直接调用的接口，而是 P2 / Provider 必须承接的内部执行能力。C 只提交抽象 `TierAction`，P2 负责将其转换为实际 Provider 的物理步骤。

| 物理阶段 | P2 应完成的事情 | 对 C 的可见结果 |
|---|---|---|
| `Copy` | 建立目标副本或目标层数据 | 目标副本是否建立，版本是否匹配 |
| `Verify` | 校验目标副本完整性和可读性 | 校验结果、`readable`、证据 |
| `Cutover` | 在满足条件后切换读取或服务路由 | `route_epoch` 是否变化，服务副本是否就绪 |
| `Reclaim` | 在目标安全可用后回收旧副本 | 副本数量、释放副作用和剩余安全副本 |

P2 必须通过 CP2-01、CP2-05 或 CP2-06 让 C 能观察迁移阶段、实际结果、副作用和最新 Placement。

## CP2-08 Freeze Unfreeze Cancel

CP2-08 的支持范围必须由 P2 明确，不要求所有动作、所有阶段都无条件支持冻结、解冻或取消。

| 能力 | P2 必须明确的内容 | 关联字段 |
|---|---|---|
| `Freeze` | 冻结对象、目标、写入或路由中的哪一层，以及从哪个阶段生效 | `action_id`、`provider_ref`、`generation`、`freeze_state` |
| `Unfreeze` | 成功、失败、取消和重启后何时恢复 | `action_id`、`unfreeze_result`、当前状态 |
| `Cancel` | 在 Copy、Verify、Cutover、Reclaim 哪些阶段有效，以及取消后是否仍有副作用 | `action_id`、`provider_task_id`、`cancel_result`、`side_effect` |

如果本期不支持某项控制，P2 必须返回明确的不支持结果和当前真实状态，不能让 C 误判为已冻结或已取消。

## 统一状态与异常规则

### TierAction 状态

```text
TierAction.Generated
  -> Submitted
     -> Succeeded
     -> Failed
     -> Unknown

Unknown
  -> QueryActionStatus / Reconciliation
     -> Succeeded
     -> Failed
     -> Replan
     -> Unknown
```

- `ACCEPTED` / `RUNNING` 只对应 C 的 `Submitted`；
- `SUCCEEDED` 必须结合 CP2-01 的真实 Placement 才能收口；
- `FAILED` 只有在明确没有物理副作用或完成对账后才能收口；
- `Unknown` 不是终态，必须先查询原动作和最新 Placement；
- 需要重新执行时必须创建新的 `action_id`，并通过 `retry_of_action_id` 关联原动作；
- C 不修改 `current_tier`、`actual_tier`、`generation` 或 `route_epoch`。

### 版本与路由规则

- `memory_version`、`mapping_generation`、`generation`、`target_generation` 和 `route_epoch` 不能混用；
- C 不自行递增 P2 / Provider 维护的代次；
- `expected_generation` 或目标版本不一致时，P2 拒绝提交或返回冲突；
- `Copy`、`Verify` 不改变 `route_epoch`；
- `Cutover` 只有在路由真正生效并可被 Placement 观察到后才改变 `route_epoch`；
- 旧反馈不能覆盖更新代次后的新 Placement。

### Prewarm 与 Prefetch 的边界

| 项目 | Recall RP2-04 `Prewarm` | C 的 `Prefetch` |
|---|---|---|
| 发起方 | A / Recall | C / Operate |
| 触发时机 | 在线读取当前正文时，获准后尝试一次 | C 根据预测和策略生成后台计划后 |
| 主要目的 | 加速本次读取，尝试使用已有热副本 | 提前准备目标放置，服务后续访问 |
| 是否创建 C 的 `TierAction` | 否 | 是 |
| 结果来源 | 随读取返回来源和放置观察 | `ExecutionFeedback` 加后续 `GetPlacement` |

在线读取命中热副本，不能反推 C 已经执行过 `Prefetch` 或 `Promote`；在线读取不触发 `Promote`、`Prefetch` 或 `Release`。

## 最低联调验收

```text
C 读取 GetPlacement / GetResourceState
  -> C 生成 RepresentationPlacementPlan
  -> C 请求 ResolveActuationTarget
  -> C 提交 SubmitTierAction
  -> P2 / Provider 执行物理动作
  -> C 接收反馈或查询 QueryActionStatus
  -> C 再次调用 GetPlacement
  -> C 按 action_type、版本、路由和副作用完成对账
```

以下情况只能算建议链路或 Mock 验收，不能算真实调度闭环验收：

- C 只生成了 `PlacementPlan` 或 `TierAction`；
- P2 只返回 `Accepted` 或 `Running`；
- P2 返回 `Succeeded`，但没有真实 Placement 和版本证据；
- 反馈丢失后无法按原 `action_id` 查询；
- 结果未知时只能盲目创建新动作；
- C 被要求解析 Provider 内部路径或自行拼接 Provider-specific action。

## 对照文档

| 对照文档 | 本文采用的内容 |
|---|---|
| [C 组与 P2 接口对接需求](../AetherStore_P3_C组与P2接口对接需求_V0.1.md) | C-P2 完整流程、字段语义、异常和恢复规则 |
| [P2/C 调度接口清单](AetherStore_P3_P2-C调度接口清单_V0.1.md) | CP2-01～CP2-08 的接口编号、验收要求和动作关系 |
| [Operate 业务框架节点与 P2 交接说明](AetherStore_P3_Operate_业务框架节点与P2交接说明_V0.1.md) | 六层业务框架、16 个节点和责任边界 |
| [C 组功能详细汇总文档](../C组功能填报/AetherStore_P3_功能详细汇总文档_V1.1.md) | 项目基线日期、C 组 Epic 计划和联调窗口 |

## 一句话总结

Operate 负责“根据事实做出调度决定，并确认调度是否真正完成”；P2 负责“把抽象调度决定转换成真实物理执行，并返回可核验的真实状态”。
