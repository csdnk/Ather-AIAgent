# Recall 与 Embedding：A 侧实现与接入

日期：2026-09-23。代码基础版本：`5340a303f94cc521673d5ee68052fa4d6cd2a24c`，本轮增量位于当前工作区，尚未提交、推送。本文覆盖本轮 A 侧实现，不改变其他任务的 Mock。

## 本轮交付与真实边界

A 的新多块流程已实现并完成契约环境验收：真实 A 服务、真实 RF 身份与 SQLite 事务，B 使用严格测试替身。现有 B 基础链路另外通过真实 BGE、SQLite 和 TCP HTTP 验收。**新多块流程仍待真实 B 的资格审查、关系快照、正文读取及最终守卫接入；不能把前者称为全业务端到端已完成。**

本轮没有实现 B 的多块投影写入/发布，也没有实施 Milvus 联调、生产部署、管理台或混合检索。源代码与必要测试留在 `AgentJYS-main`，运行数据与原始证据留在项目外工作区。

## 1. Query 与 Passage 使用同一已核验模型空间

原生 BGE 仍从 `NativeP3Embedding` 调用已有 CPU 推理后端。Query 使用模型规定的查询前缀，Passage 使用配套方式；旧 `model_space` 生成规则保持不变。

`EmbeddingSpace` 现在由已加载模型产生，包含 model_space、模型 ID/权重版本、维度、tokenizer 标识、Query/Passage 前缀、归一化、距离规则和实际输入 token 上限。`EmbeddingSpaces.resolve` 供候选服务解析；同空间配置持久化到 RF SQLite 的 `embedding_spaces`，同 ID 不同配置拒绝启动。

实际 tokenizer 上限低于配置时，公布实际值。每批编码核验 operation_id、usage、model_space、维度、条目数量、连续 index、输入摘要、有限向量和单位归一化。超长输入拒绝，不截断、不在模型故障后静默换词法向量。RF 继续负责身份、幂等与期限，取消不能假定 CPU 内核已停止。

实现入口：[空间解析与输出检查](../../../../AgentJYS-main/src/aether_agent_memory/recall/embedding/spaces.py)、[原生适配器](../../../../AgentJYS-main/src/aether_agent_memory/recall/embedding/p3.py)。

## 2. 块命中先经 B 审查，再成为记忆候选

`MemoryCandidates.search` 实现 `MemoryCandidatePort.search`：编码 Query → `GenerationSearchPort.search` 分页取块 → `MemoryQualificationPort.qualify` → 记忆级 Top K。

本轮新增的 B 契约仅定义提供方责任，没有 B 服务实现：

| 对象 | 完整字段 | 主要约束 |
|---|---|---|
| CandidateQualificationTarget | memory: MemoryRef；generation: Identifier；model_space: Identifier；body_hash: Digest；chunk_index: Count；vector_id: Digest；input_hash: Digest | 精确指向版本与批次中的一个块，无正文、无排名分数 |
| CandidateQualificationResult | target: CandidateQualificationTarget；decision: allowed/excluded/unverifiable；reason_code: Identifier；manifest: ProjectionManifest或null；guard: GuardStamp或null | allowed 必须有匹配清单和凭据；其余结果不得携带这些证据 |

`qualify(ctx, targets, purpose)` 的 purpose 为 recall/extraction；输入非空、去重，结果精确覆盖全部目标。同批采用一致权威视图，核验当前权限、期限、来源、版本、正文指纹、模型空间和已发布块，不产生读取热度。缺项、重复、错配是契约错误；unverifiable 影响覆盖结论。

A 以作用域＋memory_id 占 Top K，按最高合格块分数排序；不混用版本/批次，不重复计块。触及页、块、轮数或期限上限时准确报告停止原因。同记忆证据冲突最多重验一次；仍无法确定则排除该记忆并降低覆盖。SQLite 实现只读 `generation_vectors`，游标绑定请求、主体及当前数据指纹；生产写入仍归 B。

实现入口：[候选服务](../../../../AgentJYS-main/src/aether_agent_memory/recall/basic/candidates.py)、[SQLite 多块搜索](../../../../AgentJYS-main/src/aether_agent_memory/recall/basic/generation_search.py)。

## 3. 完整正文与关系共同形成组包计划

`ContextAssembly.plan` 先取得 B 的受控快照和关系，再调用 `load_bodies` 读取完整正文，检查正文指纹、对象修订、关系修订与授权代次。向量片段不能替代全文。

实施中补齐了原设计缺少的关系修订关联：

| 对象/接口 | 完整字段或签名 | 责任 |
|---|---|---|
| MemoryRelationSnapshot | guards: tuple[GuardStamp, ...]；conflicts: tuple[ConflictGroup, ...] | B 在同一权威视图返回全部请求成员凭据及完整冲突成员 |
| MemoryContextGuardPort.relations | relations(ctx, refs) → MemoryRelationSnapshot | 无法核验明确失败，A 对照受控快照关系列表并在正文读取后比较修订 |

已有 GuardStamp 字段是 memory、object_revision、relations_revision、authorization_epoch、body_hash、checked_at。它是具体时点的核验凭据，不能永久代替当前授权。

Working-only 使用 B 受控读取与局部排名，完全绕过 Embedding、向量搜索和 RRF；混合来源按记忆级 RRF 融合，常数为 60。冲突组必须完整，缺成员或补读失败时整组跳过；其他独立可信内容仍可交付。歧义重叠组不拆成部分内容交付。

默认使用 `o200k_base` 对最终渲染文本计数，计入编号和分隔符；整条记忆、整组装入，不截断正文。重排序默认关闭，显式开启后先核验全部待发送正文的当前资格。fallback 仅用于可降级的依赖/期限错误，非法分数和契约错误不降级。

计划连同选中成员的发布清单、期望凭据、身份与签名保存到 `recall_assembly`。这是 A 的受控内部记录，不是新增公开 HTTP 输入。

## 4. RF 事务内复核并提交

| 对象/接口 | 完整字段或签名 | 责任 |
|---|---|---|
| ContextGuardRequest | expected: tuple[GuardStamp, ...]；manifests: tuple[ProjectionManifest, ...]，默认空 | 全部入包成员的期望凭据，以及入包长期候选的发布清单；拒绝重复、跨租户与同记忆多版本 |
| MemoryContextGuardPort.revalidate_context | revalidate_context(tx, ctx, request) → tuple[GuardStamp, ...] | B 在调用方事务中读取当前事实，核验权限、来源/生命周期、正文/关系修订与发布批次；不得把传入凭据当证明 |

`ContextAssembly.commit` 复核已持久化计划与 RF 原始请求的主体、operation_id、输入签名、Query、selection、预算、来源、作用域及期限。随后在同一事务中调用 B 守卫，检查 Recall 修订，持久化结果并写 packed Outbox。事务末尾再次复核，失效则全部回滚。

read 事件表示正文实际读取成功，packed 表示正式入包；同 Recall、同记忆的重复读取和重复请求不重复计数。最终提交失败不抹掉已经发生的真实读取事实。

`GenerationRecall.result` 对历史结果重新执行完整守卫。若默认旧服务重启后遇到新流程结果，会拒绝返回，要求配置新守卫提供方；不能降级到旧的简单复核。

实现入口：[组包与事务提交](../../../../AgentJYS-main/src/aether_agent_memory/recall/basic/assembly.py)、[新 Recall 服务](../../../../AgentJYS-main/src/aether_agent_memory/recall/basic/generation.py)。

## 5. 显式接入与 HTTP

默认 `ThreeFlows` 继续运行现有真实 B 链路。在启动 HTTP/Worker 前调用 `enable_generation_recall`，明确注入 memories、qualification、bodies、guards、EmbeddingSpace；可替换只读搜索适配器和有界健康探针。B 接口不完整、空间不兼容、有运行中请求或重复配置时拒绝切换。

未提供 B 集成探针时，新依赖健康为 unknown，不宣称 available。新节点沿用 RF 的日志、trace、事务与事件机制，日志只记摘要，不输出正文或凭证。未新建任务队列。

HTTP 输入输出沿用现有 RecallRequest/ContextPack，并补齐只读查询路由：

| 方法 | 路径 | 返回 |
|---|---|---|
| POST | /p3/recall | ContextPack |
| GET | /p3/recalls/{recall_id} | RecallRecord |
| GET | /p3/recalls/{recall_id}/result | 重新核验后的 ContextPack |

接口均使用现有 Bearer 可信身份与授权。查询不会重新执行一次 Recall；结果失效不会重新返回旧正文。对外契约中的通用历史 HTTP 映射与当前 `/p3` 装配有不同前缀，运行时以本表为准。

## 6. 复验与下一位实现者

Python 3.13；在 `AgentJYS-main` 中运行。将 TEMP/TMP、PYTHONPYCACHEPREFIX、TIKTOKEN_CACHE_DIR 和报告路径指向项目外工作目录。

```text
python scripts/p3/validate_collaboration.py --report <外部目录>/gate.json
python scripts/p3/validate_native_flows.py --config <真实模型配置.json> --report <外部目录>/native.json
python scripts/p3/validate_native_http.py --directory <新的外部运行目录> --config <真实模型配置.json> --report <外部目录>/http.json
```

新消费者测试位于 `tests/runtime/flows/test_generation_candidates.py`、`test_generation_assembly.py`、`test_generation_commit.py`；类型/Schema反例位于 `tests/contracts/p3/test_qualification_contracts.py`。所有新增实现和测试已纳入协作门禁。

模型配置允许指定本地 `model_path` 和外部 `cache_dir`，本次复用已存在权重，没有复制、删除或改写用户模型。真实 HTTP 验收脚本生成临时凭证，仅绑定 127.0.0.1 的临时端口，结束时关闭服务。

下一位 B 实现者需要实现 qualify、load_bodies、relations、revalidate_context 以及多块投影写入/发布，满足本文字段、同批视图和调用方事务要求。先运行消费者契约测试，再接真实 B 端到端验收。人工 B/RF 消费者确认、真实新链路联调、Milvus 和生产容量仍待完成。

验收结论见[本轮正式验收报告](../05_已有用例与验证/Recall与Embedding_A侧工程验收_20260923.md)。
