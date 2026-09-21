# AetherStore P3 C 组交付文档

本文档是 C 组 Operate / Optimize 工作包的独立交付物，来源于《AetherStore P3 C 组设计方案 V0.1》。C 组负责调度控制面，不拥有 Memory 主事实，不实现底层存储和物理迁移。

统一责任边界如下：C 组消费运行事实，生成计划和动作，接收执行反馈，刷新真实观察并完成对账；P2 / Provider 提供真实层级、资源和物理执行结果。

本文档只保留本交付物相关内容。涉及 P2 执行方、接口字段和资源配额的部分，在尚未冻结的地方明确标记为待确认。

## 交付物说明

C-02 Representation 到 ActuationTarget 映射

## 交付边界

本交付物冻结 C 的调度粒度、memory_id -> representation_id -> provider_ref 映射、ActuationTarget 的不透明原则和解析结果字段。C 不根据 Segment、Object 或 Cache Key 的具体类型分支。

## C-02 Representation 到 ActuationTarget 映射

### 调度粒度

C 按 `representation_id` 做热度和放置决策，不把整个 Memory 作为不可拆分的调度对象。一个 Memory 可以同时具有多个 Representation，它们可以拥有不同的目标层级。

```text
memory_id
    -> representation_id
        -> provider_ref
            -> ActuationTarget
```

`Memory` 是业务根对象，`representation` 是 C 的决策单位，`Segment`、`Object`、`Cache Key` 等是 P2 / Provider 的执行单位。C 不根据 `target_type` 编写多套调度逻辑。

### 映射登记表

C 维护逻辑映射的版本和有效期，P2 / Provider 维护物理目标的真实存在性。

| 字段 | 含义 | 来源/拥有方 |
|---|---|---|
| `memory_id` | 业务 Memory 标识 | B |
| `representation_id` | 稳定的表示标识 | 表示生产方与 C 共同确认 |
| `representation_type` | Representation 的类别标识，由表示生产方定义 | 表示生产方 |
| `provider_ref` | Provider 中的稳定引用 | P2 / Provider |
| `provider_name` | 执行 Provider 标识 | P2 / Provider |
| `logical_tiers` | 支持的逻辑层级 | P2 / Provider 能力声明 |
| `supported_operations` | `Promote`、`Demote`、`Prefetch`、`Release` 等 | P2 / Provider |
| `mapping_generation` | 映射自身的版本 | P2 / Provider |
| `valid_until` | 映射有效截止时间 | C / Provider |

### ActuationTarget 解析结果

`ResolveActuationTarget` 返回不透明的执行目标。C 只保存和转发目标引用，不解析 P2 的内部 Segment 或物理路径。

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
```

### ActuationTarget 解析结果字段含义

| 字段 | 含义 | 来源/说明 |
|---|---|---|
| `target_id` | 本次解析得到的不透明执行目标标识 | P2 / Provider 返回，C 只保存和转发 |
| `representation_id` | 本次调度对应的 Representation 标识 | C 的调度对象 |
| `provider_ref` | Provider 中的稳定对象引用 | P2 / Provider 提供，C 不解析其内部结构 |
| `provider_name` | 本次执行目标对应的 Provider 标识 | P2 / Provider 提供 |
| `source_tier` | 解析时对象实际所在的当前层级 | P2 / Provider 的实时状态 |
| `supported_operations` | 当前目标支持的动作列表 | P2 / Provider 的能力声明 |
| `target_generation` | 本次目标对象的驻留或内容版本 | 用于提交前的版本校验，不等同于映射登记版本 |
| `route_epoch` | 当前路由或切换版本 | 用于判断目标路由是否已经变化 |
| `resolved_at` | 本次目标解析完成的时间 | 用于判断解析结果的新鲜度 |
| `valid_until` | 本次解析结果的有效截止时间 | 超过后必须重新解析 |

其中，`mapping_generation` 表示映射登记本身的版本，`target_generation` 表示目标对象当前的驻留或内容版本，两者不能混用。`memory_id` 和 `representation_type` 可以通过 `representation_id` 反查；具体 Representation 类别由 B 组或对应的表示生产方另行定义，不在本交付物中冻结。

解析失败、目标不存在、映射过期或目标不支持所需动作时，C 在提交前结束本次决策并记录 `Keep / No-op` 或明确的拒绝原因，不创建可执行的 `TierAction`。解析成功不代表动作已经执行成功。

### Per-representation Hotness 基线

C 对每个 `representation_id` 独立计算热度，所有分量归一化到 `[0, 1]`，并在 `policy_version` 中保存权重和统计窗口。MVP 可采用以下启发式基线：

```text
hotness_score =
    0.40 * access_stability
  + 0.20 * importance
  + 0.20 * temporal_decay
  + 0.10 * context_use
  + 0.10 * resource_value
```

### Hotness 字段含义

| 字段 | 含义 | 来源/计算说明 |
|---|---|---|
| `hotness_score` | Representation 的综合热度分数 | 按当前 `policy_version` 的权重计算，用于触发升层、降层或保持 |
| `access_stability` | 访问稳定性 | 根据有效访问频次、命中情况和统计窗口计算，不能只使用单次搜索命中 |
| `importance` | 业务重要性 | 来自 MemorySignal 或业务侧价值判断 |
| `temporal_decay` | 最近访问的时间衰减值 | 访问越近，通常分数越高；长时间未访问则逐步降低 |
| `context_use` | 实际进入 Context 或被使用的程度 | 以 `Context Selected`、`Context Emitted` 或 `used_in_context` 为依据 |
| `resource_value` | 驻留该 Representation 的收益与成本关系 | 综合热层占用、读取收益、迁移成本和当前资源压力计算 |
| `policy_version` | 本次热度计算使用的策略版本 | 记录权重、统计窗口和阈值，保证结果可复现 |
| `input_fingerprint` | 本次计算所用输入事实的摘要标识 | 用于审计、去重和判断输入是否已经变化 |

所有热度分量和最终 `hotness_score` 使用同一套归一化口径。权重、统计窗口、衰减规则和阈值由 `PolicyContext` 管理，不应由 C 在代码中隐式写死。

其中 `access_stability` 由有效访问频次和命中情况计算，`temporal_decay` 表示最近访问的衰减值，`context_use` 只在实际进入 Context 或被使用时计入，不能用搜索候选替代。`resource_value` 用于表达驻留该表示的收益与成本关系。

默认决策起点如下，阈值和连续窗口均可配置：

| 条件 | 默认决策 |
|---|---|
| 连续两个观察窗口 `hotness_score >= 0.70` | 生成更高层级的 `desired_tier` |
| 连续三个观察窗口 `hotness_score <= 0.30`，且无 Pin / 冲突动作 | 生成更低层级的 `desired_tier` |
| 分数处于中间区间 | 保持当前层级 |
| 输入或资源状态不足 | `Keep / No-op`，等待下一轮 |

这是可复现的 Heuristic Baseline，不是把参数永久写死。权重、阈值、窗口和冷却时间变化时必须产生新的 `policy_version`，旧 Plan 和旧 Action 保留原版本。

### RepresentationPlacementPlan 契约

Plan 是 C 的 Desired State，至少记录：

```text
plan_id
representation_id
generated_at
valid_until
observed_tier
desired_tier
target_generation
policy_version
decision_reason
input_fingerprint
manual_override
```

### RepresentationPlacementPlan 字段含义

| 字段 | 含义 | 来源/用途 |
|---|---|---|
| `plan_id` | 本次放置计划的唯一标识 | C 生成，用于关联后续 TierAction、反馈和审计记录 |
| `representation_id` | 计划针对的 Representation 标识 | C 的调度对象 |
| `generated_at` | 计划生成时间 | 用于判断计划的新鲜度 |
| `valid_until` | 计划有效截止时间 | 超过后计划失效，不能直接生成 TierAction |
| `observed_tier` | 生成计划时观察到的实际层级 | 来自 Placement Observation，不代表目标层级 |
| `desired_tier` | 计划希望达到的目标层级 | C 根据热度、业务价值和资源状态计算 |
| `target_generation` | 计划生成时绑定的目标版本 | 提交前与最新观察版本比较，防止使用旧计划 |
| `policy_version` | 生成计划所使用的策略版本 | 用于复现决策和审计 |
| `decision_reason` | 生成计划的原因 | 说明 Promote、Demote、Keep 等决策依据 |
| `input_fingerprint` | 生成计划时输入事实的摘要标识 | 判断输入是否已经变化，辅助去重和审计 |
| `manual_override` | 是否存在有效人工控制 | 记录人工指定、暂停、Pin 或 Force Keep 等影响 |

其中，`observed_tier` 表示“现在在哪里”，`desired_tier` 表示“希望去哪里”；两者不同才可能产生调度动作。`target_generation` 用于确认计划仍针对当前版本，不能用旧版本计划覆盖新的对象状态。

Plan 可被使用的条件是：当前时间未超过 `valid_until`，当前 generation 与 `target_generation` 匹配，输入事实没有被更新版本替代，且没有生效的人工控制。Plan 失效时重新生成 Plan；Plan 失效或更新本身只影响调度目标，不直接创建 TierAction。
