# ADR-0011: Resource/Skill/Session 统一派生 Context 投影队列

- 状态：Proposed
- 日期：2026-08-31
- 决策层：派生数据 / 一致性 / 可恢复处理
- 关联：[0007-aether-context-namespace.md](0007-aether-context-namespace.md)、[0008-session-lifecycle.md](0008-session-lifecycle.md)、[0009-derived-projection-work-queue.md](0009-derived-projection-work-queue.md)

## Context

P3 的 Resource、Skill 和 Session Archive 已经是统一 Context Catalog 中的逻辑对象，
但此前只有 Memory 有独立的派生投影工作队列。新对象可以立即出现在目录中，语义索引却
只能依赖手工 reindex，导致目录事实和派生检索之间的延迟不可见、不可恢复。

## Decision

为所有非 Memory Context 对象建立 `ContextProjectionWorkItem` 和
`ContextProjectionQueuePort`。权威写入成功后只 enqueue URI、source revision、scope 和
目标层级；worker 再通过 `ContextCatalogReaderPort` / `ContextContentReaderPort` 回读当前
权威对象，并通过 `SemanticIndexPort` 写入 L0/L1 派生索引。

- 生产使用独立 Redis namespace `aether:p3:context-projection-work`；测试和 integration
  使用等价的 InMemory adapter。
- 队列具有幂等键、租约、失败重试和有限 drain；派生失败不回滚 Resource、Skill 或 Session
  主事实，也不把失败显示为成功。
- executor 重新执行 Scope 可见性校验，并以当前 source revision 回读内容，避免旧队列项
  将过期内容写回索引。若权威 revision 已更新，工作项进入 `SUPERSEDED` 终态，不作为
  provider 失败重试。
- Session commit 仍不调用 B1 或语义模型；只在 CAS 成功后 enqueue，具体投影在 worker 中
  完成。

## Consequences

- Resource、Skill、Session Archive 与 Memory 现在有统一的派生处理边界，新增 Context kind
  可以复用同一队列和 executor 形态。
- 语义索引仍是可丢弃投影，Catalog 和各自的事实存储仍是权威来源；`ReindexService` 仍
  是历史数据和队列丢失后的恢复路径。
- 当前队列报告尚未持久化为独立运维指标，P2 仍没有物理向量删除接口；stale 清理和真实
  Redis/Milvus 压力验证继续属于后续工作。
- 目录恢复使用与 root/layers 绑定的 opaque cursor 支持分页续跑；cursor 不是新的事实源，
  也不包含 provider 凭据。
