# PROGRAM A — Production Readiness Addendum V1.0
# Recall / Context Serving（Primary DRI = A）

> 归一化版：每个 Action 带 **Parent Gap ID**（指向 `PRR_Gap_Register_V1.0` 的 Theme）。
> 层级：`Gap Theme（Register）→ Action（本文件）→ Owner → Test → Acceptance Evidence`。
> 只写"现有 Recall Work Package 需**增加**的生产工作"，不复制 WBS。

## 1. Production Outcome
Recall/Context 读路径在生产环境：容量安全、可降级、可追踪、可解释、依赖故障不雪崩。

## 2. Critical Path / SLO
`Query → Embedding → Search → Read → Load → Rank → Context Pack`
- Embedding Effective Throughput ≥ 2000 item/s（[A]）
- Recall/Context 端到端 P99 + degraded 率（口径见 P1-1，正式值以 Acceptance Profile 冻结）

## 3. P0 Actions

| Action ID | Parent Gap | Action | 行为要求 | 验收 |
|---|---|---|---|---|
| A-P0-1 | **P0-1** | Recall 入口限流 + Query Embedding 队列深度上限 | 限流返回稳定 429/Busy；队列有上限；限流下 P99 不劣化 | 突发流量 Game Day |
| A-P0-2 | **P0-1** | passage/query embedding 资源隔离 | usage 分级（L1 query > L2 passage > L3 后台），前台劣化→L3 节流 | 混合负载下 query 不被抢占 |
| A-P0-3 | **P0-1** | PER-01 混合负载双 Profile | 隔离态 + 混合负载态两套 Profile 都达标（≥2000 item/s） | 双场景 Benchmark 报告 |
| A-P0-4 | **P0-5** | Recall degraded 率/缺失源告警（A 侧 Metric） | 阈值告警 + 缺失源可定位 | 告警触发 + 定位证据 |
| A-P0-5 | **P0-4** | Embedding 模型/维度阈值受控发布 | Shadow→Canary→Rollback，模型版本可追踪 | 受控发布 Drill |

## 4. P1 Actions

| Action ID | Parent Gap | Action |
|---|---|---|
| A-P1-1 | **P1-1** | Recall E2E SLO/availability 口径（complete/degraded 分类阈值） |
| A-P1-2 | **P1-5** | 候选悬空引用（object_ref/graph_node_ref）检测 |
| A-P1-3 | **P1-3** | Search 后端不可用/半故障的显式降级阈值 |
| A-P1-4 | **P1-6** | Recall 结果可复现审计（同 trace 可重放定位） |

## 5. Consumer vs Provider 边界

| 项 | A 负责（Consumer） | 外部 Provider |
|---|---|---|
| 向量检索/写 | VEC-001/002 契约消费、degraded 语义、候选有效性 | P2 E1 / Milvus（索引/ANN） |
| canonical 原文 | 经 B 映射 + ContentStorePort 加载、checksum 校验 | Durable Store Provider |
| Embedding 机制 | 拥有（B1 是 Contributor） | None |

## 6. External Dependencies
VEC-001/002（P2 E1/Milvus）、OBJ-001（经 B）、身份/trace（P4 最小 native）。

## 7. 非责任（A 不拥有）
Memory 主事实（B）、ProjectionState（B）、调度决策（C）、canonical payload 物理事实（Provider）。

---

*PROGRAM A Addendum V1.0 完。*
