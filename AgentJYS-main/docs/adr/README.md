# 架构决策记录（ADR）

本目录记录 P3（及 P3 与上下游之间）的**架构与产品契约决策**。它补齐了之前缺失的那一层：
「事实性文档」（契约、架构说明）之外，还必须有一层「**为什么这么定、哪些是决策、兼容性影响
是什么、哪些仍待双方冻结**」。

> 定位：ADR 回答的是**接口契约 + 集成边界**层面的问题（黑盒/白盒、存储形式、上层接口是否
> 修改、执行归属、授权模型），它与「算法正确性」正交。算法回答「P3 内部怎么做对」，ADR 回答
> 「P3 对外怎么承诺、和上下游怎么连接、改动了会不会破坏别人」。

## 编号与状态

- 文件名：`NNNN-<kebab-case-slug>.md`，编号 4 位、全局单调递增、不复用。
- 状态机：`Proposed → Accepted → Superseded → Deprecated`。
  - `Proposed`：乙方已给出完整方案，待双方评审。
  - `Accepted`：评审通过并冻结；破坏性修改只能通过 `Superseded`（新 ADR 引用旧 ADR）完成。
  - `Deprecated`：决策已废弃但保留历史。

## 模板

每个 ADR 固定使用以下字段（可裁剪 Alternatives）：

```markdown
# ADR-000N: 标题

- 状态：Proposed | Accepted | Superseded | Deprecated
- 日期：YYYY-MM-DD
- 决策层：交付形态 / 接口契约 / 存储契约 / 授权模型 / 执行归属 / 验收
- 关联：[P3_NORTHBOUND_API_V1.md](../P3_NORTHBOUND_API_V1.md) 等

## Context
背景与要解决的问题（为什么需要这个决策）。

## Decision
明确的决策内容。优先给出「推荐默认值 + 可配范围」，只在真正需要甲方拍板处留「待双方确认」。

## Alternatives Considered（可选）
考虑过但未采纳的方案及理由。

## Consequences
- 正面影响
- 负面/代价
- 待跟进项（follow-ups，指向后续 ADR 或工程项）
```

## 决策地图（索引）

| 编号 | 标题 | 决策层 | 状态 | 关键关联 |
| --- | --- | --- | --- | --- |
| [0001](0001-p3-northbound-black-box.md) | P3 交付形态：组件独立服务 + 产品应用化交付（HTTP 为过渡） | 交付/集成形态 | Proposed | `P3_NORTHBOUND_API_V1.md`、`P4_SIMULATOR.md`、`WEB_DASHBOARD.md` |
| [0002](0002-native-api-versioning-compatibility.md) | 北向 API 版本化与兼容性（含 breaking change 判定） | 接口契约 | Proposed | `P3_NORTHBOUND_API_V1.md`、`contracts/p3-northbound-v1.json` |
| [0003](0003-storage-fact-projection.md) | 存储形式：事实/投影分离与原文归属，v1 不改存储布局 | 存储契约 | Proposed | `p3_runtime_architecture.md` |
| [0004](0004-identity-scope-authorization.md) | 身份与权限：消费 scope、不自建 RBAC | 授权模型 | Proposed | `P3_NORTHBOUND_API_V1.md` §3 |
| [0005](0005-b3-action-execution-ownership.md) | B3 决策与迁移执行归属、回执状态机 | 执行归属 | Proposed | `p3_runtime_architecture.md` §Scheduling Path |
| [0006](0006-contract-testing-acceptance.md) | 消费者驱动契约测试与验收 | 验收 | Proposed | `P4_SIMULATOR.md`、`contracts/p3-northbound-v1.json` |
| [0007](0007-aether-context-namespace.md) | AetherStore 统一上下文命名空间与分层内容模型 | 上下文数据模型/检索契约 | Proposed | `0003-storage-fact-projection.md`、`p3_runtime_architecture.md` |
| [0008](0008-session-lifecycle.md) | P3 Session 记录、提交与归档生命周期 | 会话事实/一致性 | Proposed | `0007-aether-context-namespace.md` |
| [0009](0009-derived-projection-work-queue.md) | P3 派生投影工作队列与重建边界 | 派生数据/一致性/可恢复处理 | Proposed | `0003-storage-fact-projection.md`、`0007-aether-context-namespace.md` |
| [0010](0010-context-resource-persistence.md) | P3 Context Resource 与 Skill 持久化边界 | Context Catalog / 持久化 | Proposed | `0007-aether-context-namespace.md` |
| [0011](0011-context-projection-work-queue.md) | Resource/Skill/Session 统一派生 Context 投影队列 | 派生数据/一致性/可恢复处理 | Proposed | `0007-aether-context-namespace.md`、`0008-session-lifecycle.md` |

## 待补候选 ADR（决策地图的「下一步」）

以下决策点已识别但暂未成文，按优先级排列；每个都已归属到正确层面，避免再次散落在 PRD 里：

| 优先级 | 候选 ADR | 决策层 | 触发来源 |
| --- | --- | --- | --- |
| P0 | 指标定义与验收口径（2000 QPS / P99 / 5× / +10% / 72h / Hit 的 Definition·Measurement·Numerator·Denominator·Default·Range·Acceptance） | 验收 | 公司「指标没定义」 |
| P0 | 降级与错误语义（依赖不可用时 P3 的 degraded/protect 行为矩阵） | 产品行为 | 公司「故障/恢复」 |
| P1 | 部署拓扑与运维（进程/容器划分、扩缩容、升级、监控项） | 部署 | 公司「多服务器部署」 |
| P1 | 幂等与并发冲突（idempotency_key 全量落地、同对象多 action 去重/合并） | 产品行为 | 契约 §3、ADR-0005 |
| P1 | 数据删除与保留（删 Memory 是否级联删投影/原文、过期与保留策略） | 存储契约 | ADR-0003 |
| P1 | 应用化交付细化（管理控制台产品化 / 首个 SDK / 私有化 vs SaaS） | 交付形态 | ADR-0001 待跟进 |
| P1 | 层级租户 + API key scope 校验（阶段 2 立项，对齐 ADR-0004 折中） | 授权模型 | ADR-0004 待跟进 |
| P2 | 北向 gRPC 是否新增（当前 v1 只冻结 HTTP） | 接口契约 | ADR-0001/0002 |
| P2 | B3 Policy 管理授权（谁能改 policy、policy 版本化） | 授权模型 | ADR-0004 |

## 业界对标参照

当前 ADR 的决策均显式对齐业界成熟产品范式，避免自创概念、避免只复述项目现状：

| ADR | 对标产品/范式 | 关键参照 |
| --- | --- | --- |
| 0001 交付形态 | Mem0 / Zep / Letta / LangSmith | SDK + 服务 + 控制台，无人只交付裸 HTTP |
| 0002 版本化 | Stripe / OpenAI / Pinecone | 版本锁定（pin）+ 只增不改 + 有期限弃用 |
| 0003 存储 | Zep / Letta / Ceph·Azure·MinIO | fact/message 分离、memory blocks、hot/cold 自动分层 |
| 0004 权限 | LangSmith / Pinecone | org/workspace/project 层级租户、API key scope |
| 0005 调度 | Ceph RGW / Azure smart tier / MinIO lifecycle | 生命周期策略引擎与数据面分离 |
| 0006 验收 | Pact | consumer-driven contract + provider verifier + CI 门禁 |
| 0007 上下文命名空间 | OpenViking | 统一 URI、分层内容、目录式检索与检索轨迹；只参考模式，不复制 AGPL 实现 |
| 0008 会话生命周期 | OpenViking Session | 消息记录、保留窗口、归档 L0/L1、异步记忆提取边界 |

## 与文档分层的关系

本目录位于 `合同 → P3 PRD（产品行为）→ B1/B2/B3 需求 → 详细设计 → 代码 → 验收` 链条的
**「产品行为」与「接口/集成」横切层**。ADR 只记录「决策」，不重复写技术实现细节；技术细节仍
留在 `p3_runtime_architecture.md` 与各模块方案里。
