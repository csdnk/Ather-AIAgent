# AetherStore P3 P2/C 调度接口清单

版本：V0.1；编制日期：2026-09-13；提出方：P3 C 组；对接方：AetherEngine P2。

## 1. 文档目的

本文只列出 C 与 P2 为完成真实调度闭环必须对接的接口和能力，作为双方接口契约冻结和联调分工的直接清单。

本文不重新展开 C 提出的 14 个问题，也不替代《AetherEngine P2_P3接口对接需求评估与答复_V1.0.md》或 C 组调度答复文档。本文只定义业务语义和字段含义，不定义具体 Representation 类型，只使用 `representation_id` 作为调度标识；同时不定义字段数据类型、序列化格式、RPC/HTTP 方法名、接口路径或数据库结构。

## 2. 直接结论

P2/C 需要冻结：

- 6 个 C 与 P2 之间的逻辑接口；
- 1 项由 P2 / Provider 内部承接的物理执行能力；
- 1 项按动作阶段启用的冻结、解冻和取消控制能力。

“6 个逻辑接口 + 2 项执行/控制能力”不等于 P2 必须暴露 8 个独立 RPC。P2 可以将多个逻辑接口由同一服务承载，但不能省略对应的业务语义、字段和可验证结果。

## 3. 接口总览

| 编号 | 接口或能力 | 调用方向 | 必须性 | 主要用途 |
|---|---|---|---|---|
| CP2-01 | `GetPlacement` | C → P2 | 必须 | 获取对象当前按 P3 定义的逻辑层级、版本、路由和可读状态 |
| CP2-02 | `GetResourceState` | C → P2 | 必须 | 获取容量、健康、压力、预算和迁移准入事实 |
| CP2-03 | `ResolveActuationTarget` | C → P2 | 必须 | 将 C 的抽象对象和目标层级解析为不透明执行目标 |
| CP2-04 | `SubmitTierAction` | C → P2 | 必须 | 提交 C 生成的抽象调度动作 |
| CP2-05 | `QueryActionStatus` | C → P2 | 必须 | 按原动作查询真实执行状态，支持超时和重启恢复 |
| CP2-06 | `WatchExecutionFeedback` | P2 → C，或 C 读取 P2 反馈 | 必须具备；实现方式可选 | 获取执行进度、结果、副作用和错误证据 |
| CP2-07 | 物理迁移执行 | P2 / Provider 内部 | 必须具备 | 承接 `Copy / Verify / Cutover / Reclaim` 等物理步骤 |
| CP2-08 | `Freeze / Unfreeze / Cancel` | C ↔ P2，按阶段启用 | 必须明确支持边界 | 在迁移或异常场景下提供一致性保护和安全控制 |

## 4. 公共字段

以下字段按场景使用，不要求每个接口都携带全部字段。字段名最终以双方签收的契约为准，但不能删除其业务含义。

| 字段 | 业务含义 | 主要产生方或权威方 |
|---|---|---|
| `request_id` | 一次业务请求标识 | 调用方 |
| `trace_id` | 跨组件调用链标识 | 调用方 |
| `schema_version` | 报文语义版本 | 双方契约 |
| `memory_id` | 业务记忆标识，适用时使用 | Remember / B |
| `memory_version` | 业务记忆版本，适用时使用；不等于 Provider 代次 | Remember / B |
| `representation_id` | C 的抽象调度对象标识 | 表示生产方与 C 共同确认 |
| `provider_ref` | P2 / Provider 侧稳定对象引用 | P2 / Provider |
| `target_id` | P2 返回的不透明执行目标标识 | P2 / Provider |
| `plan_id` | C 生成的放置计划标识 | C |
| `action_id` | C 生成的一次调度动作标识 | C |
| `provider_task_id` | P2 / Provider 执行任务标识 | P2 / Provider |
| `idempotency_key` | 防止同一动作重复执行 | C 生成，P2 保证幂等 |
| `mapping_generation` | 抽象表示到 Provider 对象的映射版本 | 映射权威方产生，P2 回传或校验 |
| `generation` | Provider 对象或内容的实际代次 | P2 / Provider |
| `target_generation` | `ActuationTarget` 绑定的目标代次 | P2 / Provider |
| `expected_generation` | C 提交动作时预期的对象代次 | C 发送，P2 校验 |
| `expected_route_epoch` | C 提交动作时预期的路由代次 | C 发送，P2 校验 |
| `route_epoch` | 当前或动作完成时的路由切换代次 | P2 / Provider |
| `observed_at` | P2 观察或反馈产生的时间 | P2 / Provider |
| `error_code` | 失败、冲突或未知原因 | P2 / Provider |
| `retryable` | P2 对等待或重试的建议 | P2 / Provider |
| `evidence` | 支持状态结论的证据引用 | P2 / Provider |

## 5. CP2-01：GetPlacement

### 5.1 用途

回答：“这个 `representation_id` 当前按 P3 定义属于哪个逻辑层级、对应哪个真实对象、是什么版本、是否可读、是否可以继续调度？”

### 5.2 调用时机

- 生成新的 `PlacementPlan` 前；
- 提交动作前发现原观察可能过期时；
- 收到 `SUCCEEDED` 反馈后；
- `TierAction` 进入 `Unknown` 后；
- 执行 `Demote`、`Release` 或 `Reclaim` 前；
- P2 重启、路由变化或对账时。

### 5.3 C 发送

`memory_id`、`memory_version`（适用时）、`representation_id`、`provider_ref`（已知时）、`target_id`（已知时）、`mapping_generation`（已知时）、`request_id`、`trace_id`。

### 5.4 P2 返回

| 字段 | C 的用途 |
|---|---|
| `observation_id` | 观测去重和对账 |
| `memory_id`、`memory_version` | 关联业务记忆，适用时返回 |
| `representation_id` | 校验被观测对象一致 |
| `provider_ref`、`provider_name` | 识别承载该逻辑层级的真实对象和 P2 / Provider |
| `current_tier` | 获取按 P3 定义的当前逻辑层级，例如冷层、温层或热层；不直接表示 Redis、Ceph、磁盘等物理介质 |
| `mapping_generation` | 判断映射是否仍对应当前表示 |
| `generation` | 防止旧观测覆盖新事实 |
| `target_generation` | 记录当前目标代次，适用时返回 |
| `route_epoch` | 判断读取路由是否变化 |
| `readable` | 判断当前副本是否可读 |
| `serving_ready` | 判断是否可以作为服务副本 |
| `replica_count` | 判断释放后是否仍有可用副本 |
| `supported_operations` | 判断当前允许的动作 |
| `observed_at` | 判断观测新鲜度 |
| `observation_source` | 记录事实来自 P2、Executor 还是 Provider |
| `state_version` | 处理乱序观测 |
| `error_code`、`evidence` | 说明查询失败或观测证据 |

### 5.5 验收要求

P2 必须根据真实物理放置确认并返回 P3 逻辑层级 `current_tier`，同时返回 `provider_name`、`provider_ref`、`generation`、`route_epoch` 和可读性信息作为核验依据。`current_tier` 是 P3 的逻辑层级，不是 P2 的物理介质名称；物理 Provider 和物理对象信息属于支撑该判断的证据，不作为 C 直接依赖的调度层级。C 不接受只返回计划中的 `desired_tier` 或只返回“对象存在”。

## 6. CP2-02：GetResourceState

### 6.1 用途

回答：“当前资源是否允许提交新的后台调度动作？”

### 6.2 C 发送

`resource_scope_id`、`provider_name`（适用时）、`request_id`、`trace_id`。

### 6.3 P2 返回

| 字段 | C 的用途 |
|---|---|
| `resource_scope_id` | 确认资源快照适用范围 |
| `provider_name` | 识别资源来源 |
| `state_version` | 处理乱序资源状态 |
| `observed_at` | 判断资源快照新鲜度 |
| `freshness` | 判断是否允许正常准入 |
| `backend_health` | 判断 Provider 是否可接收动作 |
| `total_capacity_bytes` | 获取资源总量 |
| `used_capacity_bytes` | 获取资源已用量 |
| `available_capacity_bytes` | 判断动作成本是否可容纳 |
| `pressure_ratio` | 判断资源压力和释放优先级 |
| `working_reserved_capacity_bytes` | 保护在线 Working 资源 |
| `prewarm_budget_bytes` | 限制 C 后台预取或预热预算 |
| `prewarm_used_bytes` | 计算后台预取剩余额度 |
| `pin_budget_bytes` | 限制固定驻留预算 |
| `pin_used_bytes` | 计算固定驻留占用 |
| `active_migration_count` | 判断当前迁移压力 |
| `max_concurrent_migration` | 限制迁移并发 |
| `migration_bandwidth_bytes_per_sec` | 估算当前迁移能力 |
| `migration_bandwidth_budget_bytes_per_sec` | 保护在线服务带宽 |
| `read_latency_p99_ms`、`write_latency_p99_ms` | 判断调度对在线读写的影响 |

### 6.4 验收要求

当 `freshness` 为 `STALE` 或 `UNKNOWN`，或者 `backend_health` 不允许接收动作时，C 只能查询、对账或记录 `Keep / No-op`，不得提交新的非保持动作。C 不根据自己已提交的动作反推资源状态。

## 7. CP2-03：ResolveActuationTarget

### 7.1 用途

P2 将 C 的抽象对象和目标层级解析为不透明的 `ActuationTarget`。C 不解析 `target_id`，不把 `provider_ref` 直接当成 `target_id`，也不拼接 Provider-specific action。

### 7.2 C 发送

`memory_id`、`memory_version`（适用时）、`representation_id`、`desired_tier`、`mapping_generation`（已知时）、`plan_id`、`trace_id`、`expected_route_epoch`（计划有路由约束时）、`valid_until`（适用时）。

如果是复用旧目标、重新解析或校验旧目标，C 才携带上一次返回的 `target_generation` 或等价版本约束；首次解析不要求 C 提供 `target_generation`。

### 7.3 P2 返回

| 字段 | C 的用途 |
|---|---|
| `target_id` | 保存和转发的不透明目标 |
| `representation_id` | 校验目标对象一致 |
| `provider_ref`、`provider_name` | 关联真实 Provider 对象 |
| `source_tier` | 记录解析时真实源层级 |
| `supported_operations` | 判断目标支持的动作 |
| `mapping_generation` | 校验表示映射版本 |
| `generation` | 记录解析时的 Provider 对象代次 |
| `target_generation` | 作为动作提交的目标版本约束 |
| `route_epoch` | 判断目标路由是否仍适用 |
| `resolved_at` | 判断解析时间 |
| `valid_until` | 判断目标是否过期 |
| `error_code`、`evidence` | 说明目标不存在、过期或冲突原因 |

### 7.4 验收要求

目标解析成功只代表得到可提交的目标，不代表物理动作已经执行成功。目标过期、映射变化、对象版本不一致或不支持目标动作时，P2 必须明确拒绝或返回冲突，不能静默指向另一个物理对象。

## 8. CP2-04：SubmitTierAction

### 8.1 用途

C 将 `PlacementPlan` 转换出的抽象 `TierAction` 提交给 P2。只有 Placement、ResourceState、目标解析和冲突检查通过后才提交。

### 8.2 C 发送

`memory_id`、`memory_version`（适用时）、`action_id`、`idempotency_key`、`plan_id`、`representation_id`、`provider_ref`、`actuation_target`、`action_type`、`source_tier`、`desired_tier`、`mapping_generation`（已知时）、`target_generation`、`expected_generation`、`expected_route_epoch`（适用时）、`policy_version`、`priority`（适用时）、`deadline`（适用时）、`request_id`、`trace_id`。

### 8.3 P2 返回

| 字段 | C 的用途 |
|---|---|
| `action_id` | 校验原动作未被替换 |
| `provider_task_id` | 后续查询和反馈关联 |
| `provider_status` | 判断已受理、执行中或提交失败 |
| `effect_status` | 提交阶段已知的动作效果 |
| `side_effect` | 判断是否已产生物理副作用；未知时不能默认为无副作用 |
| `failure_stage` | 记录提交、Copy、Verify、Cutover 或 Reclaim 阶段的失败 |
| `observed_at` | 判断返回状态新鲜度 |
| `error_code` | 解释冲突、过载或提交失败 |
| `retryable` | 提供等待或重试建议 |
| `evidence` | 支持提交结果的证据 |

### 8.4 验收要求

同一 `action_id` 或相同幂等键重复提交时，P2 返回原动作事实，不得产生第二次物理执行。相同幂等键但载荷不同必须返回冲突。

`ACCEPTED` 和 `RUNNING` 只表示 P2 已接收或开始处理，不表示目标层级已经达到。

## 9. CP2-05：QueryActionStatus

### 9.1 用途

在反馈丢失、请求超时、C 或 P2 重启、Provider 返回 `Unknown` 时，按原动作查询真实结果，避免重复提交。

### 9.2 C 发送

`action_id`、`provider_task_id`（已知时）、`idempotency_key`（适用时）、`request_id`、`trace_id`。

### 9.3 P2 返回

| 字段 | C 的用途 |
|---|---|
| `action_id`、`provider_task_id` | 关联原动作和 P2 任务 |
| `memory_id`、`memory_version` | 关联业务记忆，适用时返回 |
| `representation_id`、`provider_ref` | 校验动作对象一致 |
| `provider_status` | 获取当前真实执行状态 |
| `actual_tier` | 获取动作实际达到的层级，适用时提供 |
| `mapping_generation` | 校验动作使用的映射版本 |
| `target_generation` | 校验动作绑定的目标代次 |
| `pre_generation` | 获取动作执行前代次 |
| `post_generation` | 获取动作执行后代次，适用时提供 |
| `expected_route_epoch` | 记录提交时的路由约束 |
| `route_epoch` | 校验执行完成时的路由 |
| `migration_stage` | 判断 Copy、Verify、Cutover、Reclaim 进度 |
| `effect_status` | 判断动作实际效果，而不只看任务状态 |
| `side_effect` | 判断是否产生部分或完整物理副作用 |
| `failure_stage` | 判断失败或未知发生在哪个阶段 |
| `readable`、`serving_ready` | 判断副本能否读取或提供服务 |
| `replica_count` | 判断释放后是否仍有可用副本 |
| `completion_time` | 记录 P2 的完成判断时间 |
| `error_code`、`retryable` | 分类失败并决定等待或重计划 |
| `observed_at` | 判断查询结果新鲜度 |
| `evidence` | 支持动作结果和副作用判断 |

### 9.4 验收要求

“任务存在”“任务已受理”或“任务已结束”都不能单独代替当前 Placement。P2 必须使 C 能从原动作查询中判断：仍在执行、明确成功、明确失败、明确未执行，或结果仍未知。

## 10. CP2-06：WatchExecutionFeedback

### 10.1 用途

向 C 提供动作执行期间和完成后的反馈。反馈可以通过回调、事件流或轮询实现，但必须具备可恢复查询能力。

### 10.2 C 提供

`action_id`、`feedback_cursor`（断点续传时提供）、`consumer_id`（适用时）、`request_id`、`trace_id`。

### 10.3 P2 返回

`feedback_id`、`action_id`、`provider_task_id`、`representation_id`、`provider_ref`、`provider_status`、`actual_tier`（适用时）、`mapping_generation`、`target_generation`、`pre_generation`、`post_generation`（适用时）、`expected_route_epoch`、`route_epoch`、`migration_stage`、`effect_status`、`side_effect`、`failure_stage`、`readable`、`serving_ready`、`replica_count`、`completion_time`（适用时）、`error_code`、`retryable`、`observed_at`、`next_feedback_cursor`（适用时）、`evidence`。

### 10.4 验收要求

- 反馈允许重复投递，C 能按 `feedback_id` 或 `action_id` 去重；
- 反馈丢失后，C 能用 CP2-05 查询原动作；
- P2 重启后，原动作和反馈仍可恢复查询；
- `SUCCEEDED` 反馈不能直接让 C 收口成功，C 仍需调用 CP2-01；
- MVP 可以不提供独立事件流，但必须提供 CP2-05 轮询等价能力。

## 11. CP2-07：物理迁移执行能力

CP2-07 不是要求 C 直接调用的接口，而是 P2 / Provider 必须承接的内部执行能力。C 只提交抽象 `TierAction`，P2 负责将其转换为适合实际 Provider 的物理步骤。

| 物理阶段 | P2 应完成的事情 | 对 C 的可见结果 |
|---|---|---|
| `Copy` | 建立目标副本或目标层数据 | 目标副本是否建立、版本是否匹配 |
| `Verify` | 校验目标副本完整性和可读性 | 校验结果、`readable`、证据 |
| `Cutover` | 在满足条件后切换读取或服务路由 | `route_epoch` 是否变化、服务副本是否就绪 |
| `Reclaim` | 在目标安全可用后回收旧副本 | 副本数量、是否产生释放副作用 |

P2 必须通过 CP2-01、CP2-05 或 CP2-06 让 C 能观察到迁移阶段、实际结果、副作用和最新 Placement。

## 12. CP2-08：Freeze / Unfreeze / Cancel

CP2-08 的支持范围必须由 P2 明确，不要求所有动作、所有阶段都无条件支持。

| 能力 | P2 必须明确的内容 | 关联字段 |
|---|---|---|
| `Freeze` | 冻结对象、目标、写入或路由中的哪一层；从哪个阶段生效 | `action_id`、`provider_ref`、`generation`、`freeze_state` |
| `Unfreeze` | 成功、失败、取消和重启后何时恢复 | `action_id`、`unfreeze_result`、当前状态 |
| `Cancel` | 在 Copy、Verify、Cutover、Reclaim 哪些阶段有效；取消后是否仍有副作用 | `action_id`、`provider_task_id`、`cancel_result`、`side_effect` |

P2 至少需要明确已有 Task 取消、Segment freeze/seal 能力是否可被 C 复用；对于 E2 迁移级 Freeze / Unfreeze，如果本期不支持，必须返回明确的不支持结果和当前真实状态，不能让 C 误判为已冻结或已取消。

## 13. 动作与接口关系

| C 动作 | 必要接口或能力 | 成功判断 |
|---|---|---|
| `Keep` / `No-op` | CP2-01；必要时 CP2-02 | 当前放置保持不变，未产生隐式迁移或路由变化 |
| `Promote` | CP2-01、CP2-02、CP2-03、CP2-04、CP2-05/06、CP2-07 | 目标服务层级、对象版本、路由和可读状态与计划一致 |
| `Demote` | CP2-01、CP2-02、CP2-03、CP2-04、CP2-05/06、CP2-07 | 目标层级可读，释放前仍保留安全副本 |
| `Prefetch` | CP2-01、CP2-02、CP2-03、CP2-04、CP2-05/06、CP2-07 | 目标副本建立且版本匹配；默认不切换路由、不删除源副本 |
| `Pin` / `Unpin` | CP2-01、CP2-02、CP2-04、CP2-05/06；CP2-08 视实现需要 | 保护或解除保护结果可观察，预算占用可解释 |
| `Evict` / `Release` / `Reclaim` | CP2-01、CP2-02、CP2-03、CP2-04、CP2-05/06、CP2-07 | 目标副本安全可读，释放副作用和剩余副本可验证 |

动作到物理步骤的转换由 P2 负责，C 不把一个动作自动转换成另一个动作，也不直接生成 Provider-specific action。

## 14. 统一状态和异常规则

### 14.1 C 侧状态

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

- `ACCEPTED` / `RUNNING` 对应 C 的 `Submitted`；
- `SUCCEEDED` 必须结合 CP2-01 的真实 Placement 才能收口；
- `FAILED` 只有在明确没有物理副作用或已完成对账时才能收口；
- `Unknown` 不是终态，必须先查询原动作和最新 Placement；
- 原动作明确未执行且需要重新计划时，C 创建新的 `action_id`，并关联原动作；
- C 不修改 `current_tier`、`actual_tier`、`generation` 或 `route_epoch`。

### 14.2 版本和路由规则

- `memory_version`、`mapping_generation`、`generation`、`target_generation` 和 `route_epoch` 不能混用；
- C 不自行递增 P2 / Provider 维护的代次；
- `expected_generation` 或目标版本不一致时，P2 拒绝提交或返回冲突；
- `Copy`、`Verify` 不改变 `route_epoch`；
- `Cutover` 只有在路由真正生效并可被 Placement 观察到后才改变 `route_epoch`；
- `Reclaim` 不重新定义路由；
- 旧反馈不能覆盖更新代次后的新 Placement。

### 14.3 Prewarm / Prefetch 的边界

- P3 PRD 的 `Predictive Prewarm` 是 C/B3 的产品能力和策略目标；
- C 的 `Prefetch` 是承载该目标的后台调度动作，进入 CP2-04～CP2-07 闭环；
- Recall 的 RP2-04 在线 `Prewarm` 是读取路径，不属于 C 调度接口，本期不纳入本清单；
- 在线读取命中热副本，不得隐式创建 C 的 `TierAction`。

## 15. P2 交付清单

P2 需要在接口契约冻结和 RC 前提供：

1. CP2-01～CP2-06 的实际方法名称、契约版本和调用样例；
2. CP2-07 的承载组件、支持的动作、物理阶段和可观测结果；
3. CP2-08 的支持范围、生效阶段、不支持时的返回和安全结果；
4. 各接口的请求、响应、错误和冲突样例；
5. `representation_id`、`provider_ref`、`target_id` 和各类代次的关联规则；
6. `action_id`、`provider_task_id` 和幂等键的关联、保留和查询规则；
7. `Accepted`、`Running`、`Succeeded`、`Failed`、`Unknown` 的状态、副作用和阶段语义；
8. 反馈丢失、超时、重复提交、P2 重启、Provider Unknown 和版本冲突的可重复用例；
9. 真实 Provider 环境、模拟环境及两者证据差异的说明。

## 16. 最低联调验收

```text
C 读取 GetPlacement / GetResourceState
  -> C 生成 PlacementPlan
  -> C 请求 ResolveActuationTarget
  -> C 提交 SubmitTierAction
  -> P2 / Provider 执行物理动作
  -> C 接收反馈或查询 QueryActionStatus
  -> C 再次调用 GetPlacement
  -> C 按 action_type、版本、路由和副作用完成对账
```

以下任一情况只能算“建议链路”或 Mock 验收，不能算真实调度闭环验收：

- C 只生成了 `PlacementPlan` 或 `TierAction`；
- P2 只返回 `Accepted` 或 `Running`；
- P2 返回 `Succeeded`，但没有真实 Placement 和版本证据；
- 反馈丢失后无法按原 `action_id` 查询；
- 结果未知时只能盲目创建新动作；
- C 被要求解析 Provider 内部路径或自行拼接 Provider-specific action。
