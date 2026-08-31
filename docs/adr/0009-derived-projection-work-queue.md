# ADR-0009: P3 派生投影工作队列与重建边界

- 状态：Proposed
- 日期：2026-08-30
- 决策层：派生数据 / 一致性 / 可恢复处理
- 关联：0003-storage-fact-projection.md、0007-aether-context-namespace.md

## Context

Memory 是 P3 的权威业务事实，Embedding、向量索引、L0/L1 摘要和压缩结果都是可重新生成的派生物。
如果这些工作直接嵌入同步写入或由某个 Provider 的任务格式定义，依赖异常时就无法区分“Memory 已写入”
和“派生投影尚未完成”，也无法安全重试。

## Decision

P3 建立 provider-neutral ProjectionWorkItem 和 ProjectionQueuePort。工作项固定包含
memory_id、revision、kind、scope，当前种类为 embedding、vector_index、summary。
同一 Memory revision 和 kind 以幂等键去重；失败状态不修改 Memory 事实，后续由队列重试或 reconciler
重新规划。

MemoryProjectionReconciler 只扫描权威 MemoryStore 并生成缺失工作的计划，不直接调用 B1、Milvus、
P2 或 Celery。默认按 Agent 范围检查全部 session；需要处理当前 Working Memory 时显式使用
session_only=True。Application Worker 只依赖 ProjectionExecutorPort，负责有界 claim、执行和
complete/failed 状态转换；具体 B1、向量索引和摘要执行器仍由后续生产接线提供。现有 B2/Celery
业务链路本轮不替换，作为兼容执行器继续运行。

## Consequences

- P3 可以在没有外部 Provider 时独立测试“事实存在、派生待处理”的状态。
- 后续可增加 Redis/数据库队列适配器或具体 ProjectionExecutor，而不改变 Application 或 Memory Domain。
- Redis 队列、lease 和 Worker 编排已经具备；生产仍需具体执行器、重试退避策略及 outbox/reconciler
  现场验证。
