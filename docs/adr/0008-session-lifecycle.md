# ADR-0008: P3 Session 记录、提交与归档生命周期

- 状态：Proposed
- 日期：2026-08-30
- 决策层：会话事实 / 一致性
- 关联：[0007-aether-context-namespace.md](0007-aether-context-namespace.md)

## Context

P3 已能接收 MemoryEvent 和构造 ContextPack，但此前没有独立 Session Fact。会话消息、使用过的 Context、
归档摘要和记忆提取状态无法形成可恢复生命周期，也无法进入统一 `aether://` 目录。

## Decision

1. Session 是 P3 Domain，不由 P2、B1、B3 或 HTTP Route 定义。
2. `SessionRecord` 保存完整 scope、revision、活动消息与有界 Archive；消息可记录使用过的 Context URI
   和 Skill 标识。
3. Store 使用 expected revision 的 CAS Port。demo/integration 使用有界内存实现，Redis 实现使用 WATCH/MULTI
   保证多进程写冲突可检测；Service 在有限次数内重试，不能静默覆盖。
4. commit 保留最近 N 条消息，其余消息形成不可变 Archive。Archive 同步生成确定性 L0 Abstract、L1
   Overview 和 L2 Transcript，并映射到 `scope/history/{archive_id}`。
5. commit 在 Archive CAS 成功后，仅向 `SessionExtractionQueuePort` 写入一个带 scope、archive_id、租约和
   状态的异步工作项，并将 `memory_extraction_status` 返回为 `pending`；不会在 CAS 或 Redis 锁内调用模型。
   `SessionExtractionWorker` 在队列外执行幂等提取，并对失败任务进行有界重试。显式、幂等的
   `/api/v1/sessions/consolidate` 仍可直接处理指定 Archive；没有配置 worker 时，旧兼容路径仍返回
   `not_implemented`，不伪造已完成。归档到 Memory 的抽取通过 P3
   `MemoryExtractionPort` 扩展；旧 `SessionMemoryExtractionPolicy` 仅作为
   兼容适配入口，默认仍使用确定性实现。
6. Session 数量、消息数和 Archive 数保持有界；生产 Redis 记录设置 TTL。更长期保留和导出由后续
   Retention ADR 决定。

## Alternatives Considered

- 继续把 session 当作请求字段：无法归档、恢复、审计和自动沉淀，不采纳。
- commit 内同步调用 LLM：延迟不可控且扩大锁窗口，不采纳。
- 直接复用 P2 对象作为 Session 主数据：会让 P2 反向定义 P3 Domain，不采纳。

## Consequences

- 正面：P3 拥有独立、可恢复、可并发控制的会话事实，并进入统一 Context Catalog；Archive 到 Memory 的
  异步边界可持久化、可租约恢复且具备幂等重试。
- 代价：默认提取仍使用确定性 Archive 摘要，但已通过
  `SessionMemoryExtractionPolicy` 形成替换边界；复杂语义提取和保留/删除策略仍需后续决策。
- 兼容性：不新增或修改 Northbound v1 路由；当前能力只通过 Runtime/Application 内部接口暴露。
