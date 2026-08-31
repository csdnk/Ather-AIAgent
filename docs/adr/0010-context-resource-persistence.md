# ADR-0010: P3 Context Resource 与 Skill 持久化边界

- 状态：Accepted
- 日期：2026-08-30
- 决策层：Context Catalog / 持久化
- 关联：[0007-aether-context-namespace.md](0007-aether-context-namespace.md)

## Context

Resource 和 Skill 已经是 P3 Context Catalog 的独立对象，但如果生产仍使用进程内字典，重启后目录会丢失，
也无法支撑多进程读取。它们不应被重新塞回 Memory 或交给 P2 定义。

## Decision

1. ResourceStorePort 与 SkillStorePort 保持 provider-neutral；Resource/Skill 模型不依赖 Redis、Milvus、P2 或 B1。
2. production 使用 Redis descriptor store。对象本体按 scope 建立索引，记录以 JSON 保存；作为 Context Catalog 的
   权威事实，默认不设置 TTL。只有显式配置 `AETHER_P3_CONTEXT_FACT_TTL_SECONDS` 时才启用过期策略，读取适配器只返回
   P3 DTO。
3. demo/integration 使用有界内存适配器，方便单元测试和本地开发。
4. Context Reader 负责把 descriptor 映射成统一 `ContextItem` 与 L0/L1/L2；Redis 不是 Context Domain 的身份来源。
   当 L2 只有 `content_ref` 时，Reader 通过 provider-neutral `ObjectStorePort.read_text` 按需读取，不把外部
   对象内容同步复制进 descriptor store。文本化由 `ResourceContentParserPort` 负责；未配置对应二进制解析器时，
   Resource 仍可被目录发现，但 L2 保持 `pending`。
5. Resource/Skill 删除先移除 P3 权威 descriptor，再通过 `SemanticIndexPort` 使派生索引失效。当前 P2 无物理向量删除
   契约，因此生产适配器写入 P3 tombstone；检索结果仍须回查 Catalog，不能把派生索引当作事实源。
6. Store 为每个 scope/object identity 持久维护单调 revision。删除 descriptor 不删除 revision counter，因此同 ID 重建
   不会复用旧投影队列的去重代际；适配器拒绝较旧 revision 覆盖较新事实。
7. Resource category 属于 canonical URI。category 变化时，新 descriptor 先持久化，再失效旧 URI 的派生索引并为新 URI
   生成投影任务；旧队列工作通过 Catalog/revision 复查进入 `SUPERSEDED`。
8. 外部文件本体仍归对象存储所有，删除 descriptor 不隐式删除 P2 对象。版本保留和物理向量回收由后续 Lifecycle ADR
   决定，本 ADR 不把文件解析或向量索引塞进 descriptor store。

## Consequences

- 生产 Context Catalog 重启后可恢复，Resource/Skill 可被多进程读取。
- Redis 连接、持久化策略和可选 TTL 需要纳入服务器健康与容量验证。
- 显式删除可立即阻止 Catalog/检索返回对象；P2 物理向量空间仍需要后续离线回收能力。
- 当前默认组合根不预置 Skill；真实技能注册仍需上游接入或后续管理 API。
