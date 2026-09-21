# ADR-0007: AetherStore 统一上下文命名空间与分层内容模型

- 状态：Proposed
- 日期：2026-08-30
- 决策层：上下文数据模型 / 检索契约
- 关联：[0003-storage-fact-projection.md](0003-storage-fact-projection.md)、[p3_runtime_architecture.md](../p3_runtime_architecture.md)

## Context

P3 已经具备 Memory Formation、Working/Episodic/Semantic、统一 Recall Source、B1 投影和 B3 调度，
但系统身份仍以 memory id、P2 object URI、Milvus collection 等实现字段拼接。继续增加长文档、会话、
技能或企业资源时，会形成多套路径、追加式召回和无法重建的派生索引。

[OpenViking](https://github.com/volcengine/OpenViking/blob/main/README_CN.md) 的虚拟文件系统、统一 URI、
L0/L1/L2 内容分层和可追踪检索证明了这种模式适合 Agent 上下文系统。本 ADR 仅采用架构模式，
不引入或复制其 AGPL 实现。

## Decision

1. 增加内部统一身份 `aether://`。v1 默认层级为：

   ```text
   aether://tenants/{tenant}/users/{user}/agents/{agent}/memories/{episodic|semantic}/{id}
   aether://tenants/{tenant}/users/{user}/agents/{agent}/resources/{category}/{id}
   aether://tenants/{tenant}/users/{user}/agents/{agent}/sessions/{session}/memories/working/{id}
   aether://tenants/{tenant}/users/{user}/agents/{agent}/sessions/{session}/history/{archive}
   ```

   `aether://` 是逻辑身份；`p2://`、Milvus collection、Redis key 是 placement/content reference，不能
   反向成为 Context Domain 的主键。

2. 建立统一 `ContextItem`，首批类型为 `Memory / Resource / Skill / Session`。当前 Memory、长文档
   Resource 和 Session Archive 已完成兼容映射；Skill 是后续接入点，不改变 P2/P4 合同。

3. 每个 ContextItem 可持有三层内容：

   - L0 Abstract：用于目录级快速筛选；
   - L1 Overview：用于展开候选上下文；
   - L2 Detail：权威正文或权威正文引用。

   当前 Memory 映射使用明确标注的 deterministic preview 生成 L0；只有已有 overview 时才生成 L1；
   L2 保留 Memory 原文与现有 `content_ref`。

4. 建立 `ContextCatalogPort / ContextContentPort / SemanticIndexPort`。Catalog 与 Content 是可恢复事实源，
   SemanticIndex 是派生数据，通过 `ReindexService` 重建；内存实现只用于单元测试和本地开发，不作为
   生产数据库或性能结论。

5. Recall 内部主契约为 provider-neutral `ContextCandidate`，正式 facade 命名为
   `ContextRetrievalService`；原 `RecallCandidate / MemoryRetrievalService` 作为兼容 DTO 与 thin wrapper
   保留，现有 Memory 数据源无需同步迁移。ContextCandidate 明确携带 URI、类型、内容层级、来源与得分。

6. Recall 生成结构化 `RetrievalTrace`，记录数据源耗时、降级、候选得分、预算跳过与最终选入。轨迹
   携带 scope，production 使用现有 Redis 做 TTL 持久化，demo/integration 使用有容量上限的内存实现。
   Trace 写入失败不阻断前台召回；读取必须匹配 tenant/user/agent scope。本轮不改变 Northbound v1
   response，轨迹先通过 Runtime facade 提供，为后续只读诊断接口预留。

7. 当前生产 Context Catalog 采用 `CompositeContextReader` 组合独立 Reader：`MemoryStoreContextReader`
   直接从既有 Redis/SQLite MemoryStore 读取权威 Memory，`SessionContextReader` 从 SessionStore 读取
   归档，并按需映射为 ContextItem，不建立第二份 Context 主数据、不引入双写。Catalog/Content
   Port 拆分 Reader/Writer，当前 Adapter 只实现真实需要的只读能力。Runtime 的 item/children facade 在
   调用 Adapter 前后均校验可见性 scope：SESSION 节点匹配 session，AGENT 节点匹配 tenant/user/agent。
   Memory 已提供不落第二份数据的虚拟目录树：当前 session root 挂载 Working、Agent 级
   Episodic/Semantic 和 Resource 目录，因此 Catalog 与 reindex 可以从 scope 根递归遍历。
   既有 Celery 长文档产生的 `SourceType.DOCUMENT` Memory 同时投影到
   `agent -> resources -> documents -> item`；该视图的 L2 只保留 P2 E2 `content_ref`，不复制长文本正文。
   Session commit 归档映射到 `scope -> history -> archive`，每个 Archive 提供 L0/L1/L2。Skill
   通过独立 SkillStorePort 映射到 `agent -> skills -> skill`，同样进入 Catalog、权限和层次检索；
   当前默认注册表为空，不伪造生产 Skill 数据。

## Alternatives Considered

- 继续以 memory id 与 provider URI 拼接：短期改动少，但多数据源接入后无法统一授权、树形检索和迁移。
- 直接把 OpenViking 作为底座：产品能力成熟，但 AGPL 与现有 P2/P3 数据面、合同边界存在明显集成和
  商业授权风险，本阶段不采用。
- 一次性替换现有 ContextPack：会破坏 P4 验证环境和 Northbound v1，改为内部模型先行、渐进映射。

## Consequences

- 正面：Context 身份与存储供应商解耦；Memory/Resource/Skill/Session 可以进入同一目录与检索体系；
  B2 压缩可自然演进为 L0/L1 派生流水线；索引具备重建语义。
- 代价：后续需要实现 Catalog/Content adapter、URI 授权解析、reindex/reconciler 和数据迁移工具。
- 兼容性：本 ADR 不改变 Northbound v1 path、请求字段和成功状态码；现有 `content_ref` 原样保留。
- 下一步：增加 Resource 原文读取 Port、生产 Skill registry 和具备真实调用方身份校验的只读
  trace/catalog inspect API。不能只依靠调用方提交 scope 就把内部数据暴露到公网。
