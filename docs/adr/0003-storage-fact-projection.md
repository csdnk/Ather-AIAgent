# ADR-0003: 存储与记忆分层——Fact/Projection、原文归属与自动分层（对标 Zep / Letta）

- 状态：Proposed
- 日期：2026-08-19
- 决策层：存储契约
- 关联：[p3_runtime_architecture.md](../p3_runtime_architecture.md)、[P3_NORTHBOUND_API_V1.md](../P3_NORTHBOUND_API_V1.md) §6

## Context

公司问「是否修改之前的存储形式」，背后是「P3 的数据到底怎么组织、原文谁存、记忆怎么分层」的产品
问题。这条赛道的成熟产品已有统一范式，P3 不应自创概念，而应显式映射上去：

- **Zep**：区分 **business data（业务事实/图）vs chat message data（聊天消息）**；记忆是「抽取后的
  fact + 时间知识图谱」，不是原文堆叠。[Zep 概念](https://help.getzep.com/v3/concepts)
- **Letta（MemGPT）**：把记忆组织成 **memory blocks**——core memory（常驻设定）、archival memory
  （长期事实）、recall memory（工作记忆），SDK 直接暴露 blocks。
  [Letta Key Concepts](https://deepwiki.com/letta-ai/letta/1.3-key-concepts)
- **对象存储自动分层**：Ceph RGW tiering、Azure Blob smart tier、MinIO lifecycle——数据按访问热度在
  热/冷层间**透明自动移动**。[Ceph RGW tiering](https://metrics.ceph.com/en/news/blog/2025/rgw-tiering-enhancements-part1/)

共同结论：**「记忆分层 + 事实/消息分离 + 按热度自动分层」是行业通用范式**。P3 的
working/episodic/semantic、Fact/Projection、B3 tier 都应显式挂靠到该范式，便于与公司对齐术语。

## Decision

1. **v1 不改存储布局**：P2 Proto、Redis 状态、P2 E2 对象、P2 E1 向量、可选 Milvus 投影维持现状；
   布局变更走独立 ADR + 迁移方案，不与 v1 契约耦合。

2. **记忆分层对齐 Letta blocks（产品化表述）**：
   - `working` ≈ recall memory（会话内工作记忆，Redis 热层）
   - `episodic` ≈ 事件/经历记忆（按时间组织）
   - `semantic` ≈ archival/core memory（长期事实）
   这三种「记忆类型」是**产品对外可见的分类**，不是内部实现细节；B2 写入时确定类型，召回时按类型
   融合。

3. **Fact vs Projection 分离（对齐 Zep 的 fact/message 分离）**：
   - **Fact（权威主数据）**：`ACTIVE / ARCHIVED / SUPERSEDED / DELETED`，是记忆的「事实」，P3 主数据
     持有。
   - **Projection（派生，可重建）**：`embedding_status / compression_status /
     vector_projection_status / scheduler_signal_status`，各自为
     `PENDING / PROCESSING / SUCCEEDED / FAILED / NOT_APPLICABLE / NOT_IMPLEMENTED`。
   - **投影失败不删事实**：对应 Zep「fact 抽取自 message、message 是原始数据」——投影派生自 fact，
     fact 是权威，派生失败只标记失败，不回头删 fact。

4. **原文归属（对齐 Zep business/chat 分离）**：
   - 长文本原文（business data）→ **P2 E2 对象存储**，P3 持 `content_uri` / `content_ref` 引用。
   - 短记忆事件 `content`（chat/会话事实）→ **P3 主数据字段**。
   - 「原文最终由谁保存」的答案：**原始大对象在 P2 E2，抽取后的记忆事实在 P3，向量/压缩在投影层**。

5. **自动分层对齐对象存储 tiering**：
   B3 的 tier 迁移（L0 DRAM / L2 HDD / L3 object）对标 hot/cold 自动分层：策略引擎（B3）决定
   「该不该搬」，数据面（Executor）做「透明移动」，业务层无感知。物理迁移执行见 ADR-0005。

6. **schema evolution（向量维度演进）**：B1 换模型导致维度变化时，通过「维度进集合名」隔离
   （沿用 `p2_collection_for_scope`），新维度不写入旧集合、旧集合不污染新集合。

7. **删除/保留语义**：删除 Memory Fact ≠ 物理删除投影/原文；v1 默认**保守软删/归档**，级联物理删除
   作为待补候选 ADR（README「待补候选」P1）。

## Alternatives Considered

- **单层扁平存储（无分层）**：最简，但无法回答「记忆怎么长期化/降成本」，不符合行业范式，不采纳。
- **P3 主数据复制全量原文**：查询就近但双写失控，不采纳为默认（仅短事实 `content` 存主数据）。
- **v1 顺带重构存储布局**：扩大交付面 + 引入迁移风险，与「契约先冻结、内部后演进」冲突，推迟。

## Consequences

- **正面**：记忆分层、事实/投影、自动分层都有行业对标，不再是自创概念，和公司对齐术语的成本大幅
  降低；换 B1 模型不污染历史向量。
- **负面/代价**：需建立 `content_uri` 引用一致性治理；working/semantic 等命名需与公司统一（避免与
  Letta blocks 混用造成歧义）。
- **待跟进**：
  - 删除级联与过期/保留策略（待补候选 ADR P1）。
  - P2 对象生命周期 owner（谁回收无引用原文）。
  - 压缩后原文是否保留、保留多久（关联 B2 5× 压缩口径）。
