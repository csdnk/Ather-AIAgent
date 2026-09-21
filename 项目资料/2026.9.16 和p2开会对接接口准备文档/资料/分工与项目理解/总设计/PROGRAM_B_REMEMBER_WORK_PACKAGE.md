# PROGRAM B — Remember / Memory Formation · Work Package V1.0

> Primary DRI = B。冻结架构：V0.4.1。任务编号与主 WBS 一致。
> Outcome：记忆可靠形成、可追踪、可版本化、可被后续 Recall 使用。**Memory 没形成找 B**。

## Core Chain（B 拥有）

```
MemoryEvent → Fact Formation → Classification → Lifecycle → Memoryize
            → Make Recallable（ProjectionState Ready，B 唯一拥有）
```

## Epics

| Epic | 目标 | 关键 Task（主 WBS ID） | 冻结 DoD 摘要 |
|---|---|---|---|
| B-EPIC-01 Ingest & Fact Formation | Fact-First + 幂等 + 主事实 | B-EPIC-01-T1..T4 | 主事实可靠落点后才 Success；否则 Accepted/Partial |
| B-EPIC-02 Lifecycle | 五状态 + 分类 + 版本/冲突 | B-EPIC-02-T1..T3 | 状态机可测；uncertain→Working；冲突不静默 |
| B-EPIC-03 Durable Content | 映射 + Port + Simulator | B-EPIC-03-T1..T4 | durable/checksum/head；物理权威=Durable Store Provider |
| B-EPIC-04 Memoryize | 异步压缩 + 恢复 | B-EPIC-04-T1..T3 | Task 持久化；失败保 Original；压缩 ≥5x |
| B-EPIC-05 Make Recallable | Projection 写路径（B 唯一拥有 ProjectionState） | B-EPIC-05-T1..T6 | 只有 B 能转 Ready；版本校验；重建/Stale |
| B-EPIC-06 MemoryRead | 提供给 A 的读能力 | B-EPIC-06-T1..T3 | A 不感知 B 内部存储；语义属性完整 |
| B-EPIC-07 MemorySignal | 给 C 的信号 | B-EPIC-07-T1..T2 | Submitted ≠ 已消费；恢复后补发 |
| B-EPIC-08 Recovery/Reconciliation | 未完成任务/投影/内容写恢复 | B-EPIC-08-T1..T4 | 重启可恢复；不假设写成功/失败；旧版本不覆盖 |

## 完整 DoD（Make Recallable，B-EPIC-05）

1. Memory 写入后产生 ProjectionState=Building。
2. 调用真实/模拟 SemanticEmbeddingCapability（A 提供）。
3. 调用 VectorProjectionPort（A 提供）。
4. ProviderResult 五态（ACCEPTED/PENDING/READY/FAILED/UNKNOWN）均有处理。
5. **只有 B 可以把 ProjectionState 转 Ready**（A 禁止写 ProjectionState）。
6. timeout 可查询 provider 状态。
7. restart 后任务可恢复。
8. version mismatch 不错误转 Ready。
9. Simulator Contract Test PASS。
10. 真实 P2 未 Ready：Lane 2 PASS；Ready 后：Lane 3 PASS。

## Handoff 表

| 方向 | Capability | Provider | Consumer | B 无需知道 | 对方无需知道 |
|---|---|---|---|---|---|
| B → A | MemoryReadCapability | B | A | — | 最终排序（A 执行） |
| B → A | CanonicalLoadCapability（mapping） | B | A | — | payload 物理权威（Durable Store） |
| B → C | MemorySignalCapability | B | C | 调度决策 | signal 事实（B 拥有） |
| A → B | SemanticEmbeddingCapability | A | B | ONNX/SIMD/backend | — |
| A → B | VectorProjectionPort mechanism | A | B | — | ProjectionState（B 拥有） |

## First Batch（B 第一批产出）

1. `B-EPIC-01-T1`：Fact-First 写入序列设计（主事实先落→派生异步）。
2. `B-EPIC-02-T1`：生命周期状态机（五态）。
3. `B-EPIC-03-T1/T2`：ContentStorePort 契约 + Content Store Simulator。
4. `B-EPIC-05-T1/T2`：Projection Build 序列 + ProjectionBuildCapability 契约（与 A 对齐五元组/ProviderResult）。

> B 的前置依赖是 A 的 Embedding/VectorProjectionPort 契约（A First Batch 1/2），先冻结契约即可并行开发。

## 显式非责任（B）

- 不拥有 Embedding 推理实现（A）、不拥有向量写/检索机制（A）、不拥有 canonical payload 物理事实（Durable Store Provider）、不拥有调度决策（C）、不替 A 做 Recall 排序。

---

*PROGRAM B 工作包完。*
