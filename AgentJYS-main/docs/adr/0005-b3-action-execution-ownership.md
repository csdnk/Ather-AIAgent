# ADR-0005: B3 调度与迁移执行归属——策略引擎与数据面分离（对标自动分层）

- 状态：Proposed
- 日期：2026-08-19
- 决策层：执行归属
- 关联：[p3_runtime_architecture.md](../p3_runtime_architecture.md) §Scheduling Path、[P3-B3语义智能分层调度器技术方案_v0.2.md](../P3-B3语义智能分层调度器技术方案_v0.2.md)

## Context

公司问：Action 提交后怎么知道迁移成功？物理迁移谁做？同一对象并发两个 Action 怎么办？Prediction 挂了
怎么办？

「按热度在存储层间自动搬数据」是成熟基础设施范式，P3 的 B3 应显式对标，而不是自创一套调度语义：

- **Ceph RGW tiering**：[按 S3 storage class 配置 tier](https://metrics.ceph.com/en/news/blog/2025/rgw-tiering-enhancements-part1/)
- **Azure Blob smart tier / MinIO lifecycle**：按访问热度在 hot/cool/archive 层间**自动**移动。
  [Azure smart tier](https://www.cloudthat.com/resources/blog/automating-cost-optimization-in-azure-blob-storage-with-smart-tier)
- **通用生命周期策略**：[HashiCorp 数据生命周期管理](https://developer.hashicorp.com/well-architected-framework/optimize-systems/lifecycle-management/data-management)

共同结构：**「生命周期策略引擎」决定何时搬，「数据面」做透明移动，业务层无感知**。这个三层结构与
P3 的 B3 决策/跟踪/执行天然对应——B3 是策略引擎，Executor 是数据面，P3 编排层在中间跟踪回执。

## Decision

1. **决策 / 跟踪 / 执行三层分离（用分层引擎的行业语言表述）：**
   - **策略引擎（B3）**：基于 AccessTrace + MemorySignal + ResourceState 生成 TierAction（决定「该
     不该搬、往哪搬」）。
   - **编排跟踪（P3）**：持有 Action 状态、记录回执、维护路由版本与历史（知道「搬没搬成」）。
   - **数据面（外部 Executor）**：真实数据搬运（Redis/Milvus/P2 之间的字节移动）。P3 **不实现存储
     引擎内部的物理迁移状态机**。

2. **TierAction 状态机（产品行为，冻结）：**
   ```
   Generated → Submitted → Succeeded
                         ↘ Failed
                         ↘ Unknown
   ```
   - 对每个 Action，P3 必须给出确定状态；状态不完整显式标记 `Unknown`，**不得假装成功**。
   - `Prediction 不可用` → 回退 `Heuristic` → 回退保守 `Keep`（降级链），保证行为确定。

3. **回执契约（P3 ↔ Executor 边界）：**
   Executor 必须回写 `execute_status / execute_latency_ms / new_tier / failure_reason / trace_id`。
   现有 `P2MigrationExecutor` 的「确定性逻辑块路由 + 完成/失败回写 + 路由版本更新 + 幂等恢复」作为
   v1 实现基线。

4. **推荐默认（v1 阶段）：**
   - 采用「确定性逻辑块路由 + 外部物理迁移」；物理字节搬运由外部迁移组件负责（与 README 现状一致）。
   - Executor Owner 作为「待双方确认」点，但**契约先行冻结**：不管 Executor 由谁实现，P3 侧的 Action
     状态机与回执契约不变。

5. **幂等与并发冲突：**
   - 同一对象并发产生多个 Action 时，P3 以对象最新决策为准，记录冲突事实（去重/合并规则作为待补
     候选 ADR，README「待补候选」P1）。
   - Action 提交支持幂等（`idempotency_key` 语义延续契约 §3）。

## Alternatives Considered

- **P3 直接实现物理迁移状态机**：闭环完整，但把存储引擎内部迁移细节耦合进 P3，扩大交付面，与
  「策略引擎/数据面分离」的行业范式冲突，不采纳。
- **B3 只出决策、不跟踪回执（fire-and-forget）**：最简，但无法回答「迁移到底成功没有」，违背公司
  「行为确定」诉求，不采纳。
- **Prediction 不可用即报错**：把降级责任抛回调用方，行为不确定，不采纳；改用
  Prediction→Heuristic→Keep 降级链。

## Consequences

- **正面**：B3 有了明确的行业对标（生命周期策略引擎），和公司讲「调度」时用通用术语；决策/跟踪/
  执行解耦，各自可替换；Action 状态可审计、行为可验收。
- **负面/代价**：需冻结并维护 P3 ↔ Executor 回执契约；物理迁移 Owner 未定是当前最大外部依赖。
- **待跟进**：
  - Executor Owner 确认（待双方确认）。
  - Action 去重/合并规则、路由版本回滚。
  - 真实 Milvus → Redis prefetch executor 落地（对应 `p3_runtime_architecture.md` TODO）。
