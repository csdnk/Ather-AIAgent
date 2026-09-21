# AetherStore P3 C 组交付文档

本文档是 C 组 Operate / Optimize 工作包的独立交付物，来源于《AetherStore P3 C 组设计方案 V0.1》。C 组负责调度控制面，不拥有 Memory 主事实，不实现底层存储和物理迁移。

统一责任边界如下：C 组消费运行事实，生成计划和动作，接收执行反馈，刷新真实观察并完成对账；P2 / Provider 提供真实层级、资源和物理执行结果。

本文档只保留本交付物相关内容。涉及 P2 执行方、接口字段和资源配额的部分，在尚未冻结的地方明确标记为待确认。

## 交付物说明

C-01 输入事实来源图

## 交付边界

本交付物回答 MemorySignal、AccessTrace、Placement Observation、ResourceState、ExecutionFeedback、ActionHistory 和 PolicyContext 分别由谁产生、谁拥有、如何获取、如何判断新鲜度，以及丢失后如何处理。

## C-01 输入事实来源图

### 输入事实和权威方

| 输入事实 | 产生方 | 权威方 | C 组获取方式 | C 组处理原则 |
|---|---|---|---|---|
| `MemorySignal` | Remember Runtime（B） | B 的 Memory 生命周期和语义事实 | 事件或消息推送，支持 Outbox、补发和重放 | 只触发或更新 Plan，不直接生成 TierAction |
| `AccessTrace` | Recall Runtime（A/B） | 实际 Recall 过程 | 事件流、批量接口或 Shared Runtime Trace Store | 只使用真实访问事实，不把搜索候选当成实际使用 |
| `Placement Observation` / `TierState` | P2 / Provider | 真实层级和版本事实 | 状态事件加最新状态查询 | 创建动作前必须重新读取，不能依赖过期快照 |
| `ResourceState` | P2 / Provider | 真实容量、压力和执行资源 | 周期快照加压力事件 | 过期或未知时禁止新的非 `Keep` 动作 |
| `ExecutionFeedback` | P2 / Provider | 物理动作执行过程 | 回调加 `action_id` 查询兜底 | 反馈用于推进状态，最终结果还要和 Placement 对照 |
| `ActionHistory` | C 组 | C 的动作记录 | C 自有持久化存储 | 用于去重、冷却、冲突判断和审计 |
| `PolicyContext` | C 策略服务 | 已发布的策略/模型版本 | 版本化配置读取 | Plan 和 Action 必须记录版本 |

### 各输入事实字段契约

统一输入信封只解决消息的来源、时间、版本和去重问题；下面的业务字段才是 C 进行调度判断所需要的内容。除特别说明外，字段均为必填。字段名称以英文为准，字段语义和允许取值应在联调接口中固定，具体数据类型不在本交付物中冻结。

#### MemorySignal

`MemorySignal` 描述 Memory 的业务事实变化。它只能触发或更新 Plan，不能直接替代 Placement Observation，也不能直接生成 TierAction。

| 字段 | 字段语义/允许取值 | 必填性 | C 组用途 |
|---|---|---|---|
| `signal_id` | 信号唯一标识 | 必填 | 信号唯一标识，用于去重、补发和审计 |
| `signal_type` | 允许取值：`created`、`updated`、`lifecycle_changed`、`importance_changed`、`representation_changed` | 必填 | 判断本次变化的业务含义 |
| `memory_id` | 业务 Memory 标识 | 必填 | 关联业务 Memory |
| `memory_version` | Memory 版本标识 | 必填 | 判断信号是否覆盖旧版本 |
| `memory_type` | 允许取值：`working`、`episodic`、`semantic` | 必填 | 作为热度和策略输入；不由 C 修改 |
| `lifecycle` | 允许取值：`active`、`archived`、`superseded`、`expired`、`deleted` | 必填 | `deleted`、`expired` 等状态禁止继续生成不合适的新动作 |
| `importance` | 重要性范围：`[0,1]` | 必填 | 参与 per-representation Hotness 计算 |
| `affected_representation_ids` | 受影响的 Representation 标识列表 | 条件必填 | `signal_type=representation_changed` 时指出受影响的表示 |
| `content_version` | 内容版本标识 | 条件必填 | 内容更新时判断 Projection 或 Materialization 是否过期 |
| `scope` | 权限和资源范围信息 | 必填 | 进行权限和资源范围隔离 |
| `occurred_at` | 业务事实发生时间 | 必填 | 记录业务事实发生时间 |
| `producer` | 具体生产组件标识 | 必填 | 标识 B 的具体生产组件 |

如果缺少 `memory_version`、`lifecycle` 或稳定的 `memory_id`，C 只能保存信号并等待补发，不能据此覆盖已有计划。

#### AccessTrace

`AccessTrace` 描述 Recall 过程中的真实访问事实。C 必须能够区分“搜索命中”和“内容被实际使用”，不能只接收一个笼统的命中次数。

| 字段 | 字段语义/允许取值 | 必填性 | C 组用途 |
|---|---|---|---|
| `trace_event_id` | 访问事件唯一标识 | 必填 | 访问事件唯一标识，用于去重和重放 |
| `request_id` | 业务请求标识 | 必填 | 关联一次业务请求 |
| `trace_id` | 技术调用链标识 | 必填 | 关联技术调用链 |
| `memory_id` | 业务 Memory 标识 | 必填 | 关联业务 Memory |
| `representation_id` | Representation 标识 | 必填 | 按表示而不是按整个 Memory 统计热度 |
| `representation_type` | Representation 的类别标识，由表示生产方定义 | 必填 | 区分访问的是哪种表示 |
| `event_stage` | 允许取值：`search_hit`、`candidate_accepted`、`content_loaded`、`context_selected`、`context_emitted` | 必填 | 区分检索、加载和实际使用 |
| `access_result` | 允许取值：`hit`、`miss`、`partial`、`failed` | 必填 | 计算命中和降级情况 |
| `source_provider` | 来源 Provider 标识 | 必填 | 标识 Redis、Vector Provider 或 Canonical Provider 等来源 |
| `observed_tier` | 允许取值：`HOT`、`WARM`、`COLD` | 条件必填 | 实际访问发生时记录当时层级 |
| `generation` | 对象版本标识 | 条件必填 | 访问事实需要和某一版本关联时提供 |
| `used_in_context` | 是否实际进入 Context | 必填 | 只有为 true 才能作为有效使用证据 |
| `occurred_at` | 访问发生时间 | 必填 | 访问发生时间，用于时间衰减和窗口统计 |
| `latency_ms` | 访问延迟 | 推荐 | 评估层级收益和 P99 影响 |
| `bytes_read` | 读取数据量 | 推荐 | 评估读取成本 |
| `action_id` | 关联的调度动作标识 | 条件必填 | 访问命中由某次 Prefetch/Promote 建立的表示时回填 |
| `scope` | 租户、项目或任务范围信息 | 必填 | 按租户、项目或任务范围统计和隔离 |

其中，`search_hit` 和 `candidate_accepted` 不等于真实使用；`context_emitted` 或 `used_in_context=true` 才能计入有效访问。`action_id` 不应由 C 事后猜测，必须由 Recall 或执行链路在能够确认关联时提供。

#### Placement Observation / TierState

`Placement Observation` 是当前真实层级的事实快照。它是 C 判断 Plan 是否仍有效、动作是否成功以及是否可以释放高层副本的依据。

| 字段 | 字段语义/允许取值 | 必填性 | C 组用途 |
|---|---|---|---|
| `observation_id` | 观测快照唯一标识 | 必填 | 观测快照唯一标识 |
| `target_id` | 可执行目标标识 | 必填 | 关联可执行目标 |
| `representation_id` | Representation 标识 | 必填 | 关联 C 的调度对象 |
| `provider_ref` | Provider 稳定引用 | 必填 | 关联 P2 / Provider 的实际对象 |
| `current_tier` | 允许取值：`HOT`、`WARM`、`COLD` | 必填 | 与 Plan 的 `desired_tier` 比较 |
| `generation` | 对象版本标识 | 必填 | 判断观察是否属于当前对象版本 |
| `route_epoch` | 路由或切换版本标识 | 必填 | 判断路由或切换版本是否发生变化 |
| `observed_at` | 观测发生时间 | 必填 | 判断快照是否过期 |
| `observation_source` | 观测来源标识 | 必填 | 记录 P2、Tier Executor 或具体 Provider |
| `supported_operations` | 支持的动作列表 | 必填 | 判断是否支持所需动作 |
| `readable` | 当前副本是否可读 | 必填 | 判断当前副本是否可读 |
| `serving_ready` | 是否可作为低层 Serving 副本 | 推荐 | 判断是否可以作为低层 Serving 副本 |
| `replica_count` | 可用副本数量 | 推荐 | 判断释放后是否仍有可用副本 |
| `state_version` | 状态版本标识 | 推荐 | 处理观测事件乱序 |

如果 `current_tier`、`generation`、`observed_at` 任一缺失，C 不得把该快照用于新的非 `Keep` 动作。`Placement Observation` 只能反映真实状态，不能填入 C 计划中的 `desired_tier`。

#### ResourceState

`ResourceState` 描述动作提交时可用的容量、预算、迁移能力和在线服务压力。C 消费该事实，不根据自己已提交的动作反推真实容量。

| 字段 | 字段语义/允许取值 | 必填性 | C 组用途 |
|---|---|---|---|
| `resource_scope_id` | 资源范围标识 | 必填 | 标识资源池、Redis 实例或 Provider 范围 |
| `provider_name` | Provider 标识 | 必填 | 标识资源事实来源 |
| `state_version` | 状态版本标识 | 必填 | 处理快照更新和乱序 |
| `observed_at` | 资源观测时间 | 必填 | 判断资源快照是否过期 |
| `freshness` | 允许取值：`FRESH`、`STALE`、`UNKNOWN` | 必填 | 决定是否允许新的非 Keep 动作 |
| `backend_health` | 允许取值：`READY`、`DEGRADED`、`NOT_READY`、`UNKNOWN` | 必填 | 判断 Provider 是否可接收动作 |
| `total_capacity_bytes` | 资源总量 | 必填 | 资源总量 |
| `used_capacity_bytes` | 当前已用容量 | 必填 | 当前已用容量 |
| `available_capacity_bytes` | 当前可用容量 | 必填 | 判断动作成本是否可容纳 |
| `pressure_ratio` | 资源压力范围：`[0,1]` | 必填 | 判断 Hot Tier 或资源池压力 |
| `working_reserved_capacity_bytes` | Working 预留容量 | 必填 | 保护 Working Memory 的预留量 |
| `prewarm_budget_bytes` | Prewarm 预算上限 | 必填 | 限制 Prewarm 总占用 |
| `prewarm_used_bytes` | Prewarm 当前占用 | 必填 | 计算 Prewarm 剩余额度 |
| `pin_budget_bytes` | Pin 预算上限 | 必填 | 限制 Pin 总占用 |
| `pin_used_bytes` | Pin 当前占用 | 必填 | 计算 Pin 剩余额度 |
| `active_migration_count` | 当前迁移数量 | 必填 | 判断当前并发迁移数量 |
| `max_concurrent_migration` | 迁移并发上限 | 必填 | 限制同时执行的迁移动作 |
| `migration_bandwidth_bytes_per_sec` | 当前迁移带宽 | 推荐 | 评估迁移带宽余量 |
| `migration_bandwidth_budget_bytes_per_sec` | 迁移带宽预算上限 | 推荐 | 限制后台迁移带宽 |
| `read_latency_p99_ms` | 读取延迟 P99 | 推荐 | 判断在线读延迟是否受影响 |
| `write_latency_p99_ms` | 写入延迟 P99 | 推荐 | 判断在线写延迟是否受影响 |

如果 `freshness` 为 `STALE` 或 `UNKNOWN`，或者 `backend_health` 不是 `READY`，C 默认只允许查询、对账和 `Keep / No-op`，不提交新的 `Promote`、`Demote`、`Prefetch` 或 `Release`。

#### ExecutionFeedback

`ExecutionFeedback` 描述一次 TierAction 在执行侧的进展和结果。中间状态用于推进 `Submitted`，最终状态必须结合 `Placement Observation` 确认。

| 字段 | 字段语义/允许取值 | 必填性 | C 组用途 |
|---|---|---|---|
| `feedback_id` | 反馈事件唯一标识 | 必填 | 反馈事件唯一标识 |
| `action_id` | TierAction 标识 | 必填 | 关联 C 的 TierAction |
| `provider_task_id` | Provider 任务标识 | 条件必填 | Provider 已创建任务时用于查询 |
| `provider_status` | 允许取值：`ACCEPTED`、`RUNNING`、`SUCCEEDED`、`FAILED`、`UNKNOWN` | 必填 | 映射 C 的 ActionState |
| `actual_tier` | 允许取值：`HOT`、`WARM`、`COLD` | 成功时必填 | 校验是否达到目标层级 |
| `pre_generation` | 动作开始时的版本标识 | 必填 | 校验动作开始时的版本 |
| `post_generation` | 动作完成后的版本标识 | 成功或版本变化时必填 | 记录动作完成后的版本 |
| `route_epoch` | 路由或切换版本标识 | 推荐 | 校验路由切换是否匹配 |
| `migration_stage` | 允许取值：`copy`、`verify`、`cutover`、`reclaim` | 推荐 | 展示执行进展；不作为 C 的业务状态 |
| `completion_time` | 动作完成时间 | 成功或失败时必填 | 记录最终完成时间 |
| `error_code` | 执行错误标识 | 失败或未知时必填 | 分类处理和判断是否可重试 |
| `retryable` | 是否允许重试 | 失败或未知时必填 | 给出重试建议，但不能绕过对账 |
| `observed_at` | 反馈观测时间 | 必填 | 判断反馈新鲜度 |

`ACCEPTED` 和 `RUNNING` 不要求提供 `actual_tier`；`SUCCEEDED` 若缺少 `actual_tier`、`pre_generation` 或 `post_generation`，C 不能直接更新为 `Succeeded`，应进入查询或对账。

#### ActionHistory

`ActionHistory` 是 C 对已生成和已提交动作的持久化记录，用于幂等、冷却、冲突判断和审计。它不是 P2 的执行事实。

| 字段 | 字段语义/允许取值 | 必填性 | C 组用途 |
|---|---|---|---|
| `action_id` | 动作唯一标识 | 必填 | 动作唯一标识 |
| `idempotency_key` | 幂等键 | 必填 | 防止重复提交和重复执行 |
| `plan_id` | Plan 标识 | 必填 | 关联产生该动作的 Plan |
| `representation_id` | Representation 标识 | 必填 | 锁定调度对象 |
| `action_type` | 允许取值：`Keep`、`Promote`、`Demote`、`Pin`、`Prefetch`、`Release` | 必填 | 记录动作类型 |
| `source_tier` | 动作开始层级 | 必填 | 记录动作开始层级 |
| `desired_tier` | 动作目标层级 | 必填 | 记录动作目标层级 |
| `expected_generation` | 提交前版本标识 | 必填 | 提交前版本校验 |
| `policy_version` | 策略版本标识 | 必填 | 复现决策依据 |
| `action_state` | 允许取值：`Generated`、`Submitted`、`Succeeded`、`Failed`、`Unknown` | 必填 | C 侧动作状态 |
| `provider_ref` | Provider 稳定引用 | 必填 | 关联实际执行目标 |
| `provider_task_id` | Provider 任务标识 | 条件必填 | 提交成功后关联 Provider 任务 |
| `retry_of_action_id` | 被重试动作的标识 | 重试时必填 | 关联原动作，禁止复用旧 action_id |
| `reason_code` | 原因标识 | 必填 | 记录准入、拒绝或失败原因 |
| `created_at` | 动作生成时间 | 必填 | 记录动作生成时间 |
| `submitted_at` | 动作提交时间 | 条件必填 | 记录提交时间 |
| `completed_at` | 动作收口时间 | 终态时必填 | 记录收口时间 |

#### PolicyContext

`PolicyContext` 描述 C 生成 Plan 和 TierAction 时使用的策略、模型和运行参数。任何影响调度结果的参数变化都必须生成新的 `policy_version` 或关联的 `model_version`。

| 字段 | 字段语义/允许取值 | 必填性 | C 组用途 |
|---|---|---|---|
| `policy_version` | 策略版本标识 | 必填 | 绑定 Plan、Action 和审计记录 |
| `model_version` | 模型版本标识 | 条件必填 | 使用预测模型时复现模型版本 |
| `feature_version` | 特征版本标识 | 推荐 | 复现热度特征定义 |
| `effective_from` | 策略生效时间 | 必填 | 策略生效时间 |
| `effective_until` | 策略失效时间 | 推荐 | 策略失效时间 |
| `mode` | 允许取值：`Auto`、`Advisory`、`Paused` | 必填 | 决定是否允许自动提交动作 |
| `promote_threshold` | Promote 触发阈值 | 必填 | 触发升层的阈值 |
| `demote_threshold` | Demote 触发阈值 | 必填 | 触发降层的阈值 |
| `low_heat_confirmation_windows` | 低热度确认窗口数量 | 必填 | 降层防抖窗口 |
| `cooldown_seconds` | 动作冷却时长 | 必填 | 同一目标动作冷却时间 |
| `scheduling_interval_seconds` | 调度重算周期 | 必填 | 调度重算周期 |
| `access_window_seconds` | 访问统计窗口 | 必填 | 访问统计窗口 |
| `prediction_horizon_seconds` | 预测窗口长度 | 使用预测时必填 | 预测窗口长度 |
| `working_reserved_capacity_bytes` | Working 预留容量参数 | 必填 | Working 资源保护参数 |
| `prewarm_budget_bytes` | Prewarm 预算上限 | 必填 | Prewarm 资源上限 |
| `pin_budget_bytes` | Pin 预算上限 | 必填 | Pin 资源上限 |
| `max_concurrent_migration` | 后台迁移并发上限 | 必填 | 后台迁移并发上限 |
| `migration_bandwidth_budget_bytes_per_sec` | 后台迁移带宽上限 | 必填 | 后台迁移带宽上限 |
| `migration_impact_threshold` | 迁移影响阈值 | 必填 | 触发迁移限流的线上影响阈值 |
| `fallback_policy` | 允许取值：`previous_stable`、`heuristic`、`keep_noop` | 必填 | 策略或模型不可用时的回退顺序 |

策略服务必须返回完整且自洽的 `PolicyContext`。如果只返回 `policy_version` 而缺少阈值、冷却或预算参数，C 只能记录配置不完整并采用 `Keep / No-op`，不能使用本地隐式默认值提交物理动作。

### 统一输入信封

所有进入 C 的事件或快照至少包含以下字段：

```text
event_id
event_type
schema_version
source
occurred_at
observed_at
request_id
trace_id
memory_id
representation_id
generation              # 适用时提供
idempotency_key         # 适用时提供
payload
```

### 统一输入信封字段含义

| 字段 | 含义 | 使用规则 |
|---|---|---|
| `event_id` | 输入事件或快照的唯一标识 | 用于去重、重放和审计 |
| `event_type` | 输入事实的事件类型 | 用于选择对应的消费和校验规则 |
| `schema_version` | 输入结构版本 | 用于兼容性校验，不能用新结构直接覆盖旧结构 |
| `source` | 输入来源组件或系统 | 标识事实生产方和查询来源 |
| `occurred_at` | 业务事实发生时间 | 判断事实发生顺序和时间窗口 |
| `observed_at` | 事实被观测或生成的时间 | 判断快照或反馈是否新鲜 |
| `request_id` | 关联的业务请求标识 | 贯通一次业务请求 |
| `trace_id` | 关联的技术调用链标识 | 贯通跨组件调用和排障链路 |
| `memory_id` | 关联的业务 Memory 标识 | 适用于 Memory 粒度输入 |
| `representation_id` | 关联的 Representation 标识 | 适用于 Representation 粒度输入 |
| `generation` | 关联对象的版本标识 | 适用时用于防止旧事实覆盖新状态 |
| `idempotency_key` | 关联操作的幂等键 | 适用时用于防止重复处理或重复提交 |
| `payload` | 具体业务事实内容 | 按 `event_type` 解析，并接受对应字段契约约束 |

C 以 `event_id` 或来源方提供的幂等键去重，以 `observed_at` 判断新鲜度，以 `schema_version` 做兼容校验。输入事实缺少稳定对象标识、版本或时间时，不得直接驱动物理动作。

### 获取、持久化和丢失处理

`MemorySignal` 和 `AccessTrace` 必须支持补发或重放。C 维护每个来源的消费游标和最近处理版本；重复消息只更新消费记录，不重复生成动作。`Placement Observation` 和 `ResourceState` 必须保留最新可查询快照，事件丢失时由 C 主动拉取。`ExecutionFeedback` 丢失时，C 将关联的 `TierAction` 置为 `Unknown`，通过 `action_id` 查询和对账收敛，不把无反馈直接判定为失败或成功。

建议的默认新鲜度策略如下，最终阈值应写入 `PolicyContext`：

| 事实 | 新鲜度判断 | 过期后的行为 |
|---|---|---|
| `MemorySignal` | 以来源事件时间和版本判断 | 保留待重放，不据此覆盖更新版本 |
| `AccessTrace` | 以统计窗口截止时间判断 | 可以参与历史统计，但不能伪造近期访问 |
| `Placement Observation` | `now - observed_at <= placement_staleness` | 重新查询；禁止非 `Keep` 动作 |
| `ResourceState` | `now - observed_at <= resource_staleness` | 重新查询；禁止新增非 `Keep` 动作 |
| `ExecutionFeedback` | 回调时间和状态版本有效 | 查询 Provider；仍不能确认则进入 `Unknown` |

### AccessTrace 计数口径

`AccessTrace` 的事件阶段不能混用。C 计算热度时按以下口径消费：

| 事件阶段 | 是否算真实访问 | 用途 |
|---|---:|---|
| `Search Hit` | 否 | 记录检索结果，但不能证明对象被使用 |
| `Candidate Accepted` | 否 | 记录候选进入后续处理 |
| `Content Loaded` | 是，表示内容已加载 | 判断内容读取和加载成本 |
| `Context Selected` | 是，表示被选入上下文 | 判断进入 Context 的事实 |
| `Context Emitted` / `used_in_context=true` | 是，作为有效使用 | 计算有效访问、命中和 Prewarm 收益 |

不同 Representation 的命中、内容加载和上下文使用应分别记录 `representation_id` 和 `source_provider`，不能只留下一个笼统的 Memory 命中计数。`action_id` 只有在访问确实命中了由该动作建立的目标表示时才回填，用于计算 `hit_after_prefetch`。
