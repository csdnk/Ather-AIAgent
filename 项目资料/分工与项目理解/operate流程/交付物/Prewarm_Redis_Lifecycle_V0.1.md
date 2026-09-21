# AetherStore P3 C 组交付文档

本文档是 C 组 Operate / Optimize 工作包的独立交付物，来源于《AetherStore P3 C 组设计方案 V0.1》。C 组负责调度控制面，不拥有 Memory 主事实，不实现底层存储和物理迁移。

统一责任边界如下：C 组消费运行事实，生成计划和动作，接收执行反馈，刷新真实观察并完成对账；P2 / Provider 提供真实层级、资源和物理执行结果。

本文档只保留本交付物相关内容。涉及 P2 执行方、接口字段和资源配额的部分，在尚未冻结的地方明确标记为待确认。

## 交付物说明

C-06 Prewarm Redis 生命周期

## 交付边界

本交付物定义 Prefetch、Promote、Keep、Demote、Release 和 Pin 的生命周期，Working Memory 与 Prewarm 的资源隔离，以及释放失败、反馈丢失和状态未知时的处理方式。

## C-06 Prewarm Redis 生命周期

### 操作定义

| 操作 | 触发依据 | C 组决定 | 执行侧负责 |
|---|---|---|---|
| `Prefetch` | 预测窗口内有潜在访问，且预算和压力允许 | 是否提前建立 Hot Materialization | 读取低层内容、写入 Redis、校验并反馈 |
| `Promote` | 真实访问热度达到阈值，或业务优先级要求 | 目标逻辑层级和优先级 | 执行实际升层或建立目标表示 |
| `Keep` | 目标和实际一致，或安全条件不足 | 不创建物理动作 | 保持当前状态 |
| `Demote` | 热度下降、预热窗口结束或资源压力升高 | 选择低价值且未保护的候选 | 写入低层、校验后回收高层副本 |
| `Release` | 高层 Materialization 已无依赖且低层可读 | 是否允许释放和释放原因 | 实际删除、TTL、物理回收和结果反馈 |
| `Pin` | 运营或策略要求短期固定 | Pin 的范围、期限和预算 | 按执行侧能力保持驻留 |

`Prefetch` 只代表预测性准备，不能当作真实访问。只有后续 `AccessTrace` 证明对象被实际命中和使用，才计入有效预热指标。

### 生命周期规则

```text
候选产生
  -> Prewarm Admission
  -> Prefetch Generated / Submitted
  -> Hot Materialization 已确认
  -> 等待真实访问或窗口结束
  -> 命中：保持或按热度转 Promote
  -> 未命中且压力上升：Demote
  -> 低层可读且无依赖：Release
```

候选必须记录 `prediction_time`、`prediction_window`、`prediction_score`、`resource_cost`、`prefetch_action_id` 和 `hit_after_prefetch`。窗口结束后仍未发生有效访问的候选，其热度和优先级应降低，不得无限期占用 Hot Tier。

### Prewarm 候选字段含义

| 字段 | 含义 | 使用规则 |
|---|---|---|
| `prediction_time` | 产生预取预测的时间 | 用于判断预测是否仍然新鲜 |
| `prediction_window` | 预测对象可能被访问的时间窗口 | 窗口结束后重新评估候选，不无限保留 |
| `prediction_score` | 预测对象将被访问的置信或优先分数 | 用于 Prewarm Admission 和候选排序 |
| `resource_cost` | 建立该预热表示预计占用的资源 | 与 Prewarm Budget 和当前压力比较 |
| `prefetch_action_id` | 建立该预热表示的动作标识 | 关联执行反馈和后续 Recall |
| `hit_after_prefetch` | 预取后是否发生真实命中和使用 | 只能由后续 AccessTrace 确认，不能由 Prefetch 成功直接推断 |

### Pin、Force Keep 和 Release Admission

Pin 和 Force Keep 不是普通热度分数。有效期间，C 不得对相同表示提交普通 `Demote` 或 `Release`。Pin 必须带有作用域、创建者、原因、开始时间、截止时间和解除方式，并计入 `Pin Budget`。Pin 到期或被解除后，C 重新读取 Observation 和 ResourceState，再决定 Keep、Demote 或 Release。

### Pin 控制字段含义

| 字段 | 含义 | 使用规则 |
|---|---|---|
| `scope` | Pin 作用的对象或资源范围 | 限制保护范围，不能默认为全局 |
| `created_by` | 创建 Pin 的操作主体 | 用于权限校验和审计 |
| `reason` | 创建 Pin 的原因 | 说明为什么需要阻止 Demote 或 Release |
| `started_at` | Pin 生效时间 | 判断当前是否已经生效 |
| `expires_at` | Pin 截止时间 | 到期后重新评估，不得无限期保护 |
| `release_method` | 解除 Pin 的条件或操作 | 支持到期、人工解除或关联任务完成等方式 |

允许 Release 的必要条件是：

```text
低层 Serving / Authoritative Copy 已可读
目标 generation 和 route_epoch 仍然有效
没有有效 Pin / Force Keep
没有 Submitted / Unknown 冲突动作
Prefetch Window 已结束或已确认不再需要
目标副本已通过写入和完整性校验
```

释放失败、反馈丢失或状态未知时，保留原 Redis 副本，不提前修改 `current_tier`，进入 `ReconciliationTask`。C 只生成 Release 请求，物理删除、TTL 和 eviction 由 Provider / Redis 执行侧负责。

### 共享 Redis 的资源隔离

如果 Working Memory 和 Prewarm 共用 Redis，必须至少采用独立实例，或使用独立 namespace / keyspace 与可执行配额。仅增加 key 前缀而没有配额和压力反馈，不能证明隔离有效。

```text
Working Reserved Capacity  -> 在线读写优先保护
Prewarm Budget              -> 后台预热上限
Pin Budget                  -> 固定驻留上限
Provider / Redis            -> 物理写入、回收和真实状态
C                           -> 预算判断、动作准入和 Demote 候选
```

Hot Tier 压力上升时，执行顺序为：停止新增低优先级 Prefetch，拒绝超预算的 Promote / Pin，选择低价值且未 Pin 的 Prewarm 生成 Demote 候选，最后才由 Provider 进行物理回收。不能让 Redis 原生淘汰随机删除 Working 数据。

### Prewarm 例子

某个 Representation 预测将在未来十分钟被访问，C 生成 `Prefetch`。Provider 完成写入并返回成功后，C 仍等待真实 `AccessTrace`。如果十分钟内发生有效访问，记录 `hit_after_prefetch=true`；如果窗口结束仍未访问，且 Working 预留接近压力阈值，C 停止新增 Prewarm，并把该对象作为 `Demote` 候选。只有低层副本校验可读后，Provider 才能回收 Redis 中的预热副本。
