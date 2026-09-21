# PROGRAM C — Operate / Optimize · Work Package V1.0

> Primary DRI = C。冻结架构：V0.4.1。任务编号与主 WBS 一致。
> Outcome：从 MemorySignal/AccessTrace 到调度决策、TierAction、Feedback、Reconciliation 闭环正确，并形成可验证优化效果。**TierAction 一直 Unknown 找 C**。

## Core Chain（C 拥有）

```
MemorySignal / AccessTrace → per-representation hotness → RepresentationPlacementPlan
  → ActuationTarget → TierAction → Provider Execution → Feedback
  → Placement Observation → Reconciliation → Optimization
```

## Epics

| Epic | 目标 | 关键 Task（主 WBS ID） | 冻结 DoD 摘要 |
|---|---|---|---|
| C-EPIC-01 Signal/Trace Consumption | 消费 B 的 Signal + A/B 的 Trace | C-EPIC-01-T1..T2 | Submitted≠已消费；trace 可复现 |
| C-EPIC-02 Representation Hotness | per-representation 热度 | C-EPIC-02-T1 | 非笼统 Memory hot |
| C-EPIC-03 RepresentationPlacementPlan | 冻结 Domain Object + 运行时 | C-EPIC-03-T1..T2 | entries[] 全字段；Plan 是正式 Domain Fact |
| C-EPIC-04 Actuation Target Resolve | opaque target，不绑 Segment | C-EPIC-04-T1..T2 | P3 自己 memory_id→representation_id→provider_ref；禁按 target_type 分支 |
| C-EPIC-05 Scheduling View/Observation | 真实 observed tier + 段级观测 | C-EPIC-05-T1..T2 | 只消费不拥有；段级不反推对象级 |
| C-EPIC-06 Placement Decision | desired tier + 防抖 + 回退 | C-EPIC-06-T1..T2 | policy_version + decision_reason；状态不足→Keep/No-op |
| C-EPIC-07 TierAction Runtime | 状态机 + P3↔Provider 映射 | C-EPIC-07-T1..T2 | accepted≠success；Unknown→Reconcile |
| C-EPIC-08 Storage Control Simulator | 统一 Simulator + 六用例 | C-EPIC-08-T1..T2 | 共享 SimulatedStorageState；Case 1–6 |
| C-EPIC-09 Feedback + Reconciliation | 对账 + 不盲重试 | C-EPIC-09-T1..T2 | 先确认真实状态再收敛 |
| C-EPIC-10 Optimize/Predict | 启发式基线→特征→预测+回退 | C-EPIC-10-T1..T3 | 不直接跳 RL；回退链完整；命中率 +10% 可审计 |

## 完整 DoD（TierAction 闭环，C-EPIC-07/09）

1. Generated/Submitted/Succeeded/Failed/Unknown 状态机完整。
2. 与 Provider ACCEPTED/RUNNING/SUCCEEDED/FAILED/UNKNOWN 映射正确。
3. `accepted/submitted ≠ succeeded`；Succeeded 仅收 P2 SUCCEEDED + actual_tier。
4. Unknown/超时 → 先 query(action_id) 确认真实状态，不盲重试。
5. 反馈丢失/stale placement/generation mismatch 可由 Reconciliation 收敛。
6. duplicate action_id 不重复执行。
7. P3 重启后 query 仍有效（Simulator 独立进程或持久化）。
8. 回退链：预测→上一稳定→Heuristic→Keep/No-op。
9. Simulator Case 1–6 Contract/Fault Test PASS。
10. 真实 P2 未 Ready：Lane 2 PASS；Ready 后：Lane 3 PASS。

## Handoff 表

| 方向 | Capability | Provider | Consumer | C 无需知道 | 对方无需知道 |
|---|---|---|---|---|---|
| B → C | MemorySignalCapability | B | C | signal 内部存储 | 调度决策（C 执行） |
| A/B → C | AccessTrace | A/B（Producer）+ Steward（Schema） | C | trace 采集细节 | 决策（C 执行） |

## First Batch（C 第一批产出）

1. `C-EPIC-03-T1`：RepresentationPlacementPlan Domain Object（冻结字段）。
2. `C-EPIC-07-T1`：TierAction 状态机 + P3↔Provider 映射。
3. `C-EPIC-08-T1/T2`：Storage Control Simulator（统一 + 共享 SimulatedStorageState + Case 1–6）。
4. `C-EPIC-06-T2`：回退链（Keep/No-op 兜底）。

> C 的前置依赖是 B 的 MemorySignal 契约（B First Batch 未含 signal，需尽早与 B-EPIC-07 对齐）与 S-EPIC-04 AccessTrace 契约；Simulator 可完全独立先行。

## 显式非责任（C）

- 不拥有 Memory 主事实（B）、不拥有向量机制（A）、不拥有物理迁移执行/真实层级事实（P2/Tier Executor）、不替 A/B 定义 signal/trace 的 Schema（Steward 拥有 Schema/Ingest 契约）。

---

*PROGRAM C 工作包完。*
