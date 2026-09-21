# AetherStore P3 C 组交付文档

本文档是 C 组 Operate / Optimize 工作包的独立交付物，来源于《AetherStore P3 C 组设计方案 V0.1》。C 组负责调度控制面，不拥有 Memory 主事实，不实现底层存储和物理迁移。

统一责任边界如下：C 组消费运行事实，生成计划和动作，接收执行反馈，刷新真实观察并完成对账；P2 / Provider 提供真实层级、资源和物理执行结果。

本文档只保留本交付物相关内容。涉及 P2 执行方、接口字段和资源配额的部分，在尚未冻结的地方明确标记为待确认。

## 交付物说明

C-04 TierAction 状态与冲突模型

## 交付边界

本交付物冻结 TierAction 的状态映射、重复和反向动作处理、generation 冲突、反馈丢失、Unknown、重启恢复、ReconciliationTask、OperatorControlTask 及策略发布规则。

## C-04 TierAction 状态与冲突模型

### 状态约束

```text
Generated -> Submitted -> Succeeded
                       -> Failed
                       -> Unknown -> Succeeded / Failed
```

`Generated` 表示 C 已形成动作但尚未提交，`Submitted` 表示已交给执行侧，`Succeeded` 和 `Failed` 是已确认终态，`Unknown` 表示结果待确认。重试必须创建新的 `action_id`，保留 `retry_of_action_id`，不能修改旧动作假装重新执行。

### 冲突处理矩阵

| 场景 | C 的处理 | 是否创建新物理动作 |
|---|---|---|
| 同一目标、同一方向、相同幂等键重复提交 | 返回已有动作结果，Provider 端去重 | 否 |
| 同一目标已有 `Submitted`，再次收到同方向计划 | 合并计划或等待原动作结果 | 否 |
| 同一目标已有 `Submitted`，收到相反方向计划 | 阻止新动作，先查询/对账 | 否 |
| 同一目标已有 `Unknown` | 保持单一有效动作，先对账 | 否 |
| 提交前 generation 不匹配 | 使当前 Plan 失效，重新读取并计算 | 否 |
| 提交后 generation 不匹配 | 原 Action 进入 `Unknown`，创建对账任务 | 暂不创建 |
| `policy_version` 变化 | 旧 Action 按原状态收敛，新需求生成新 Plan | 视新 Plan 而定 |
| Memory 已删除或已过期 | 停止新动作；已提交动作按查询、取消或对账处理 | 视 P2 结果而定 |
| Projection 已失效 | 禁止把失效 Projection 当作有效目标，等待重建或重新计划 | 否 |
| ActuationTarget 消失 | 提交前记为拒绝；提交后进入 `Unknown` 并查询 | 视对账结果而定 |
| 反馈丢失或超时 | 置 `Unknown`，按 `action_id` 查询 | 否 |
| C 重启 | 恢复持久化动作，先查询 Provider，不重复提交 | 否 |
| `Pin` / `Force Keep` 生效 | 阻止相冲突的 `Demote` / `Release`，保留审计 | 否 |

### 对账和重试规则

对账任务依次读取 Provider 状态和最新 Placement Observation，比较 `action_id`、`provider_ref`、`actual_tier`、generation 和 `route_epoch`。只有确认原动作没有产生物理效果，才允许依据最新 Plan 创建新的 `action_id`。如果实际层级已经达到目标，即使反馈丢失，也把原动作收敛为 `Succeeded`，不能再提交一次相同动作。

策略、模型、Memory 生命周期变化不会篡改旧 TierAction。旧动作继续按原状态收敛；新的调度需求通过新 Plan 和新 Action 表达。

### ReconciliationTask 和人工控制

`ReconciliationTask` 只负责查询、比较和收敛，不因为查询本身直接发起物理迁移。它使用以下状态：

```text
Waiting -> Running -> Succeeded
                    -> Failed
                    -> Cancelled
```

`TierAction.Unknown`、反馈丢失、generation 不一致、P2 重启或 Placement 与 Action 不一致时创建对账任务。`Running` 阶段查询 `action_id`、Provider 状态和最新 Placement；确认层级与版本一致后才关闭为 `Succeeded`。证据不足时保持 `Waiting / Running`，不得直接重试；确认原动作未产生物理效果后，才允许依据新 Plan 新建 `action_id`。

`OperatorControlTask` 由有权限的运营人员创建，支持 `Pause`、`Resume`、`Pin`、`Force Keep`、`Cancel` 和人工指定目标层级。人工控制必须经过权限、作用域、有效期和原因校验，并写入 Audit Log。控制只影响新的调度准入；已经 `Submitted` 的动作继续按 Provider 反馈和对账规则收敛，除非 P2 明确提供可安全取消的能力。

### 策略和模型发布

策略或模型按 `Shadow -> Canary -> Progressive -> Rollback` 推进。`Shadow` 只计算不提交，`Canary` 小范围提交，`Progressive` 按比例放量；任一阶段出现错误率、P99、容量或一致性回归时停止放量并进入 `Rollback`。回退不删除旧 Plan、Action 或审计记录，稳定版本重新生成 Plan。

## 验收标准

### Storage Control Simulator

在真实 P2 未就绪时，C 使用共享的 `SimulatedStorageState` 先验证控制面。模拟状态至少保存 `current_tier`、`generation`、`provider_task_id`、`action_id`、反馈状态和查询记录；测试驱动器可以注入反馈延迟、反馈丢失、明确失败、重复提交和重启。

```text
Case 1：正常 ACCEPTED -> RUNNING -> SUCCEEDED，层级更新
Case 2：P2 FAILED，保持原层级并进入 Failed
Case 3：反馈丢失但实际已完成，对账后进入 Succeeded
Case 4：反馈丢失且实际未执行，对账后允许新 action_id 重试
Case 5：重复 action_id，只返回已有结果，不重复执行
Case 6：generation 不一致，拒绝旧 Plan 并重新计算
```

模拟器只能证明 C 的状态机、幂等和对账逻辑，不能替代真实 Redis、P2 和物理迁移验收。P2 Ready 后必须在同一套断言下完成真实联调。

| 编号 | 验收场景 | 必须证明的结果 |
|---|---|---|
| C-01 | 重复 Signal / Trace、补发和重放 | 不重复生成动作，消费游标可恢复 |
| C-02 | Representation 映射和目标失效 | 目标不可解析时不提交物理动作，C 不依赖 P2 内部类型分支 |
| C-03 | 正常调层 | `Generated -> Submitted -> Succeeded`，且实际层级和版本校验通过 |
| C-04 | 重复、反向动作和 generation 冲突 | 同一目标最多一个有效物理动作，旧 Action 不被篡改 |
| C-05 | 资源预算和压力 | Working 预留不被 Prewarm 挤占，预算耗尽时停止新增动作 |
| C-06 | Prewarm 命中和未命中 | 命中可关联 `action_id`，未命中可降级和释放，不能误计为访问 |
| C-07 | 反馈丢失、Unknown 和重启 | 先查询和对账，不盲重试；C 重启后不重复执行 |
| C-08 | Release 失败或状态未知 | 保留原副本，不提前修改 `current_tier`，进入对账 |
| C-09 | P2 不可用或状态过期 | 只允许 Keep / 查询 / 对账，恢复后再继续非 Keep 动作 |
| C-10 | 日志和审计 | 可由 `action_id` 串起 Plan、Submit、Feedback、Observation 和 Reconciliation |
