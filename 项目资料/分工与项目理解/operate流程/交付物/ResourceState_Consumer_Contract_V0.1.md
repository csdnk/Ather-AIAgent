# AetherStore P3 C 组交付文档

本文档是 C 组 Operate / Optimize 工作包的独立交付物，来源于《AetherStore P3 C 组设计方案 V0.1》。C 组负责调度控制面，不拥有 Memory 主事实，不实现底层存储和物理迁移。

统一责任边界如下：C 组消费运行事实，生成计划和动作，接收执行反馈，刷新真实观察并完成对账；P2 / Provider 提供真实层级、资源和物理执行结果。

本文档只保留本交付物相关内容。涉及 P2 执行方、接口字段和资源配额的部分，在尚未冻结的地方明确标记为待确认。

## 交付物说明

C-05 ResourceState Consumer Contract

## 交付边界

本交付物定义 C 消费的资源状态字段、权威方、新鲜度、预算、并发迁移、在线 P99 保护和资源压力下的动作准入。真实容量和压力事实由 P2 / Provider 提供。

## C-05 ResourceState Consumer Contract

### 最小字段

```text
resource_scope_id
provider_name
state_version
observed_at
freshness
backend_health

total_capacity_bytes
used_capacity_bytes
available_capacity_bytes
pressure_ratio

working_reserved_capacity_bytes
prewarm_budget_bytes
prewarm_used_bytes
pin_budget_bytes
pin_used_bytes

active_migration_count
max_concurrent_migration
migration_bandwidth_bytes_per_sec
migration_bandwidth_budget_bytes_per_sec

read_latency_p99_ms
write_latency_p99_ms
```

### ResourceState 字段含义

| 字段 | 含义 | C 组用途 |
|---|---|---|
| `resource_scope_id` | 资源池、Redis 实例或 Provider 的范围标识 | 确定资源事实适用的范围 |
| `provider_name` | 提供资源事实的 Provider 标识 | 识别资源来源 |
| `state_version` | 资源状态版本 | 处理快照更新和事件乱序 |
| `observed_at` | 资源状态观测时间 | 判断资源快照是否过期 |
| `freshness` | 资源事实的新鲜度状态 | 允许取值：`FRESH`、`STALE`、`UNKNOWN`；决定是否允许非 `Keep` 动作 |
| `backend_health` | Provider 当前健康状态 | 允许取值：`READY`、`DEGRADED`、`NOT_READY`、`UNKNOWN`；判断是否可提交动作 |
| `total_capacity_bytes` | 资源总量 | 计算容量使用情况 |
| `used_capacity_bytes` | 当前已使用容量 | 计算剩余资源和压力 |
| `available_capacity_bytes` | 当前可用容量 | 判断动作成本是否可容纳 |
| `pressure_ratio` | 资源压力水平 | 判断是否触发限流或释放候选 |
| `working_reserved_capacity_bytes` | 为 Working Memory 保留的容量 | 防止后台调度挤占在线读写资源 |
| `prewarm_budget_bytes` | Prewarm 可使用的预算上限 | 限制后台预热规模 |
| `prewarm_used_bytes` | Prewarm 当前已使用的预算 | 计算剩余预热空间 |
| `pin_budget_bytes` | Pin 可使用的预算上限 | 限制固定驻留规模 |
| `pin_used_bytes` | Pin 当前已使用的预算 | 计算剩余固定驻留空间 |
| `active_migration_count` | 当前正在执行的迁移数量 | 判断是否还能提交后台动作 |
| `max_concurrent_migration` | 允许的迁移并发上限 | 限制同时执行的迁移动作 |
| `migration_bandwidth_bytes_per_sec` | 当前迁移带宽 | 评估迁移能力和后台调度速度 |
| `migration_bandwidth_budget_bytes_per_sec` | 后台迁移带宽预算 | 防止迁移影响在线服务 |
| `read_latency_p99_ms` | 读取延迟 P99 | 判断在线读取是否受到迁移影响 |
| `write_latency_p99_ms` | 写入延迟 P99 | 判断在线写入是否受到迁移影响 |

`ResourceState` 的权威方是 P2 / Provider。C 只保存消费快照、版本和决策证据，不能根据自己的动作记录推算真实容量。

### 新鲜度和未知状态

建议将资源事实归一为：

```text
FRESH   # 在允许使用的时间窗内
STALE   # 超过正常窗口但仍有旧快照
UNKNOWN # 无快照、字段冲突或 Provider 不可用
```

`STALE` 和 `UNKNOWN` 时，C 默认只允许 `Keep / No-op`、查询、对账和降低后台调度速率；不能提交新的 `Promote`、`Demote`、`Prefetch` 或 `Release`。紧急资源保护可以由有权限的 OperatorControlTask 触发，但必须记录人工原因和审计信息。

### 预算和保护规则

预算必须由 P2 / Provider 明确是独立配额还是同一容量池中的逻辑配额。若是同一容量池，至少要能计算以下三个安全量：

```text
prewarm_headroom = prewarm_budget - prewarm_used
pin_headroom     = pin_budget - pin_used
working_headroom = available_capacity - working_reserved_capacity
```

创建动作前应同时满足：

```text
ResourceState.freshness == FRESH
prewarm_headroom >= action.prewarm_cost       # 对 Prefetch
pin_headroom >= action.pin_cost                # 对 Pin
working_headroom >= 0
active_migration_count < max_concurrent_migration
P99 未触发 Migration Impact Guard
```

Working Reserved Capacity 优先级高于 Prewarm。C 不能通过占用 Working 预留来完成后台预热，也不能把 Redis 原生 eviction 当作正常容量管理路径。

### 资源压力下的动作规则

| 资源事件 | C 的动作 |
|---|---|
| `Prewarm Budget` 用尽 | 拒绝新的低优先级 `Prefetch`，记录 `prewarm_budget_exhausted` |
| `Pin Budget` 用尽 | 拒绝新的 `Pin`，不改变已有 Pin |
| Working 预留不足 | 停止新的 Predictive Prewarm，保护 Working 读写 |
| Hot Tier 压力过高 | 从未 Pin 且低价值的 Prewarm 中生成 `Demote` 候选 |
| 并发迁移达到上限 | 新的后台迁移保持 `Keep`，不提交 |
| 在线 P99 超过影响阈值 | 进入 `Migration Throttled`，停止后台 Prefetch / 非必要 Demote |
| Provider 不健康 | 停止新的非 Keep 动作，等待状态恢复或人工处置 |
