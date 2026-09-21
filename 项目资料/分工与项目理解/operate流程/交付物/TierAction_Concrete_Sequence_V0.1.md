# AetherStore P3 C 组交付文档

本文档是 C 组 Operate / Optimize 工作包的独立交付物，来源于《AetherStore P3 C 组设计方案 V0.1》。C 组负责调度控制面，不拥有 Memory 主事实，不实现底层存储和物理迁移。

统一责任边界如下：C 组消费运行事实，生成计划和动作，接收执行反馈，刷新真实观察并完成对账；P2 / Provider 提供真实层级、资源和物理执行结果。

本文档只保留本交付物相关内容。涉及 P2 执行方、接口字段和资源配额的部分，在尚未冻结的地方明确标记为待确认。

## 交付物说明

C-03 TierAction 运行时序列

## 交付边界

本交付物描述从 Plan 到 TierAction、P2 提交、Provider 执行、Feedback、Placement 刷新和结果收口的顺序。Copy / Verify / Cutover / Reclaim 属于 P2 / Provider 内部，不在 C 侧复制。

## C-03 TierAction 运行时序列

### C 侧流程

```text
输入事实
  -> 去重、版本和新鲜度校验
  -> 计算 per-representation Hotness
  -> 生成 RepresentationPlacementPlan
  -> 重新读取 Placement / Resource
  -> PlanValidityGuard
  -> TierMismatchGuard
  -> Admission / Conflict / Cooldown 检查
  -> Resolve ActuationTarget
  -> TierAction.Generated
  -> Submit
```

当 `desired_tier == observed_tier`、资源事实过期、目标不可解析、已有冲突动作或人工控制阻止动作时，C 记录 `Keep / No-op`，不提交物理动作。

### P2 / Provider 侧流程

```text
TierAction.Submitted
  -> ACCEPTED / RUNNING
  -> Copy
  -> Verify
  -> Cutover
  -> Reclaim
  -> SUCCEEDED / FAILED / UNKNOWN
```

以上物理步骤由 P2 / Provider 拥有。C 只消费统一的执行结果和真实观察，不复制 P2 的内部阶段。

### C 侧收口流程

```text
Feedback / Query
  -> 校验 action_id、provider_ref、generation 和 target_tier
  -> 刷新 Placement Observation
  -> 更新 TierAction
  -> 结果一致则关闭；不一致或未知则创建 ReconciliationTask
```

### TierAction 字段和状态映射

TierAction 至少包含：

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
trace_id
action_state
provider_task_id
provider_status
actual_tier
result_generation
error_code
created_at
submitted_at
completed_at
```

### TierAction 字段含义

| 字段 | 含义 | 使用规则 |
|---|---|---|
| `action_id` | TierAction 的唯一标识 | 贯通提交、反馈、查询、对账和审计 |
| `idempotency_key` | 本次动作的幂等键 | 防止同一物理动作重复提交 |
| `plan_id` | 生成该动作的 Plan 标识 | 关联调度依据 |
| `representation_id` | 动作对应的 Representation 标识 | 锁定 C 的调度对象 |
| `provider_ref` | Provider 中的稳定对象引用 | 由 P2 / Provider 提供，C 不解析内部结构 |
| `actuation_target` | 本次提交使用的不透明执行目标 | C 只保存和转发，不拼装 Provider 专属命令 |
| `action_type` | 要执行的调度动作 | 例如 `Promote`、`Demote`、`Prefetch`、`Release` 或 `Pin` |
| `source_tier` | 动作开始前的层级 | 用于和最新 Placement 对照 |
| `desired_tier` | 动作希望达到的层级 | 用于判断动作是否达到目标 |
| `expected_generation` | C 提交动作时预期的对象版本 | 与 P2 返回的开始版本校验 |
| `policy_version` | 生成动作时使用的策略版本 | 用于复现决策和审计 |
| `priority` | 动作的调度优先级 | 用于执行侧排序或准入 |
| `deadline` | 动作的最晚有效时间 | 超过后需要重新评估，不应直接执行旧动作 |
| `trace_id` | 关联技术调用链的标识 | 用于跨组件排障 |
| `action_state` | C 侧动作状态 | 允许值和转换见下方状态映射 |
| `provider_task_id` | Provider 创建的执行任务标识 | 用于查询实际执行状态 |
| `provider_status` | Provider 返回的执行状态 | 用于映射 C 侧动作状态 |
| `actual_tier` | Provider 观测到的实际层级 | 成功收口时必须与目标一致 |
| `result_generation` | 动作完成后的对象版本 | 与 Placement Observation 对照 |
| `error_code` | 执行失败或异常的原因标识 | 用于分类处理和判断是否可重试 |
| `created_at` | 动作生成时间 | 记录 C 形成动作的时间 |
| `submitted_at` | 动作提交时间 | 记录交给执行侧的时间 |
| `completed_at` | 动作收口时间 | 仅在进入终态或明确收口时记录 |

状态映射固定为：

| Provider 结果 | C 的 `ActionState` | 进入条件 |
|---|---|---|
| `ACCEPTED` / `RUNNING` | `Submitted` | Provider 已接受或正在处理 |
| `SUCCEEDED` | `Succeeded` | `actual_tier` 正确，目标和对象可关联，generation 校验通过 |
| `FAILED` | `Failed` | Provider 明确报告失败，或对账确认未执行 |
| `UNKNOWN`、超时、反馈丢失 | `Unknown` | 当前不能判断物理结果 |

`Accepted` 不等于成功。若 P2 的迁移会改变 generation，P2 必须同时返回 `pre_generation` 和 `post_generation`；C 校验动作开始时的 `pre_generation == expected_generation`，不能把动作后的版本盲目当成旧版本比较。

### 建议接口

| 接口 | 主要输入 | 主要输出 |
|---|---|---|
| `ResolveActuationTarget` | `representation_id`、目标层级、期望 generation | `ActuationTarget`、支持动作、有效期 |
| `GetPlacement` | `provider_ref` 或 `target_id` | `current_tier`、`generation`、`route_epoch`、`observed_at` |
| `GetResourceState` | `resource_scope_id` | 容量、预算、压力、迁移和健康状态 |
| `SubmitTierAction` | `TierAction`、幂等键 | `provider_task_id`、`ACCEPTED` / `RUNNING` |
| `QueryActionStatus` | `action_id` 或 `provider_task_id` | Provider 状态、实际层级、版本、错误码 |
| `WatchExecutionFeedback` | `action_id`、反馈游标 | 增量反馈，支持断点续传 |

P2 / Provider 负责动作的直接执行，C 仍使用同一组抽象接口；P2 同时提供状态、Freeze/Unfreeze 和迁移回调。

## C 与 P2 的联调交接

### 当前推荐对接路径

现有材料显示，P2 的 `SegmentControl` 负责状态暴露、`Freeze`、`Unfreeze` 和迁移完成/失败回调；`Promote`、`Demote`、`Prefetch` 或 `Release` 可由 P2 内部执行组件或其 Provider 承接。统一建议采用以下路径：

```text
C -> P2：读取 Placement、Generation、容量和健康状态
C -> P2：必要时发送 Freeze / Unfreeze
C -> P2 / Provider：提交 Promote、Demote、Prefetch 或 Release
P2 / Provider -> C：报告迁移完成或失败，并返回 ExecutionFeedback 和最新 Placement Observation
```

P2 需要提供统一的 `SubmitTierAction`、`QueryActionStatus` 和 `GetPlacement` 能力。C 对同一个物理动作只向 P2 提交一次，P2 负责执行并返回事实或回调。

### C 提交和接收的最小字段

C 向执行侧提交：

```text
action_id
idempotency_key
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
trace_id
```

执行侧返回：

```text
provider_status
provider_task_id
actual_tier
pre_generation
post_generation
route_epoch
migration_stage
completion_time
error_code
retryable
observed_at
```

### 执行侧反馈字段含义

| 字段 | 含义 | 使用规则 |
|---|---|---|
| `provider_status` | Provider 当前执行状态 | 推进 `Submitted` 或进入终态；不能单独替代 Placement 校验 |
| `provider_task_id` | Provider 执行任务标识 | 反馈丢失时用于查询 |
| `actual_tier` | 当前实际层级 | 成功收口时必须与 `desired_tier` 一致 |
| `pre_generation` | 动作开始前的对象版本 | 必须与 `expected_generation` 对照 |
| `post_generation` | 动作完成后的对象版本 | 用于确认版本变化和后续对账 |
| `route_epoch` | 动作完成时的路由版本 | 用于确认路由切换是否匹配 |
| `migration_stage` | 物理执行阶段 | 允许取值：`copy`、`verify`、`cutover`、`reclaim`；不作为 C 的业务状态 |
| `completion_time` | Provider 认为动作完成的时间 | 与 C 的收口时间和观测时间区分 |
| `error_code` | Provider 返回的错误原因 | 失败或未知时用于分类处理 |
| `retryable` | Provider 对是否可重试的判断 | 只能作为建议，C 仍须先对账 |
| `observed_at` | 反馈被观测的时间 | 用于判断反馈新鲜度 |

联调前必须共同确认以下事项：

1. `Promote`、`Demote`、`Prefetch`、`Release` 在 P2 内部由哪个执行组件承接，以及是否通过独立 Provider 完成。
2. MVP 调度粒度是 Representation、Segment、bucket 还是对象级目标。
3. `ResolveActuationTarget` 的稳定字段、有效期和失效条件。
4. `Placement Observation`、`ResourceState` 和 `ExecutionFeedback` 的完整字段、版本和新鲜度阈值。
5. `generation`、`route_epoch`、`action_id` 和幂等键的关联规则。
6. P2 是否提供富状态接口，至少包含 `actual_tier`、容量、迁移状态和 `observed_at`。
7. P2 的 `SegmentStats` 若缺少 `tier` 或 `hit_rate`，由哪个接口补充，C 不得自行推断。
8. P2 是直接接收 `TierAction`，还是只参与 Freeze、迁移完成和失败回调。

C 组最终应以以下闭环作为联调验收标准：

```text
读取真实状态
  -> 生成 RepresentationPlacementPlan
  -> 提交唯一 TierAction
  -> 确认 Feedback
  -> 刷新 Placement Observation
  -> 对账并处理异常
```
