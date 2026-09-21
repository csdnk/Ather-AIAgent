# A 组文档字段与现有代码逐项核对 V0.1

核对日期：2026-09-14。代码范围：`AgentJYS-main/src/aether_agent_memory`，并对照 `engine/proto/aether_engine.proto`。依据为现有《召回数据定义》和《召回与 P2 接口对接需求》，保持文档流程、字段语义与 A/B/P2 分工。

**结论：只能部分匹配，当前代码不能原样承接文档的完整流程对象。** 向量、Query/Passage 用途、候选 ID/分数、正文引用/字节等已有基础；模型及输入绑定、准确版本、资格与正文校验证据、投影目标和机制结果等，需要补充强类型对象及字段。还有部分同名或相似字段含义不同，不能简单改名。

本文给出“现有哪个类、已有哪个字段、缺少什么、应如何改”。所有新增类、字段和路径都是后续代码修改建议，本次未修改业务代码。

## 1. 怎样读核对结果

| 结论 | 含义 | 后续动作 |
|---|---|---|
| 可复用值 | 当前已有承载该数据的字段 | 校验后写入文档对应对象；不代表整个对象已实现 |
| 需转换 | 数据存在，但类型、命名或语义边界不同 | 明确转换规则，不能直接赋值 |
| 现有类缺字段 | 继续使用现有类时会丢失文档要求的信息 | 明确给该类加属性，并同步构造、序列化与调用处 |
| 需新增类 | 文档新增了独立的记录、身份或证据关系 | 新建对应模型，逐字段定义；不把全部信息塞进旧 metadata |
| 依赖外部事实 | A 能定义字段，但目前不能证明其值 | 由 B/P2 的真实响应或已确认契约提供，缺失时按文档处理 |

完整逐字段结果见[《文档字段与代码类核对明细》](A组_文档字段与代码类核对明细_V0.1.md)。明细保留文档类型、必填要求、源码定位、具体目标属性和原定义。公共头与嵌套类型单独列出。

## 2. 现有类应增加或修改的字段

这里列出的扩展，是沿用现有入口和底层客户端时需要做的修改；文档中的完整业务记录仍由第 3 节的新模型承接。

| 现有文件 / 类 | 现有属性与不足 | 具体建议修改 | 与文档的对应关系 |
|---|---|---|---|
| [core/scope.py：Scope](../../AgentJYS-main/src/aether_agent_memory/core/scope.py) | 有 tenant_id/user_id/agent_id/session_id/task_id，没有 project_id | 增加 `project_id: str \| None = None`；`as_dict()` 同步输出。保留 user_id 的原含义 | 给文档 Scope.project_id 提供传递位置；新契约要求字段出现，null 不代表任意项目 |
| [runtime/request_context.py：RequestContext](../../AgentJYS-main/src/aether_agent_memory/runtime/request_context.py) | 有 request_id/trace_id/deadline 等，没有 project_id | 增加 `project_id: str \| None = None`；同步 `scope`、`from_values()`、`from_mapping()` 的参数及赋值 | 不在上下文转换时丢失项目范围；request_id→request_ref、deadline→deadline_at 另由 P2 适配层转换 |
| [context/models.py：ContextRequest](../../AgentJYS-main/src/aether_agent_memory/context/models.py) | 请求平铺范围字段，同样没有 project_id；仅 max_tokens/deadline_ms/filters | 若继续使用该入口，增加 `project_id: str \| None = None`，传入 RequestContext；完整授权、tokenizer 与版本绑定进入新增 RecallRequest | `max_tokens` 可转换 token_budget；不能将任意 filters 视为已验证的 RetrievalConstraints |
| [b1/sidecar.py：InterceptItem](../../AgentJYS-main/src/aether_agent_memory/b1/sidecar.py) | 已有 `input_type: Literal['passage','query']`；默认 passage；模型绑定等只能靠外围信息 | 新增强类型 `model_binding`（见第 3 节）；新流程必须显式传 input_type。若保留旧 API，model_binding 可在兼容入口允许缺失，但进入新契约必须齐全 | usage 明确映射到 input_type；不能宣称现有代码完全不区分 Query/Passage。`extra='forbid'` 意味着扩展请求前须先更新模型 |
| [b1/models.py：EmbeddingRequest](../../AgentJYS-main/src/aether_agent_memory/b1/models.py) | 有 text/source/context，没有明确 usage 或模型绑定 | 若新流程仍走 EmbeddingPipeline，增加 `usage` 和 `model_binding`，调用处显式传递；若直接走 Sidecar，仅新增标准 SemanticEmbeddingRequest 并适配，旧 EmbeddingRequest 可保持兼容 | 两个现有推理入口择实际路径改造，不要求无差别修改两套入口；不能只加属性却继续固定调用 passage 推理 |
| [b1/models.py：EmbeddingRecord](../../AgentJYS-main/src/aether_agent_memory/b1/models.py) | 有 vector/embedding_model/chunk_id，缺少用途、完整模型绑定与输入绑定 | 若作为新流程的底层输出，增加 `usage`、`model_binding`、`source_hash`、`input_binding_digest`、`vector_hash`；保留 vector 和旧 embedding_model。记录进入 SemanticEmbeddingResult 时另生成受控引用与验证证据 | 这五项分别承接用途、实际模型、原始输入、输入上下文和向量摘要；不能全部从 model 字符串推导。Sidecar 字典输出也需等价适配 |
| [p2/client.py：P2ObjectMeta](../../AgentJYS-main/src/aether_agent_memory/p2/client.py) | 仅 bucket/key/etag/size；Proto 已有校验和字段 | 增加 `md5_hex: str \| None = None`、`blake3_hex: str \| None = None`；保留 Proto optional presence，更新 Put/Get 结果转换 | 映射 P2ContentMetadata.checksum；etag 保持不透明标签，不默认当摘要 |
| [p2/client.py：P2GrpcClient](../../AgentJYS-main/src/aether_agent_memory/p2/client.py) 的读取返回对象 | get_object 只返回 bytes/None，丢弃 ObjectBytes.meta | 新增保留 `data` 与 `meta` 的读取结果对象/方法，再适配成 P2ContentReadResult；现有 get_object 可继续为旧调用返回 bytes | 单独给 P2ObjectMeta 加字段还不够，必须让读取返回链保留它 |
| [memory/projection.py：ProjectionWorkItem](../../AgentJYS-main/src/aether_agent_memory/memory/projection.py) | 仅 work_id/memory_id/revision/kind/status/attempts/租约，不能定位文档机制操作 | 若沿用该队列，增加 `operation_ref: str \| None = None`，关联新增 VectorProjectionOperation.operation_id；新投影任务要求其非空 | operation_ref 是建议的代码连接字段，不是新增对外协议字段；原 work_id 不替代 operation_id，原 attempts 不替代分开的变更/查询次数 |

**可选字段只用于兼容旧入口。** 新契约中，文档要求“必须出现但可空”的字段用 `field: T | None` 且不设置默认值；必填非空字段必须在进入新流程前补齐。不能给关键证据默认 None 后仍按完整请求放行。

扩展 project_id 后，构造和过滤 Scope 的适配位置也要同步传递；新增字段本身不会自动建立权限隔离。A 的新严格 Scope 使用文档的 tenant/project/agent/session/task 五维，旧 user_id 不进入该五维的规范摘要。

## 3. 按流程核对：哪些对象需要新增，哪些字段不能直接映射

### 3.1 N01：向量生成

| 文档对象 / 字段 | 当前能对应什么 | 不匹配部分及具体代码动作 |
|---|---|---|
| SemanticEmbeddingRequest | EmbeddingRequest.text、InterceptItem.text/chunk_text、input_type、请求关联 | 新增 `b1/semantic/models.py::SemanticEmbeddingRequest`，定义 embedding_request_id、caller_ref、caller_request_ref、trace_id、authorization_ref、usage、input_ref、source_hash、input_binding_digest、input_binding_ref、model_binding、deadline_at、execution_policy_ref、reuse_digest；继承 RecordHeader |
| EmbeddingModelBinding | VectorQueryResult.model/dimension；Sidecar 的 embedding_model、embedding_dim、model_hash、quantization_type、schema_version | 新增同模块 `EmbeddingModelBinding`：model_id、model_version、dimension、dtype、embedding_schema_version、preprocessing_version、retrieval_space_ref、model_contract_ref。model_hash 要经目录映射为版本；推理量化精度不直接等于输出 dtype；响应 schema_version 不直接等于所有契约版本 |
| SemanticEmbeddingResult | EmbeddingRecord.vector、embedding_model；Sidecar 实际运行信息 | 新增 `SemanticEmbeddingResult`：embedding_result_id、request_ref、usage、model_binding、source_hash、input_binding_digest、vector_ref、vector_hash、validation_evidence_ref、validated_at。真实 vector 校验后保存为 vector_ref；不得将向量数组直接改名为引用 |
| QueryEmbeddingResult | VectorQueryResult.vector/dimension/model | 新增 `memory/recall_contracts.py::QueryEmbeddingResult`；补 embedding_result_ref、usage、model_id、model_version、dtype、embedding_schema_version、source_hash、retrieval_space_ref、external_contract_ref、source_evidence_ref、validated_at，并承接 vector_ref/dimension；它是已验证的 Recall 本地结果，不与共享 SemanticEmbeddingResult 合并 |
| SemanticEmbeddingExecution | 动态批处理已有 PendingEmbeddingJob、EmbeddingJobResult，主要表达在途任务与耗时 | 新增持久化契约对象 SemanticEmbeddingExecution，保存请求/调用绑定、state、attempts_reserved、deadline_at、execution_policy_ref、result_ref、error_code、lease_owner/lease_until/lease_token、state_version；不能用内存 Future 或队列成功代替 |

现有 Sidecar 已具备用途区分和部分运行元数据，主要缺口是**形成并贯通文档要求的固定绑定对象**。原 EmbeddingPipeline.process 会切分并写 sink；新流程还需确保 B 的固定 Passage 不被隐式重新切分、B 批准前不因旧流程自动写入。这里是字段来源和交接边界的配套修改，不要求重新实现底层推理。

### 3.2 N02：候选获取

| 文档字段 | 当前对应 | 结论与具体修改 |
|---|---|---|
| P2SearchInput.query_vector/top_k | P2GrpcClient.search_vectors(query, top_k) | 数值载荷和参数已有；新增 `p2/contracts.py::P2SearchInput` 接收已验证向量；向量 Port 不能继续只传 query 文本且在 P2 适配器内隐式生成 |
| P2SearchInput.usage/model_binding/retrieval_space_ref/scope/memory_types/occurred_after/occurred_before | collection + 本地 metadata 过滤；用途在上游 Sidecar 可提供 | 新增这些明确属性；空间映射到 collection，模型与筛选范围从固定契约取得。P2 当前 SearchVectorRequest 仅 collection/query/top_k，过滤与绑定保证需要双方对齐 |
| P2SearchHit.projection_ref/raw_score | P2VectorHit.id/score | 新增 P2SearchHit 并显式转换；id 须是 B 可解析引用；raw_score 必填可空，不采用旧 MemorySearchHit.score 默认 0 |
| P2SearchHit.provider_rank/model_version/projection_schema_version/identity/source_evidence | P2VectorHit.metadata 为自由字典，未定义这些完整属性 | 在 P2SearchHit 新增这些属性；排名只能按签收顺序解释，版本/身份/证据不能本地猜测 |
| P2SearchResult.requested_k/completion/partial_reason/ranking_contract_ref/query_binding_evidence/completion_evidence | search_vectors 返回列表；RecallSourceResult 有 complete、missing_sources、degraded_reasons | 新增 P2SearchResult 的明确字段及 hits/retrieval_space_ref。旧 complete 默认 true、空列表、命中数量都不能代替完成证据 |
| VectorCandidateSet、VectorCandidate、SourceReadResult | RecallSourceResult/MemorySearchResult 有候选列表和部分降级信息 | 在 `memory/recall_contracts.py` 新增三个流程记录/值对象，补执行关联、来源范围、完成证据、发现候选 ID；详细属性逐项见明细 |

### 3.3 N03/N04：资格、内容映射与正文加载

| 文档对象 / 字段 | 当前对应 | 结论与具体修改 |
|---|---|---|
| CandidateValidationResult | RecallCandidate 已有 memory_id/memory_type/content_ref；MemorySearchHit 有 source_revision | 新增 CandidateValidationResult：validation_ref、candidate_id、source_result_ref、source_id、owner_contract_ref、owner_evidence_ref、authorization_evidence_ref、projection_evidence_ref、memory_id、memory_version、memory_type、decision、reason_codes、checked_at。ID/type 可作查找线索，正式值由 B 确认 |
| P2ContentTarget | ObjectReference.provider/namespace/object_key/content_ref | 新增 P2ContentTarget：content_ref、content_version、representation_id、representation_type、bucket、key。namespace/key 可按已批准映射转换，其他版本/表示字段由 B 给出 |
| P2ContentReadInput | ObjectStorePort.read_bytes(content_ref)，没有准确版本和范围对象 | 新增 P2ContentReadInput：content、approved_range、expected_hash、version_condition_ref、max_response_bytes；expected_hash 可在 A 本地保存校验，不强制作为 P2 报文字段 |
| ByteRange.start/end | TextChunk.start_char/end_char；Proto GetObjectRangeRequest.start/end | 新增公共 ByteRange，使用字节半开区间；字符偏移须经编码映射，Proto 的含上界 end 要转换为文档 end−1，不能直接复制 |
| P2ContentMetadata | P2ObjectMeta.bucket/key/etag/size；Proto 另有 md5_hex/blake3_hex | 新增 content_ref、content_version、representation_id、object_size_bytes、content_encoding、etag、checksum、version_evidence。size→object_size_bytes 可复用；版本不能从 ETag 或 Memory.revision 无条件推导 |
| P2ContentReadResult | 当前仅 bytes/None | 新增 status、content、data、returned_range、returned_bytes、meta、read_path、placement、cache_trace、evidence。bytes→data 有基础；None 不能无条件区分不存在、错误、未核验版本 |
| ContentLoadResult | 旧 RecallCandidate.content/text 仅保存内容，未形成独立校验链记录 | 新增 ContentLoadResult，承接 validation_ref、expected_identity、expected_hash、payload_ref、content_version、actual_hash、loaded_bytes、validated、failure_reason，以及读取次数/额度关联和来源观察字段；完整字段见明细。B 映射证据与 P2 读取事实共同支撑结果 |

**这些证明字段不是要求 P2 替 B 判断资格。** B 负责批准身份、版本、范围及授权；P2 提供实际对象和读取事实；A 保存二者校验后的结果。

### 3.4 N05：排序、最终复核、上下文交付

| 文档对象 / 字段 | 当前对应 | 结论与具体修改 |
|---|---|---|
| RecallCandidate（已验证快照） | `memory/retrieval/models.py::RecallCandidate` 自述为兼容 DTO，有 source/score/content，校验引用缺失 | 在 `memory/recall_contracts.py` 新增严格 RecallCandidate，补 candidate_id、validation_ref、owner_evidence_ref、owner_contract_ref、content_result_ref、logical_sources、source_ranks、source_refs、identity_key、memory_type、conflict_check、conflict_group_ref、evidence_refs、validated_at；避免把旧候选默认当成已验证快照 |
| RankedRecallCandidates / RankedEntry | 原候选只有 score/semantic_score/temporal_score；ContextPack.recall_scores 是字典 | 新增排序对象及 ranked_ref/entries/policy_version/excluded_refs；RankedEntry 明确 candidate_id/base_score/final_rank/semantic_basis_ref/tie_break_key。旧 score 不是文档的 rank 融合分 |
| ContextPack.rendered_context/token_budget/used_tokens | 旧 ContextPack.assembled_text/budget_tokens/total_tokens | 名称可转换，但必须绑定模板和 tokenizer 后重新核验。已检查 MockContextPackBuilder 用字符估算，不能直接将 total_tokens 改名当精确 used_tokens |
| ContextPack.items/groups | 旧 memories:list[Memory]，另有 memory_refs/evidence_refs | 新契约 ContextPack 新增 items/groups；新增输出 ContextItem 与 ContextGroup。每条输出保存 memory_version、content_version、representation_id、passage_range、text_hash、provenance 等。`context_store.ContextItem` 是目录存储对象，不是这个输出片段 |
| ContextPack 状态与证据 | 旧 status/complete/missing_sources/degradation_reasons/built_at | 新增 pack_id/pack_version、scope_ref、retrieval_mode、policy_version、source_coverage、execution_state、result_class、recall_availability、tokenizer_id/version、template_version、truncated/truncation_reasons、excluded_summary、fatal_reason、validated_at、finalized_at、trace_ref。missing_sources 规范来源标签；degradation_reasons 从 dict 转为脱敏原因列表 |
| 最终复核 | 旧候选无文档完整的最终复核批次与条目证据 | 新增 FinalValidationBatch/FinalValidationItem 与 ContextProvenance.final_validation_evidence_ref/validated_at。初次 CandidateValidationResult 不能替代发出前复核 |

### 3.5 N06/N07/N08：投影写入、查询核验和删除

| 文档对象 / 字段 | 当前对应 | 结论与具体修改 |
|---|---|---|
| ProjectionIdentity 五元组 | Memory.id/revision、EmbeddingRecord.chunk_id；当前 P2VectorIndexAdapter 还会把 memory.id 填到 chunk_id | 新增共享 ProjectionIdentity：memory_id、chunk_id、memory_version、model_version、projection_schema_version。前两项须来自 B 的固定片段；revision 只有经版本契约映射后才可用 |
| P2ProjectionTarget | collection + VectorRecord.id | 新增 tenant_id、provider_ref、retrieval_space_ref、identity、physical_target_ref。原物理 ID 必须能稳定解释为准确五元组目标 |
| ProjectionPayload / P2ProjectionPayload | EmbeddingRecord.vector + 自由 metadata | A 内部新增 embedding_result_ref、representation_id、content_ref、content_version、approved_range、content_evidence_ref、metadata、metadata_hash；外发对象另承接 usage/vector/model_binding/source_hash/input_binding_digest/vector_hash 等真实载荷。受控 vector_ref 解析后才能外发 vector |
| ProjectionMetadata / P2ProjectionMetadata | Memory.type 与旧 metadata 的 scope/用户/正文/关键词等 | 新增严格 scope、memory_type、occurred_at；A 内部另存 owner_metadata_evidence_ref。Memory.created_at 不能代填业务 occurred_at。metadata_hash 仅覆盖三项外发语义属性 |
| P2UpsertInput / P2DeleteInput | 当前 upsert 接收 records；Proto DeleteVectors 接收 collection/ids | 新增 target、provider_idempotency_key、request_fingerprint、precondition_ref；upsert 加 payload，delete 加 related_upsert_keys。原插入/删除计数不是完整机制结果 |
| P2OperationQueryInput / P2TargetQueryInput | Python 客户端缺对应完整能力 | 新增明确查询对象：前者关联原动作、原幂等键和 provider_operation_ref；后者关联准确 target、预期 request_fingerprint 与是否需要载荷。不能通过 ANN 搜索未命中代替准确对象查询 |
| P2TargetState / P2MutationResult | 没有完整目标状态；upsert_vectors 直接返回 None | 新增 object_present、stored_request_fingerprint、stored_payload、durable、index_queryable、delete_confirmed、late_write_barrier_confirmed 及证据；结果还需原操作关联和状态。未知用 null，P2 未提供的事实不能由请求回显生成 |
| VectorProjectionRequest / ProjectionTargetBinding / VectorProjectionOperation | ProjectionWorkItem 仅是队列工作；MemoryProjection 为概括状态 | 在 `memory/vector_projection/models.py` 新增独立类，保存请求、目标五元组/摘要、退役标记、稳定操作键/指纹、远端操作引用、分开的调用次数与恢复状态；通过 operation_ref 关联旧队列 |
| ProviderResult | MemoryProjection.vector_projection_status、memory.models.ProjectionStatus、ProjectionWorkStatus 都有旧状态 | 新增 ProviderResult：state 五态及 operation_id/observation_version/operation_kind/identity/provider_ref/retrieval_space_ref，准确对象/索引/删除事实、binding_evidence_ref/completion_evidence_ref、重试建议与证据等。**队列 succeeded 不得直接映射机制 READY；A 的 READY 仍由 B 执行领域 Ready Guard** |

## 4. 新增模型的具体放置位置

以下路径尚未创建；只确定承接位置，不要求一个对象对应一个服务或 RPC。

| 建议新增文件 | 应定义的类 | 对应现有代码的处理 |
|---|---|---|
| `src/aether_agent_memory/runtime/contract_types.py` | 严格 Scope、Hash、ByteRange、ProjectionIdentity、RecordHeader/OnlineHeader/MutableOnlineHeader 及公共值类型 | 与 core.Scope 明确导入/转换；公开字段和摘要排除旧 user_id，不全局改变旧存储语义 |
| `src/aether_agent_memory/b1/semantic/models.py` | SemanticEmbeddingRequest、EmbeddingModelBinding、SemanticEmbeddingResult、SemanticEmbeddingExecution | 适配 InterceptItem/EmbeddingRequest 和现有推理结果，不能将模型配置当作实际推理验证证据 |
| `src/aether_agent_memory/memory/recall_contracts.py` | RecallRequest、执行/检查点、候选/资格/正文/排序/预算/交付及事实记录 | 同名 RecallCandidate/ContextPack 与旧模块隔离；新流程使用严格类型，旧入口通过明确转换迁移 |
| `src/aether_agent_memory/memory/vector_projection/models.py` | VectorProjectionRequest、ProjectionPayload/Metadata、ProjectionTargetBinding、VectorProjectionOperation、ProviderResult | 不直接替换旧队列状态和 B 领域状态；保留独立关联 |
| `src/aether_agent_memory/p2/contracts.py` | P2CallContext/ResponseContext、P2Search*、P2Content*、P2Projection*、P2UpsertInput/DeleteInput、查询/结果/证据/错误类型 | 均为逻辑 DTO；适配现有 client/Proto，不宣称 P2 已接受同名报文字段 |

`runtime/ports.py` 的新能力签名还需承接上述请求/结果类型。例如，读正文不能只有 content_ref→bytes，投影写入不能只有 Memory→None。只改变模型定义、不同步传参和返回值，会继续丢失新增字段。

## 5. 不能靠 A 本地加字段解决的项目

| 字段 / 语义 | 必须取得的来源 | 当前应如何记录 |
|---|---|---|
| memory_version、content_version、representation_id、approved_range、owner/authorization evidence | B 对准确候选/片段的批准与版本映射 | 没有事实时不得用 Memory.revision、content_ref 或默认值拼成通过校验结果 |
| model_version、preprocessing_version、retrieval_space_ref、model_contract_ref | 固定模型/空间目录及实际运行核验 | 现有 model_hash、维度、精度可作依据，但须有明确映射和兼容规则 |
| completion、ranking_contract_ref、query_binding_evidence | P2 搜索语义、响应及已确认契约 | 空 hits 不证明正常完成；本地补字段不等于已支持按范围完成的 TopK |
| durable、index_queryable、binding/completion evidence | P2 准确对象与索引能力 | inserted 计数或成功 Ack 不够；无充分依据不能形成写入 READY |
| delete_confirmed、late_write_barrier_confirmed | P2 删除完成与旧在途写屏障事实 | deleted 计数或本地 retired 标记不能单独证明远端不会复活 |

RP2-03 元数据查询按需使用；RP2-04 热副本和 RP2-05 读取来源观察的字段也列入明细，按现有范围作为补充，不因此加入基础主链路。

## 6. 核对范围与验证

本次扫描源码类与声明属性，对照模型定义、对象构造、P2 客户端返回值和 Proto，区分“有数据基础”与“已经满足文档契约”。逐字段明细中引用的现有类/属性用 AST 校验其位置，文档字段从原字段表提取，避免手工另造字段名。未运行实际 B/P2 环境，因此接口可用性与事实保证仍需联调核实。

明细覆盖《召回数据定义》的流程记录、公共头与嵌套对象，以及 P2 逻辑请求/响应/公共结构，共 90 个对象/结构、800 条字段记录（包含不同对象中的同名字段，公共头不重复展开）。其中 81 条显式核对了可复用数据、转换或不等价关系；其余字段按所在契约类的缺口逐项列出新增属性，并保留原定义，不能将这个数量理解为 800 项都已具备数据来源。本文第 2 节增加的 operation_ref 等连接字段明确标为代码建议，不冒充原文档或已确认外部参数。

设计依据：[召回数据定义](运行时详细设计_V0.1/召回数据定义_V0.1.md)、[P2 接口对接需求](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md)。
