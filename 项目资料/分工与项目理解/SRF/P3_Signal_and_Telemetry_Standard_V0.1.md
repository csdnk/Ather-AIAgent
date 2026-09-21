# P3 Signal and Telemetry Standard V0.1

版本：V0.1  
项目：AetherStore P3  
责任域：Shared Runtime Foundation  
主要负责人：杨鹏通  
协作负责人：沈家隆  
计划窗口：2026-09-09～2026-11-13

## 1. 文档定位

本文是 Shared Runtime Foundation 的第二份交付物，统一 P3 的 Signal、事件、访问事实、外部观察、技术 Trace、运行日志和审计记录的口径。

目标是让 A、B、C、P2 能回答：谁产生了这条事实、事实针对什么对象、发生在什么时候、是否可靠保存、是否已经交给指定消费者、消费者处理到哪一步、丢失后如何补发和查询。

本文只定义字段含义和交接规则，不定义字段数据类型，不把所有记录合并为一个大日志，也不把技术调用成功解释为业务成功。

## 2. Signal 与 Telemetry 的区别

| 名称 | 表达内容 | 主要生产方 | 主要消费者 | 是否可作为业务完成证据 |
|---|---|---|---|---|
| Signal / Domain Event | 权威业务事实已经发生，例如记忆版本变化 | B、A、C 或对应事实权威方 | 其他业务流、投递机制 | 只能证明事件事实已提交，不证明消费者完成 |
| AccessTrace | 真实访问阶段，例如候选被选中、正文被加载 | Recall 或实际观察组件 | C、统计、审计 | 只能证明记录的访问阶段 |
| PlacementObservation | 某时刻真实放置状态 | P2 / Provider | C、A 按需消费 | 可作为物理观察证据，不能证明动作意图 |
| ResourceState | 某资源范围的容量、压力和健康情况 | P2 / Provider 或监控能力 | C、验收 | 可作为准入输入，不能代替物理动作结果 |
| ExecutionFeedback | 某次 Provider 动作的受理、过程或结果 | P2 / Provider | C、对账、审计 | 需要结合目标、版本和 Placement 才能判断动作完成 |
| OTel Trace | 技术调用链和耗时 | 各服务运行时 | 运维、性能分析 | 不单独证明业务事实 |
| Runtime Structured Log | 诊断过程、异常和内部决策上下文 | 各服务 | 运维、问题定位 | 不单独证明业务事实 |
| AuditRecord | 重要决定、责任、权限和前后证据引用 | Shared Runtime 与业务 Owner | 项目、运维、合规、验收 | 可作为责任和决定的证据，但仍引用领域事实 |

## 3. 事实来源和责任矩阵

| 事实或记录 | Producer | Authority | 触发时机 | Acquisition | Persistence | Freshness / Replay | C 的用途 |
|---|---|---|---|---|---|---|---|
| `MemorySignal` | B / Remember：杨鹏通 / 赵旭东 | B / Remember | `MemoryRecord`、资格、版本或删除屏障提交后 | Event / Outbox | `EventRecord` + 每消费者 `DeliveryRecord` | 可补发、可重放；事件本体不可变 | 更新调度输入和重算触发 |
| `AccessTrace` | Recall：陈凯 / 肖宇定义阶段语义并负责最终输出；实际观察组件产生事实 | 产生真实阶段的组件；Recall 对最终输出负责 | 检索、校验、加载、选择、上下文交付真实发生后 | Event / API / Trace bridge，按场景确认 | 业务事实持久保存；技术 Trace 可单独采样 | 事件按身份去重；迟到数据允许修正统计 | 计算表示热度和效果 |
| `PlacementObservation` | P2 / Provider 适配 | P2 / Provider | 查询或动作后观察到真实放置时 | API / Poll / Provider event | 观察记录及来源证据 | 通过观察时间判断 Fresh / Stale；可按范围重查 | 对比计划和动作结果 |
| `ResourceState` | P2 / Provider、监控或配额服务 | 对应资源事实提供方 | 周期采集、动作准入前或告警变化时 | Poll / Snapshot / Event | 资源观察快照 | 超过 Profile 窗口标 Stale；关键数据不可用时拒绝依赖它的新动作 | 容量保护和准入 |
| `ExecutionFeedback` | P2 / Provider 适配 | P2 / Provider | Provider 返回受理、运行、完成或失败结果时 | Callback / Poll / Query | 反馈记录和原始证据引用 | 通过 `action_id`、Provider 操作身份关联；回包丢失走 Query | 推进 Action 或启动对账 |
| `DeliveryRecord` | Shared Runtime | Shared Runtime 的投递机制；消费者提供业务确认 | 事件进入投递、重试或确认流程时 | Outbox / Inbox / Broker | 持久保存 | 可重放；每个消费者独立记录 | 证明 C 是否接收并处理 |
| `AuditRecord` | 相关责任方 | Shared Runtime 提供结构，业务 Owner 提供业务原因 | 重要状态、权限、动作、删除和人工干预发生时 | Runtime hook / API | 持久保存 | 不能依赖采样；按审计保留策略保存 | 解释调度和对账决定 |

## 4. 公共 Event Envelope

所有跨组 Signal 和需要可靠交接的领域事件使用统一外层封套。事件载荷由生产方负责，外层关联和交付语义由 Shared Runtime 统一。

| 字段 | 含义 | 适用规则 |
|---|---|---|
| `event_id` | 事件事实的稳定身份 | 同一事实重发沿用原值；事实变化产生新事件 |
| `event_type` | 事件业务含义 | 不用一个泛化类型隐藏不同事实 |
| `schema_version` | 事件结构版本 | 与业务版本、状态修订、Provider 代次分开管理 |
| `producer` | 实际产生事件的组件 | 适配器和领域权威方可以不同，均需记录 |
| `authority` | 对载荷事实负责的权威方 | 消费者据此决定向谁查询和确认 |
| `owner_flow` | 所属业务流或公共能力 | 使用 `Remember`、`Recall`、`Operate` 或对应能力域 |
| `tenant` / `scope` | 适用的租户和隔离范围 | 存在字段不等于已经授权，消费者仍需校验 |
| `subject_type` | 事件主要针对的对象类别 | 例如 `MemoryRecord`、`ProjectionState`、`TierAction` |
| `subject_id` | 事件主要对象的身份 | 必须能在对应权威域查询 |
| `subject_version` | 事件适用的业务或对象版本 | 适用时携带；不能用不透明版本字符串自行排序 |
| `state_revision` | 对象状态修订 | 只在权威方定义了可比较顺序时比较新旧 |
| `event_sequence` | 同一权威对象的有序事件序号 | 发现缺口时补拉或重建，不直接跳过 |
| `occurred_at` | 业务事实发生时间 | 由事实生产方提供；不能用到达时间冒充 |
| `observed_at` | 外部事实被观察到的时间 | 主要用于 Placement、Resource 和访问观察 |
| `recorded_at` | 本地可靠保存时间 | 用于排查延迟，不代替发生时间 |
| `request_id` | 触发本次业务请求的关联身份 | 同一请求跨服务传递 |
| `trace_id` | 技术调用链关联身份 | 技术 Trace 可采样，但关键业务记录不能依赖采样 |
| `causation_id` | 直接触发当前事件的命令或事件 | 用于解释因果关系 |
| `correlation_refs` | 相关对象集合 | 可关联 `memory_id`、`recall_id`、`task_id`、`plan_id`、`action_id` 等 |
| `payload` | 当前事件所需的业务事实 | 由业务 Owner 定义，不在公共层重复解释 |
| `evidence_refs` | 原始反馈、查询、观察或文件证据引用 | 结果未知时尤其必须保留 |
| `idempotency_key` | 适用于命令或副作用的幂等身份 | 不替代 `event_id` 的事件去重身份 |

## 5. MemorySignal 标准

### 5.1 用途

`MemorySignal` 表达 B 已经提交的记忆或表示资格变化，主要包括创建、更新、删除、归档、过期、投影资格变化和授权撤销。它是通知，不是给 C 或 P2 的调度命令。

### 5.2 最小字段语义

| 字段 | 说明 | C 的消费规则 |
|---|---|---|
| `event_id` / `signal_id` | 本次 Signal 的稳定身份及兼容映射 | 以事件身份去重，不因重复投递重复加热 |
| `event_type` | 记忆或资格变化的业务事件 | 删除、撤权、版本更新不能被普通合并吞掉 |
| `memory_id` | 记忆业务身份 | 只用于定位，不能单独决定当前版本 |
| `memory_version` | 本次变化对应的记忆版本 | 调度输入必须绑定适用版本 |
| `memory_type` | B 已确认的业务类型 | 不直接映射成物理层级 |
| `lifecycle` | 当前业务生命周期语义 | 归档、过期、删除会改变 C 的动作准入 |
| `importance` | B 维护的业务重要度 | 作为热度输入之一，不由 C 改写 |
| `scope` | 记忆适用的范围 | C 消费前校验自身操作范围 |
| `state_revision` | B 主事实状态修订 | 旧修订不能覆盖新输入 |
| `occurred_at` | 业务变化发生时间 | 用于衰减和排序，不能用接收时间替代 |
| `representation_refs` | 已知表示或资格变化的关联 | 仅使用能核实版本和身份的引用 |
| `evidence_refs` | B 的提交、资格或删除证据 | 证据不足时保留待核验，不伪造状态 |
| `policy_version` | 产生该判断时使用的业务策略版本 | 只解释 B 的判断，不替代 C 策略版本 |

### 5.3 投递确认

`MemorySignal` 提交后是不可变事实。对于 C 这一消费者，另建一条 `DeliveryRecord`：

```text
Pending -> Sent -> Acknowledged
                 \-> RetryScheduled -> AttentionRequired
```

C 只有在完成去重登记并可靠保存自己的输入视图或待办后，才能确认业务消费完成。C 消费完成不等待 `TierAction` 成功，也不回写 B 的 `MemoryRecord`。

## 6. AccessTrace 标准

### 6.1 阶段定义

| `event_stage` | 证明的事实 | 可进入热度计算的含义 |
|---|---|---|
| `retrieved` | 后端或 Working 路径发现了候选 | 检索覆盖；权重低于实际使用 |
| `validated` | 候选通过当前资格、版本和权限检查 | 过滤通过；尚不证明内容已读 |
| `loaded` | 某版本正文或表示实际被加载 | 读取成本和真实加载 |
| `selected` | 组装过程中选中了候选 | 选择行为；可能因预算后续移除 |
| `used_in_context` | 条目进入交付给上层的 ContextPack | 当前 P3 可确认的最高上下文交付阶段；不证明模型采用 |

工作路径不一定产生 `retrieved`，内联内容不一定产生外部 `loaded`。只记录实际发生的阶段，不把五个阶段强行补齐。

### 6.2 最小字段语义

| 字段 | 说明 | C 的消费规则 |
|---|---|---|
| `access_trace_id` / `event_id` | 访问事实的稳定身份 | 同一访问重复投递只统计一次 |
| `request_id` / `trace_id` | 访问所属请求和技术链路 | 用于按请求聚合和端到端排查 |
| `recall_id` | 本轮召回执行身份 | 关联候选、读取、ContextPack |
| `memory_id` | 访问的记忆身份 | 不能单独推断版本 |
| `memory_version` | 实际访问的业务版本 | 热度按版本或表示准确归属 |
| `representation_id` | 实际读取、检索或调度的表示 | 同一 Memory 的不同表示分别统计 |
| `event_stage` | 实际访问阶段 | `retrieved` 不直接算作有效使用 |
| `access_result` | 该阶段的结果，例如成功、部分、失败或未知 | 失败和未知不能当作成功命中 |
| `source` | 来源路径，例如 Working、Vector、Canonical | 用于区分来源，不用来源名猜测阶段 |
| `provider_ref` | 可验证的外部来源引用 | 只有来源关系可证明时才归因 |
| `action_id` | 触发或关联的调度动作 | 只有版本、副本和执行结果可证明关联时填写 |
| `occurred_at` | 阶段实际发生时间 | 用于时间衰减和窗口统计 |
| `recorded_at` | 事件保存时间 | 仅排查延迟和补发 |
| `evidence_refs` | 候选、读取或输出证据 | 证据不足时保持未知，不猜测 |

### 6.3 热度计算边界

C 只消费实际阶段事实。建议进入热度计算的主要字段是 `event_stage`、`access_result`、`memory_id`、`memory_version`、`representation_id`、`occurred_at`、`source` 和经过验证的 `action_id`。`trace_id`、`recorded_at` 等主要用于关联和诊断，不直接代表热度。

## 7. Placement、Resource 和 Feedback 标准

### 7.1 `PlacementObservation`

| 字段 | 说明 |
|---|---|
| `observation_id` | 观察记录身份 |
| `representation_id` | 被观察的逻辑表示 |
| `provider_ref` | 外部 Provider 或对象引用 |
| `current_tier` | 观察到的实际驻留层级 |
| `generation` | Provider 物理代次 |
| `route_epoch` | 路由或映射代次 |
| `readable` | 当前是否能按约定读取；以实际证据为准 |
| `observed_at` | Provider 或适配器观察时间 |
| `source` | API、Poll、事件或其他来源 |
| `evidence_refs` | 查询响应、清单或状态证据 |
| `freshness` | C 判断该观察是否仍可用于决策的结果 |

### 7.2 `ResourceState`

| 字段 | 说明 |
|---|---|
| `resource_observation_id` | 资源观察身份 |
| `resource_scope` | 资源观察范围 |
| `capacity` | 总容量或可分配容量事实 |
| `available` | 当前可用容量事实 |
| `pressure` | 资源压力等级或观察值 |
| `inflight` | 正在执行的相关工作量 |
| `supported_operations` | 当前能力实际支持的操作 |
| `observed_at` | 资源事实观察时间 |
| `freshness` | 观察新鲜度 |
| `evidence_refs` | 监控、Provider 或配额证据 |

### 7.3 `ExecutionFeedback`

| 字段 | 说明 |
|---|---|
| `feedback_id` | 反馈记录身份 |
| `action_id` | 关联的 C 动作身份 |
| `provider_task_id` | Provider 内部操作或任务身份 |
| `feedback_status` | 受理、运行、完成、失败或未知等结果语义 |
| `provider_ref` | 实际执行目标引用 |
| `input_generation` | 动作提交时使用的 Provider 代次 |
| `output_generation` | 动作后产生的 Provider 代次，适用时提供 |
| `error_reason` | 明确失败或拒绝原因 |
| `observed_at` | Provider 反馈时间 |
| `evidence_refs` | 原始反馈和查询证据 |

受理或运行只能推进过程状态。C 需要结合 `PlacementObservation`、目标范围和版本条件，才能将 `TierAction` 收口为成功或明确失败。

## 8. 投递、幂等、乱序和重放

### 8.1 生产端

1. 领域事实提交与 `EventRecord` / Outbox 可靠衔接；
2. 事实提交成功后才允许发布对应事件；
3. 同一事实重发沿用 `event_id` 和原载荷；
4. 事实发生变化生成新事件，不覆盖历史载荷；
5. 事件序号有缺口时允许补拉完整快照或缺失事件。

### 8.2 消费端

1. 按 `consumer + event_id` 去重；
2. 在保存消费结果、视图变化或待办后再确认业务处理完成；
3. 乱序事件按 `state_revision` 或事件序号校验，不能仅按到达时间合并；
4. 删除、撤权和 Tombstone 事件具有优先保护，不能被“只保留最后一条”丢弃；
5. 超出自动重试预算后进入 `AttentionRequired`，保留事实和责任，不报告整条链路成功。

### 8.3 重试和未知

- 事件投递重试沿用同一个 `event_id`；
- 同一外部动作的传输重试优先查询原 `action_id`、Provider 操作身份或幂等键；
- 新的业务动作才生成新的 `action_id`；
- `Unknown` 表示结果不可确认，必须创建或关联对账任务；
- `Accepted`、`Sent` 或 Broker 收到不能直接当作业务处理完成。

## 9. 四类记录的边界

| 记录 | 必须回答的问题 | 不应承载的内容 |
|---|---|---|
| `AccessTrace` | 业务访问的哪一阶段真实发生了 | 不承载全部技术 Span，也不猜模型采用 |
| OTel Trace | 技术调用经过哪些服务、耗时和错误在哪 | 不用采样结果证明删除、动作成功或业务消费完成 |
| Runtime Structured Log | 运行时做了什么诊断和内部处理 | 不作为唯一业务事实来源 |
| `AuditRecord` | 谁在什么权限和依据下做了什么重要决定 | 不复制完整正文和所有高频技术日志 |

四类记录使用统一的 `request_id`、`trace_id`、对象 ID 和版本关联，但分别保存生产方、权威方和证据含义。

## 10. 责任分工与计划

| 工作 | 责任人 | 协作人 | 时间窗口 | 主要输出 |
|---|---|---|---|---|
| Event Envelope 与 MemorySignal | 杨鹏通 | 沈家隆；B：赵旭东；C：沈家隆 / 谭旭梁 | 2026-09-09～2026-09-18 | 字段语义、版本、幂等和投递确认 |
| AccessTrace 口径 | 杨鹏通 | Recall：陈凯 / 肖宇；Operate：沈家隆 / 谭旭梁 | 2026-09-09～2026-09-24 | 阶段定义、来源、结果和去重规则 |
| Placement / Resource / Feedback | 沈家隆 | P2：刘佳正 / 陈晔 / 张晋 / 胡孝阳 / 杨文博 / 徐博文；External Integration：陈凯 / 赵旭东 / 谭旭梁 | 2026-09-19～2026-10-16 | 观察、反馈、代次和证据映射 |
| Delivery / Replay / Audit | 杨鹏通 | 沈家隆；环境验收：陈凯 / 肖宇 | 2026-09-21～2026-11-06 | Outbox、Inbox、重放、审计和故障用例 |
| RC 接口冻结 | 沈家隆 / 杨鹏通 | Remember：杨鹏通 / 赵旭东；Recall：陈凯 / 肖宇；Operate：沈家隆 / 谭旭梁；P2：刘佳正 / 陈晔 / 张晋 / 胡孝阳 / 杨文博 / 徐博文；Provider 联系人待确认 | 2026-11-07～2026-11-13 | 公共事件与遥测契约 v0.9 |

| 工作项 | 人天 |
|---|---:|
| 设计 | 4 |
| 开发 | 6 |
| 测试 | 4 |
| 联调 | 2 |
| **总人天** | **16** |

## 11. 验收标准

1. MemorySignal、AccessTrace、PlacementObservation、ResourceState 和 ExecutionFeedback 均能说明 Producer、Authority、对象身份、版本、时间和证据来源。
2. 同一事件重复投递时，C 不重复计热度、不重复创建调度动作。
3. 事件事实、投递状态、任务状态、外部反馈和业务结果可以分别查询。
4. `retrieved`、`validated`、`loaded`、`selected`、`used_in_context` 的含义不混淆；Search Hit 不单独等于实际使用。
5. Provider 的 Accepted、Running、Succeeded、Failed、Unknown 能映射到相应事实，不以受理冒充完成。
6. 事件乱序、缺口、Ack 丢失、重放、重启和超出预算进入人工处理均有可重复用例。
7. 根据 `request_id`、`trace_id`、`memory_id`、`recall_id` 或 `action_id` 可以追溯关联记录。
8. 本文不定义任何字段数据类型；具体 Schema 版本、传输协议和持久化位置在公共契约评审中冻结。
