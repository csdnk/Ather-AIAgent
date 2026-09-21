# P3 State Catalog V0.1

版本：V0.1  
项目：AetherStore P3  
责任域：Shared Runtime Foundation  
主要负责人：沈家隆  
协作负责人：杨鹏通  
计划窗口：2026-09-09～2026-11-06

## 1. 文档定位

本文是 P3 Shared Runtime Foundation 的第一份交付物，回答三个问题：

1. P3 需要统一管理哪些对象；
2. 每个对象的状态是什么意思、谁可以推进；
3. 一个状态变化需要哪些版本、证据、事件和恢复记录。

本文不把所有对象强行放进一条状态机，也不替 Remember、Recall、Operate 或 P2 判断业务结果。统一管理的含义是统一对象登记、状态语义、版本校验、状态转换记录、完成证据和查询方式。

字段名只用于说明跨组契约，不在本文定义字段数据类型，也不冻结底层表结构。

## 2. 统一管理口径

### 2.1 一个对象只保留一个业务权威

| 对象范围 | 业务权威 | Shared Runtime Foundation 的职责 |
|---|---|---|
| `MemoryRecord`、`ContentBinding`、`ProjectionState` | Remember：杨鹏通 / 赵旭东 | 提供受控状态写入、版本检查、事件和审计机制 |
| `RecallRequest`、`RecallExecution`、`ContextPack`、`AccessTrace` | Recall：陈凯 / 肖宇，实际观察组件提供访问事实 | 提供请求关联、事件封套、任务、投递和查询关联 |
| `SchedulingView`、`PlacementPlan`、`ActuationTarget`、`TierAction` | Operate：沈家隆 / 谭旭梁 | 提供任务、幂等、状态目录、反馈关联和恢复机制 |
| `PlacementObservation`、`ResourceState`、`ExecutionFeedback` | P2 / Provider：按能力由刘佳正、陈晔、张晋、胡孝阳、杨文博、徐博文负责 | 接入外部事实并保留来源、代次和观察时间 |
| `EventRecord`、`DeliveryRecord`、`AsyncTask`、`ReconciliationTask`、`AuditRecord` | Shared Runtime Foundation 提供公共机制 | 统一结构、执行权、投递、查询、审计和恢复 |
| `Tombstone`、`InvalidationRecord`、`CleanupRecord` | 产生删除或失效决定的业务流 | 统一屏障传播、残留追踪和恢复顺序 |

### 2.2 统一的是状态维度，不是一列状态

| 状态维度 | 解决的问题 | 典型状态 | 主要使用对象 |
|---|---|---|---|
| 业务有效性 | 业务事实当前能不能使用 | `Active`、`Archived`、`Expired`、`Deleted`、`Superseded` | `MemoryRecord`、内容和表示资格 |
| 版本当前性 | 这份版本是不是当前版本 | `Current`、`Superseded` | 记忆、内容、投影、计划和动作引用 |
| 任务执行 | 后台工作走到哪一步 | `Pending`、`Running`、`RetryScheduled`、`Succeeded`、`Failed`、`Cancelled` | `AsyncTask`、`ReconciliationTask` |
| 事件投递 | 某个消费者收到并处理到哪一步 | `Pending`、`Sent`、`Acknowledged`、`AttentionRequired` | `DeliveryRecord` |
| 外部动作 | 动作是否已提交、完成或未知 | `Generated`、`Submitted`、`Succeeded`、`Failed`、`Unknown` | `TierAction` |
| 观察新鲜度 | 当前观察还能不能用于决策 | `Fresh`、`Stale`、`Unknown` | `PlacementObservation`、`ResourceState`、派生视图 |
| 清理进度 | 失效对象各层残留清理到哪一步 | `Pending`、`InProgress`、`Completed`、`Blocked` | `CleanupRecord` |

同一个词在不同对象中必须保持含义稳定。例如 `Succeeded` 都表示“本对象自己的完成证据已具备”，不能把“消息已投递”“任务已受理”直接解释成领域对象成功。

## 3. 对象总目录

| 对象 | 业务含义 | 稳定身份与版本依据 | 业务权威 | 允许写入方 | 主要消费者 |
|---|---|---|---|---|---|
| `MemoryRecord` | 一条记忆某个业务版本的主事实 | `memory_id`、`memory_version`、`state_revision` | B / Remember | B | A、C、公共查询 |
| `ContentBinding` | 记忆版本与正文、片段或 Artifact 的准确映射 | `memory_id`、内容版本、来源修订 | B / Remember | B | A、投影流程、清理流程 |
| `Chunk` / `Artifact` | 正文片段或加工产物 | 内容身份、来源版本、生成代次 | B / Remember 或对应加工能力 | 受授权的内容流程 | 投影、Recall、审计 |
| `ProjectionState` | 某表示或投影的构建和可用资格 | `representation_id`、`memory_version`、build 代次 | B / Remember | B | A、C |
| `RecallRequest` | 一次上层召回请求 | `request_id`、Scope、请求修订 | A / Recall | A / 调用方 | Recall 内部、审计 |
| `RecallExecution` | 一次召回请求的执行过程 | `recall_id`、`request_id`、执行代次 | A / Recall | A | A、公共查询 |
| `RecallCandidateSet` | 本次发现结果及其完整性 | `recall_id`、搜索代次、来源 | A / Recall | A | A、审计 |
| `RecallCandidate` | 某个候选及其来源和版本引用 | `candidate_id`、`memory_id`、`memory_version` | A / Recall，资格事实来自 B | A | A、AccessTrace |
| `ContextPack` | 交付给上层的上下文结果 | `recall_id`、pack 代次 | A / Recall | A | 上层调用方、审计 |
| `AccessTrace` | 真实发生的访问阶段事实 | `access_trace_id`、`request_id`、阶段序号 | 产生观察的组件；Recall 对最终输出负责 | 实际观察组件 | C、统计、审计 |
| `SchedulingView` | C 根据已知事实形成的调度输入视图 | `view_id`、输入水位、`memory_id` 或 `representation_id` | C / Operate | C | C 决策和诊断 |
| `PlacementPlan` | 对某表示的期望驻留和约束计划 | `plan_id`、`representation_id`、计划版本 | C / Operate | C | `ActuationTarget`、`TierAction` |
| `ActuationTarget` | 将逻辑目标解析为可执行的不透明目标 | `target_id`、目标代次、有效期 | P2 / Provider 能力，C 保存引用 | 目标解析能力 | C、动作执行通道 |
| `TierAction` | 一次调度意图和外部动作记录 | `action_id`、`plan_id`、幂等身份 | C / Operate | C | P2、对账、审计 |
| `ProviderBinding` | 逻辑表示与外部对象、索引或副本的映射 | `binding_id`、逻辑版本、Provider 代次 | 相关业务流维护逻辑关系，Provider 提供物理依据 | 受授权的适配层 | A、B、C、P2 适配 |
| `PlacementObservation` | 某时刻真实放置状态的观察 | `observation_id`、`representation_id`、`generation`、观察时间 | P2 / Provider | P2 / Provider 适配 | C、A 按需消费 |
| `ResourceState` | 某资源范围的容量、压力和健康事实 | `resource_observation_id`、资源范围、观察时间 | P2 / Provider 或监控能力 | P2 / Provider 适配 | C、验收 |
| `ExecutionFeedback` | Provider 对某次动作的过程和结果反馈 | `feedback_id`、`action_id`、Provider 操作身份 | P2 / Provider | Provider 适配 | C、对账、审计 |
| `MemorySignal` | Remember 已提交的记忆或资格变化事件 | `event_id`、`memory_id`、`memory_version`、事件序号 | B / Remember | B | A、C、投递机制 |
| `EventRecord` | 已提交的不可变事件事实 | `event_id`、事件类型、Schema 版本 | 产生事件的权威方 | 事件生产方 | 投递、重放、审计 |
| `DeliveryRecord` | 某事件面向某消费者的投递进度 | `event_id`、消费者、投递修订 | Shared Runtime 维护机制，消费者提供业务确认 | Shared Runtime | 生产方、消费者、运维 |
| `AsyncTask` | 一项可恢复后台工作 | `task_id`、业务对象引用、任务代次 | 发起业务流定义语义 | Shared Runtime 运行机制，业务流提交目标 | B、A、C、P2 适配 |
| `ReconciliationTask` | 对未知或不一致结果的查询和收口任务 | `reconciliation_task_id`、原对象、原操作 | Shared Runtime 维护机制，业务流定义比较规则 | Shared Runtime 运行机制 | B、C、P2 适配 |
| `AuditRecord` | 重要操作、判断和责任证据 | `audit_id`、关联对象和链路 | Shared Runtime 提供公共记录，领域提供原因 | 相关责任方 | 项目、运维、验收 |
| `Tombstone` | 版本、对象或引用失效屏障 | 失效对象、范围、修订和生效依据 | 产生删除或撤权决定的业务流 | 业务权威方 | A、B、C、Provider |
| `InvalidationRecord` | 传播失效、撤权或版本退出的记录 | 失效批次、对象引用、水位 | Shared Runtime 记录传播，业务流给出范围 | 业务流与公共机制 | 各派生视图 |
| `CleanupRecord` | 正文、投影、Working、热副本等残留的清理进度 | 清理批次、对象、层次、结果 | 业务流汇总，Provider 提供物理证据 | 受授权清理方 | 业务流、验收 |

## 4. 对象状态语义

### 4.1 Remember 对象

| 对象 | 状态 | 含义 | 进入条件 | 完成或退出证据 |
|---|---|---|---|---|
| `MemoryRecord` | `Active` | 当前业务版本有效，可按授权使用 | B 完成主事实提交和资格判断 | 当前版本和状态修订可查 |
| `MemoryRecord` | `Archived` | 业务上保留，但普通路径不再使用 | B 提交归档决定 | 归档原因、时间和授权规则 |
| `MemoryRecord` | `Superseded` | 被更新版本替代 | 新版本成为当前版本 | 新旧版本关系和提交证据 |
| `MemoryRecord` | `Expired` | 业务有效期结束 | 有效期或规则判定到期 | 到期依据和读取资格更新 |
| `MemoryRecord` | `Deleted` | 业务读取资格撤销，进入清理 | B 提交删除屏障 | `Tombstone` 和清理范围 |
| `ProjectionState` | `Pending` | 已登记，尚未开始构建 | B 产生投影需求 | 投影目标、版本和任务引用 |
| `ProjectionState` | `Building` | 正在构建或等待结果 | 构建任务取得执行权 | 执行记录和输入指纹 |
| `ProjectionState` | `Ready` | B 已确认本版本可按规定使用 | 完整输入、版本、Provider 证据和 Ready Guard 全部满足 | `ReadyProof` 或等价证据 |
| `ProjectionState` | `Failed` | 本次构建已明确失败且不再继续 | 明确错误或超过业务预算 | 错误原因和后续动作 |
| `ProjectionState` | `Stale` | 曾经可用，但当前依据不再可靠 | 版本、模型、内容或物理投影发生变化 | 失效原因和重建入口 |

`ProjectionState` 的 `Ready` 只能由 B 的领域规则确认；A 或 P2 返回的 `READY` 只是机制证据，不能直接改写 B 的业务状态。

### 4.2 Recall 对象

| 对象 | 状态或阶段 | 含义 | 说明 |
|---|---|---|---|
| `RecallRequest` | `Received`、`Running`、`Completed`、`Failed`、`Cancelled` | 请求执行进度 | 受理不代表有可信上下文 |
| `RecallExecution` | `Running`、`Completed`、`Degraded`、`Failed` | 本轮召回过程结果 | `Degraded` 需要说明缺失来源或降级原因 |
| `ContextPack` | `Building`、`Emitted`、`Degraded`、`Failed` | 上下文是否组装并交付 | `Emitted` 只证明已交给调用方，不证明模型采用 |
| `AccessTrace` | 阶段事实追加 | `retrieved`、`validated`、`loaded`、`selected`、`used_in_context` | 不是必须走完的状态链，只记录真实发生的阶段 |

### 4.3 Operate 对象

| 对象 | 状态 | 含义 | 完成证据 |
|---|---|---|---|
| `SchedulingView` | `Fresh`、`Stale`、`Rebuilding` | 调度输入是否可以使用 | 输入水位、观察时间和缺失项可查 |
| `PlacementPlan` | `Draft`、`Valid`、`Superseded`、`Expired`、`Rejected` | 期望状态计划是否仍有效 | 输入版本、策略版本、目标范围和失效原因 |
| `ActuationTarget` | `Resolved`、`Expired`、`Invalid` | 逻辑目标是否解析为当前可用目标 | 目标来源、目标代次和有效期 |
| `TierAction` | `Generated` | C 已形成动作意图，尚未证明提交 | 动作载荷、计划版本和准入记录 |
| `TierAction` | `Submitted` | Provider 已给出可确认的受理事实 | Provider 操作身份或受理证据 |
| `TierAction` | `Succeeded` | 本次动作完成契约和适用观察均满足 | 反馈、目标、版本及放置观察一致 |
| `TierAction` | `Failed` | 本次动作已确认未完成 | 明确错误或 Provider 最终失败证据；不保证没有残留 |
| `TierAction` | `Unknown` | 无法确认是否已发生或最终效果 | 回包丢失、查询不可用或证据冲突 |

`PlacementObservation` 和 `ResourceState` 不使用动作状态机。它们是带来源、时间、范围和新鲜度的观察记录，不能由 C 根据计划推算出来。

### 4.4 公共对象

| 对象 | 状态 | 统一要求 |
|---|---|---|
| `EventRecord` | 已提交事实 | 事件提交后不可因消费者未确认而改成业务失败；事实变化产生新事件 |
| `DeliveryRecord` | `Pending`、`Sent`、`Acknowledged`、`RetryScheduled`、`AttentionRequired` | 每个消费者单独记录，不能用一个全局状态代表所有消费者 |
| `AsyncTask` | `Pending`、`Running`、`RetryScheduled`、`Succeeded`、`Failed`、`Cancelled`、`Expired` | `Succeeded` 必须覆盖任务自己的 `required_outputs` |
| `ReconciliationTask` | `Pending`、`Running`、`RetryScheduled`、`Succeeded`、`Failed`、`ManualAttention` | 任务成功表示差异已有明确结论，不表示原动作一定成功 |
| `AuditRecord` | `Recorded`、`Archived` | 关键决定和证据不可依赖采样日志恢复 |
| `CleanupRecord` | `Pending`、`InProgress`、`Completed`、`Blocked` | 分开记录逻辑失效、索引清理、在线副本清理和物理清理 |

## 5. 状态转换统一规则

每次可变状态转换必须记录以下内容：

```text
对象及精确版本
预期当前状态和 state_revision
发起者、权限和 Scope
触发命令、事件、观察或查询证据
业务 Guard 和 policy_version
新状态及新的 state_revision
关联事件、任务、审计和恢复入口
```

统一约束如下：

1. 旧版本、旧执行者或旧 `state_revision` 不能覆盖当前状态；
2. 重复事件按 `consumer + event_id` 去重，不重复推进状态；
3. `Accepted`、`Submitted`、`Running` 只表达受理或过程，不表达最终成功；
4. `Unknown` 不得被包装成 `Failed` 或 `Succeeded`，必须保留查询和对账责任；
5. 已确认终态不被迟到结果直接回退，纠正必须新增审计记录；
6. 业务事实与投递、任务、观察、物理执行事实分开记录；
7. 状态转换成功不等于下游消费成功，跨组交接通过事件和投递记录追踪。

## 6. 跨对象影响矩阵

| 变化 | 必须更新或检查的对象 | 不允许的处理 |
|---|---|---|
| `MemoryRecord` 新版本成为当前 | `ContentBinding`、`ProjectionState`、`MemorySignal`、Recall 资格、`SchedulingView` | 不让旧版本任务写回当前版本 |
| 内容版本变化 | 内容映射、投影输入指纹、Recall 候选和 C 计划 | 不把旧正文和新向量拼成一个成功表示 |
| `ProjectionState` 变为 `Stale` | Recall 可用性、投影修复任务、C 调度输入 | 不把 Stale 当 Ready，也不伪造长期可召回 |
| `AccessTrace` 新增真实访问阶段 | C 的热度输入、关联查询和统计 | 不把 search hit 直接当作 context 使用 |
| `PlacementPlan` 失效 | `ActuationTarget`、待提交 `TierAction`、对账任务 | 不执行依赖已失效计划的新动作 |
| `TierAction` 结果未知 | 原动作查询、`ReconciliationTask`、最新观察 | 不换 ID 盲目重复物理动作 |
| `PlacementObservation` 改变 | `SchedulingView`、后续计划和冲突检查 | 不回写历史动作成功事实 |
| `MemoryRecord` 删除或撤权 | `Tombstone`、各派生关系、`CleanupRecord`、在途任务 | 不让迟到事件或备份重新发布失效对象 |
| 共享物理目标发生相反动作 | 目标影响范围、动作冲突和 Provider fencing | 不只按 `memory_id` 加锁后并行执行 |

## 7. 版本、删除和对账要求

### 7.1 版本要求

`memory_id` 只标识业务身份，不能单独决定当前可读内容。读取、投影、调度和清理至少要带上适用的 `memory_version` 或不可变快照。`generation` 是 Provider 物理代次，不等同于业务版本；`state_revision` 是状态修订，也不等同于 Provider 代次。

### 7.2 删除要求

删除必须先由 Remember 提交业务屏障，再由公共机制传播失效，最后按对象和物理层次逐项清理。`CleanupRecord` 至少能说明：

- 哪个版本或范围失效；
- 正文、Artifact、Projection、Working、热副本和备份分别处于什么进度；
- 是否存在共享引用；
- 哪个 Provider 返回了什么完成证据；
- 失败、未知或阻塞后下一步由谁负责。

### 7.3 对账要求

对账闭环为：读取当前权威事实 → 查询原操作和外部目标 → 记录差异 → 由业务 Owner 决定补偿 → 执行获准工作 → 再验证。Shared Runtime 提供任务、租约、退避、预算和审计；业务 Owner 提供比较规则和收口决定。

## 8. 责任矩阵

| 工作 | 主要责任人 | Shared Runtime Foundation 的交付 |
|---|---|---|
| 对象登记、身份和版本规则 | 沈家隆；Remember：杨鹏通 / 赵旭东；Recall：陈凯 / 肖宇；Operate：沈家隆 / 谭旭梁；P2：刘佳正 / 陈晔 / 张晋 / 胡孝阳 / 杨文博 / 徐博文提供业务定义 | 对象目录、关系查询和版本兼容检查 |
| 状态名称、转换和完成证据 | 沈家隆；杨鹏通协作 | State Catalog、状态变更记录和公共校验 |
| Remember 主事实与资格 | 杨鹏通 / 赵旭东 | 受控写入、CAS、事件关联和审计 |
| Recall 请求与访问事实 | 陈凯 / 肖宇 | 请求关联、AccessTrace 外层信封和查询视图 |
| Operate 动作与对账 | 沈家隆 / 谭旭梁 | Action 任务、幂等、对账任务骨架和恢复 |
| P2 / Provider 物理事实 | 刘佳正 / 陈晔 / 张晋 / 胡孝阳 / 杨文博 / 徐博文 | 外部事实映射、来源和证据关联 |
| 跨组故障验收 | 陈凯 / 肖宇组织；Remember：杨鹏通 / 赵旭东；Recall：陈凯 / 肖宇；Operate：沈家隆 / 谭旭梁；P2：刘佳正 / 陈晔 / 张晋 / 胡孝阳 / 杨文博 / 徐博文参与 | 统一状态、重放、重启、未知和删除竞态验收 |

## 9. 计划与人天

| 阶段 | 项目日期 | 主要输出 | 责任人 |
|---|---|---|---|
| 设计 | 2026-09-09～2026-09-18 | 对象总目录、状态维度、转换和完成证据初稿 | 沈家隆 / 杨鹏通；Remember：赵旭东；Recall：陈凯 / 肖宇；Operate：谭旭梁；P2：刘佳正 / 陈晔 / 张晋 / 胡孝阳 / 杨文博 / 徐博文评审 |
| 开发 | 2026-09-21～2026-10-16 | 状态目录、版本检查、关系查询和公共写入机制 | 沈家隆；杨鹏通 |
| 测试 | 2026-10-19～2026-10-30 | 状态边界、乱序、重复、重启、删除和冲突用例 | 沈家隆；陈凯 / 肖宇协作 |
| 联调 | 2026-11-02～2026-11-06 | A/B/C/P2 状态映射、完成证据和查询验收 | 沈家隆；Remember：杨鹏通 / 赵旭东；Recall：陈凯 / 肖宇；Operate：谭旭梁；P2：刘佳正 / 陈晔 / 张晋 / 胡孝阳 / 杨文博 / 徐博文 |

| 工作项 | 人天 |
|---|---:|
| 设计 | 4 |
| 开发 | 5 |
| 测试 | 3 |
| 联调 | 2 |
| **总人天** | **14** |

## 10. 验收标准

1. A、B、C、P2 的对象目录均能指出身份、版本、Scope、业务权威、写入者、消费者和完成证据。
2. `MemoryRecord`、`ProjectionState`、`RecallRequest`、`TierAction`、`DeliveryRecord`、`AsyncTask` 和 `ReconciliationTask` 的状态含义与允许转换可执行、可查询。
3. 重复事件、乱序事件、旧版本写回和旧租约写回不会覆盖当前事实。
4. `Submitted`、`Succeeded`、`Failed`、`Unknown` 能通过不同证据区分；Unknown 有持久查询和恢复入口。
5. 删除、失效、版本更新、共享引用和物理残留均能在影响矩阵和清理记录中追踪。
6. 根据 `memory_id`、`recall_id` 或 `action_id` 能查到对象关系、任务、事件、观察和审计记录。
7. 不出现第二套与本目录冲突的主事实状态机。

## 11. 不在本文冻结的内容

- 底层表、队列、数据库或缓存的具体实现位置；
- 字段数据类型、保留时长和生产阈值；
- Provider 内部复制、迁移、切换和回收算法；
- Recall 的检索、排序和上下文业务规则；
- C 的热度模型、计划算法和 TierAction 业务判定。

以上内容需在公共契约评审和各领域实现方案中分别冻结，但不得违反本文的身份、版本、状态和证据边界。
