# Recall / Embedding A 侧接入

更新：2026-10-08。本文记录当前接线；2026-09-23 的原增量验收范围保留在末节。

## 当前职责与装配

Recall 负责查询编码、候选搜索、候选资格消费、排序和 ContextPack 组装。Remember 负责记忆正文、来源、状态、关系和最终资格。Runtime 提供存储适配、可信上下文与事务；Operate 维护热副本和缓存地址。

主要入口为 `recall/basic/candidates.py`、`generation_search.py`、`assembly.py`、`generation.py`。Embedding 继续使用 `recall/embedding/p3.py`，空间定义及核验在 `embedding/spaces.py`。这次读取改造没有更换 BGE、检索算法或相似度策略。

当前 `RememberFactory.attach` 创建实际 `RememberBoundary`，调用 `ThreeFlows.enable_generation_recall` 接入 generation Recall。Host 要求 MemoryReadPort、MemoryQualificationPort、RecallBodyReadPort、MemoryContextGuardPort 与兼容 EmbeddingSpace；提供方缺少 `load_recall_batch` 或 `load_bodies` 会在装配时被拒绝，不退回旧同步加载。

## 正文读取的三个接入点

| Recall 阶段 | 接口 | Remember 实现 |
| --- | --- | --- |
| 已有候选，需要正文快照 | `RecallBodyReadPort.load_recall_batch(ctx, refs)` | Boundary → Pipeline → BodyReads.load |
| 补齐冲突组其他成员 | 同上 | 与候选相同的地址和资格规则 |
| 最终读取正文并核对组包凭据 | `MemoryFoundationPort.load_bodies(ctx, refs)` | Boundary → Pipeline.read_body → BodyReads.read |

三处均按本条 MemoryRecord 的 cache_location 决定是否尝试热层；没有地址或热层不可用时，再按 body_location 读取权威正文。候选与冲突补读不再调用旧 `memories.load`，避免在最终读取之前就通过 hydration 访问冷层。

读取前后保留完整权限、当前版本、来源和关系检查；正文哈希必须匹配。失败不能返回成功空正文。普通读取不准入、不续期、不修复或清空地址。返回的 path/location 对应实际使用的存储位置，成功读取继续使用原 recall.access 标识及去重。

精确地址规则、期限、旧内联记录和自定义提供方适配见[冷热地址读取契约](17_Recall冷热地址读取契约.md)，时序见[三流程与异常时序](../architecture/03_三流程与异常时序.md)。

## 接口与验证边界

`POST /p3/recall`、`GET /p3/recalls/{recall_id}` 及结果查询的外部格式保持原样。MemoryReadBatch、FullBodyReadResult 和 ContextPack 字段没有因本次读取改造改变。Python 仍是字段和签名来源；接口目录的 implemented=false 是历史 contract_only 标记，不能用来推断实际服务未实现。

离线地址回归、契约、类型检查及独立 Azure 测试入口统一见[CI 检查与复验](11_CI检查与复验.md)。当前读取改造的本地验证不替代真实 Redis/Ceph/PostgreSQL/Milvus/Temporal 联调，也不代表线上镜像已升级。

## 2026-09-23 历史验收范围

当时新 A 候选及组包验收使用严格 B 测试替身，既有基础 B 链路另有真实 BGE、SQLite 和 TCP HTTP 证据；当时“新多块 B 尚待接入”的结论仅适用于该批次。旧 validate_collaboration、validate_native_flows、validate_native_http 脚本已退役，不再作为当前复验命令。

当时完整说明与验收分别在仓库交付包 `交付成果/开发协作/13_Recall与Embedding_A侧实现与接入_20260923.md` 和 `交付成果/测试与验收/Recall与Embedding_A侧工程验收_20260923.md`。保留原证据范围，不将历史通过数量当作当前版本结果。
