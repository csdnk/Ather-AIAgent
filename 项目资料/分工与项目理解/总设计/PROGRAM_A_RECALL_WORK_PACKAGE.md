# PROGRAM A — Recall / Context Serving · Work Package V1.0

> Primary DRI = A。冻结架构：V0.4.1。任务编号与 `AetherStore_P3_Engineering_WBS_V1.0.md` 一致。
> Outcome：从 Query 到最终 Context Pack 的完整读路径正确、可降级、可追踪。**Recall 最终结果错误找 A**（根因在 B 的 Memory Fact 则经契约找 B 修，A 仍 E2E Accountable）。

## Core Chain（A 拥有）

```
Query → Query Embedding → Vector Search → Candidate Validation → Memory Read
      → Canonical Load → Rank/Decay/Conflict → Context Pack
```

## Epics

| Epic | 目标 | 关键 Task（主 WBS ID） | 冻结 DoD 摘要 |
|---|---|---|---|
| A-EPIC-01 Semantic Compute | passage/query 两用 embedding + 版本/幂等 + 真实吞吐 | A-EPIC-01-T1..T4 | 合法真实向量；Effective Throughput ≥2000 item/s；B 不感知 ONNX/SIMD |
| A-EPIC-02 Vector Projection Mechanism | 向量写机制（A 只拥有 ProviderResult） | A-EPIC-02-T1..T3 | ProviderResult 五态；A 不拥有/不写 ProjectionState |
| A-EPIC-03 Vector Backend Simulator | 有状态向量后端模拟 | A-EPIC-03-T1..T2 | 幂等、index_ready、故障注入可区分 |
| A-EPIC-04 Query Runtime | Query→Query Embedding + 契约校验 | A-EPIC-04-T1..T2 | 维度/版本一致；校验失败稳定错误码 |
| A-EPIC-05 Candidate Retrieval | 向量检索 + 稳定引用 + 降级 | A-EPIC-05-T1..T3 | degraded/partial 显式；超时不冒充完整 |
| A-EPIC-06 Candidate Validation + Memory Read | 调 B MemoryRead 校验候选 | A-EPIC-06-T1 | 只用当前有效版本 Ready Projection 对应 Memory |
| A-EPIC-07 Canonical Load | 经 B 映射 + ContentStorePort 加载原文 | A-EPIC-07-T1 | checksum/not_found/partial 显式 |
| A-EPIC-08 Rank/Decay/Conflict | 消费 B 属性执行排序 | A-EPIC-08-T1 | A 对 Recall E2E 结果负责；策略 B 定义、A 消费 |
| A-EPIC-09 Context Assembly | Context Pack + Token Budget | A-EPIC-09-T1..T2 | complete/degraded/failed + truncated 正确 |
| A-EPIC-10 Observability + Acceptance | 可解释 + E2E 验收 | A-EPIC-10-T1..T2 | trace 贯通；L2 可先 PASS，L3 待真实 P2 |

## 完整 DoD（Recall E2E，A-EPIC-10）

1. Query → Context Pack 全程 trace_id 贯通。
2. 每个缺失源/降级原因可定位（projection_pending/缺失源/超时/冲突/预算截断）。
3. 使用哪个 projection/model/维度可查。
4. 哪个 Memory 被过滤、为什么，可查。
5. Context Pack complete/degraded/failed + recall_availability 正确。
6. L2（Simulator）Contract PASS；L3（真实 P2）Ready 后 PASS。

## Handoff 表（跨人契约，禁止"协作/共同负责"）

| 方向 | Capability | Provider | Consumer | A 无需知道 | 对方无需知道 |
|---|---|---|---|---|---|
| A → B | SemanticEmbeddingCapability | A | B | — | ONNX/SIMD/backend |
| A → B | VectorProjectionPort mechanism | A | B | — | ProjectionState（B 拥有） |
| B → A | MemoryReadCapability | B | A | Redis/DB/schema | 最终排序（A 执行） |
| B → A | CanonicalLoadCapability（mapping） | B | A | payload 物理权威（Durable Store） | — |

## First Batch（A 第一批产出）

1. `A-EPIC-01-T1`：SemanticEmbeddingCapability 契约（usage/model_contract/source_hash/timeout + ProviderResult 无关，输出 vector/model_version/dimension）。
2. `A-EPIC-02-T1`：VectorProjectionPort 契约（五元组幂等 + ProviderResult 五态）。
3. `A-EPIC-03-T1`：Vector Backend Simulator（有状态 + index_ready + 幂等）。
4. `A-EPIC-04-T1/T2`：Query Embedding + 契约校验。

> 这四件都是 A 可独立交付、B 写路径（Make Recallable）依赖的前置契约，先冻结再并行。

## 显式非责任（A）

- 不拥有 Memory 主事实、不拥有 ProjectionState（B 唯一拥有）、不拥有 canonical payload 物理事实、不拥有调度决策、不替 B 实现 Memory 存储/生命周期。

---

*PROGRAM A 工作包完。*
