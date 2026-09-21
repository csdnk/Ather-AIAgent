# A 组对象与字段设计落地说明 V0.1

具体到“文档字段能否匹配现有代码、哪个类需要加什么属性”的核对结果，见[文档字段与现有代码逐项核对](A组_文档字段与现有代码逐项核对_V0.1.md)及其逐字段明细。本文保留对象分层说明。

更新：2026-09-14。适用范围：接口层框架文档、P2 字段级接口表，以及这些对象在当前 `AgentJYS-main` 代码中的承接方式。

**继续以文档中的对象与字段设计为目标推进。** 现有代码提供可复用的数据来源和实现基础，尚未覆盖的对象通过新增模型、字段校验和适配映射补齐。本文聚焦“对象放在哪里、字段从哪里来、现有对象怎样调整”，不展开完整业务流程或部署改造。

## 1. 对象分层与代码承接

对象分为公共值类型、A 内部对象和 P2 接口对象三类。同一字段在不同层可以有明确映射，但不能因为名字相近就视为同一事实。

| 对象层次 | 文档中的对象 | 代码承接方式 |
|---|---|---|
| 公共值类型 | Scope、ByteRange、Hash、ProjectionIdentity；EmbeddingModelBinding 由共享向量模块维护 | 提供统一类型和校验，其他模型引用同一定义 |
| A 内部对象 | SemanticEmbeddingRequest／Result／Execution、VectorProjectionRequest／Operation、ProjectionTargetBinding、ProviderResult | 新增专用模型，保存输入绑定、目标绑定、操作身份与机制结果；沿用数据定义中的 RecordHeader 和版本约定 |
| P2 接口对象 | P2CallContext／ResponseContext、P2SearchInput／Result、P2ContentReadInput／Result、P2UpsertInput、P2MutationResult 等 | 在 P2 适配边界新增强类型模型，表达请求参数及取得的事实；再映射到实际 Proto／SDK 字段 |

P2 接口对象是双方需要对齐的逻辑数据视图。公共上下文可落在 RPC metadata 或 SDK 上下文；某些结果字段可以来自原响应与已确认契约。A 的本地执行记录、租约、CAS 版本和受控证据引用不自动成为 P2 报文。

## 2. 逐类对象需要新增或修改什么

### 2.1 公共上下文与 Scope

**现有基础：** [RequestContext](../../../AgentJYS-main/src/aether_agent_memory/runtime/request_context.py)、[core.Scope](../../../AgentJYS-main/src/aether_agent_memory/core/scope.py)。

| 目标对象／字段 | 当前代码基础 | 对象或字段层面的调整 |
|---|---|---|
| P2CallContext | 现有 RequestContext 含 request_id、trace_id、tenant_id、deadline 等 | 新增独立的 P2 调用上下文；将 request_id 映射为 request_ref、deadline 映射为 deadline_at，并从固定配置／绑定取得 provider_ref、contract_ref |
| P2ResponseContext | P2 客户端当前主要返回列表、字节或简化对象 | 新增响应上下文，保留 request_ref、provider_ref、contract_ref、provider_request_ref、observed_at、error；远端未提供的可空字段显式为 null |
| 文档 Scope.project_id | 当前 Scope 没有 project_id | 在新契约 Scope 中补齐，并从可信授权范围取得；旧 user_id 保留其原用户身份语义，不能直接改名成 project_id |
| Scope 的其余维度 | 当前有 tenant_id、agent_id、session_id、task_id；tenant_id 可空 | 新契约要求 tenant_id 有有效值；其余维度按文档显式保留可空字段。缺少可信租户／授权依据时，不能构造已通过校验的新对象 |
| request_ref 与幂等键 | 当前 RequestContext 同时有 request_id 和 idempotency_key | 拆清单次调用关联、A 本地操作身份、P2 稳定幂等键、P2 返回操作引用；可以建立映射，不默认四者相等 |

新契约 Scope 与旧 Scope 通过显式转换并存。转换要保留原授权语义；无法证明的项目范围不从 user_id、字符串路径或默认配置猜测。

### 2.2 向量输入、输出及模型绑定

**现有基础：** [EmbeddingRequest、EmbeddingRecord、EmbeddingResult](../../../AgentJYS-main/src/aether_agent_memory/b1/models.py)，以及 [VectorQueryResult](../../../AgentJYS-main/src/aether_agent_memory/runtime/dtos.py)。

| 目标对象／字段 | 当前代码基础 | 对象或字段层面的调整 |
|---|---|---|
| SemanticEmbeddingRequest | 现有请求主要是 text、来源信息、请求标识和 metadata | 新增明确的 usage、caller_request_ref、input_ref、source_hash、input_binding_digest／ref、model_binding、deadline_at 等字段，按数据定义第 19 章实现 |
| EmbeddingModelBinding | 当前主要保留 embedding_model 或 model 字符串、向量长度 | 拆为 model_id、model_version、dimension、dtype、embedding_schema_version、preprocessing_version、retrieval_space_ref、model_contract_ref；值来自固定模型契约 |
| SemanticEmbeddingResult | 当前 EmbeddingRecord 有 vector、chunk_id、embedding_model、metadata | 新增向量摘要、模型／输入绑定及验证依据。A 内部保留受控 vector_ref；P2ProjectionPayload.vector 使用解析后的真实数值 |
| SemanticEmbeddingExecution | 现有请求／结果对象不表达文档中的完整共享调用绑定 | 新增独立记录对象，承接调用身份、执行与结果关联等字段；完整定义沿用第 25 章，不塞进 P2 请求 metadata |

`embedding_model` 可作为模型目录查找线索，不能仅凭一个模型名称补出模型版本、预处理版本和检索空间。维度应与实际向量核对；具体 dtype、字节序及摘要计算口径来自模型契约。

### 2.3 检索请求、检索结果与命中项

**现有基础：** [MemorySearchHit／MemorySearchResult](../../../AgentJYS-main/src/aether_agent_memory/runtime/dtos.py)、[P2VectorHit 与 search_vectors](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py)、[RecallCandidate／RecallSourceResult](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py)。

| 目标对象／字段 | 当前代码基础 | 对象或字段层面的调整 |
|---|---|---|
| P2SearchInput | 底层已有 query、top_k、collection；上层 Port 输入仍为 query 文本 | 新增消费真实向量的对象，包含 query_vector、usage、model_binding、retrieval_space_ref、scope、memory_types、时间筛选与 top_k |
| P2SearchResult | 当前主要是 items、namespace、query_model、query_dimension；RecallSourceResult 另有 complete 等来源信息 | 新增 requested_k、completion、partial_reason、ranking_contract_ref、query_binding_evidence、completion_evidence。来源级 complete 不能直接冒充 P2 的完成证据 |
| P2SearchHit | 当前 P2 命中有 id、score、metadata | 新增 projection_ref、provider_rank、raw_score、model_version、projection_schema_version、可空 identity 和 source_evidence；通过已确认映射解释原 id／score／metadata |
| raw_score | 当前 MemorySearchHit、RecallCandidate 的 score 默认 0.0 | 新对象保留 number 或 null；无分数不能默认成 0 分。需要兼容旧候选时显式处理未知分数 |

P2SearchHit 表示发现的候选，不直接承载 B 已批准的正文或领域可读结论。初始命中、资格核验结果和可信正文应使用各自对象；不能给旧 RecallCandidate 添几个字段后默认其已经通过所有校验。

### 2.4 正文目标、元数据与读取结果

**现有基础：** [ObjectReference](../../../AgentJYS-main/src/aether_agent_memory/runtime/dtos.py)、[P2ObjectMeta 与 get_object](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py)、[P2ObjectStoreAdapter](../../../AgentJYS-main/src/aether_agent_memory/adapters/p2.py)。

| 目标对象／字段 | 当前代码基础 | 对象或字段层面的调整 |
|---|---|---|
| P2ContentTarget | 当前 ObjectReference 有 provider、namespace、object_key、content_ref、metadata | 新增 content_version、representation_id、representation_type，以及经 B 批准的 bucket／key 映射；不能只靠一个 content_ref 表达完整身份 |
| P2ContentReadInput | 当前读取主要接收 content_ref 或 key | 新增 content、approved_range、expected_hash、version_condition_ref、max_response_bytes；expected_hash 可由 A 本地持有，用于校验 |
| P2ContentMetadata | 当前简化 P2ObjectMeta 仅有 bucket、key、etag、size | 新增准确内容版本、表示、编码、对象字节数、checksum、version_evidence；Proto 已有的 md5_hex／blake3_hex 按算法和覆盖契约转换 |
| P2ContentReadResult | 当前客户端仅返回 bytes 或 None，未保留 ObjectBytes.meta | 新增 status、content、data、returned_range、returned_bytes、meta、read_path、placement、cache_trace、evidence，保留原响应提供的元数据 |
| ByteRange | 当前 Proto 为 start／end，end 含上界 | 文档对象保持 [start,end)；适配到当前 Proto 时使用 start 和 end-1。不更改文档范围语义以迁就报文 |

`memory_version`、`content_version` 与物理对象版本分别表达不同事实。旧 `Memory.revision` 或 `source_revision` 只有经 B 的版本映射确认后，才可参与新对象绑定，不能同时填充所有版本字段。

### 2.5 投影身份、目标、载荷与检索属性

**现有基础：** [P2VectorIndexAdapter](../../../AgentJYS-main/src/aether_agent_memory/adapters/p2.py)、[B2 记录映射](../../../AgentJYS-main/src/aether_agent_memory/b2/p2_bridge.py)、[EmbeddingRecord](../../../AgentJYS-main/src/aether_agent_memory/b1/models.py)。

| 目标对象／字段 | 当前代码基础 | 对象或字段层面的调整 |
|---|---|---|
| ProjectionIdentity | 当前不同路径使用 memory.id，或 Memory ID 与 chunk_id 组合 | 新增五元组：memory_id、chunk_id、memory_version、model_version、projection_schema_version。旧 ID 不能直接充当完整新身份 |
| P2ProjectionTarget | 当前主要以 collection + VectorRecord.id 定位 | 新增 tenant_id、provider_ref、retrieval_space_ref、identity、physical_target_ref；为逻辑目标与物理 ID 建立稳定映射 |
| P2ProjectionPayload | 当前 EmbeddingRecord 以 vector、embedding_model 和自由 metadata 携带信息 | 新增 usage、vector、model_binding、source_hash、input_binding_digest、vector_hash、representation_id、content_ref、content_version、approved_range、metadata、metadata_hash |
| P2ProjectionMetadata | 当前 metadata 包含用户、关键词、category、正文等多种信息 | 按新契约明确为 scope、memory_type、occurred_at 三项语义视图；其余对象信息各归其位，不无条件复制旧 metadata |
| memory_type | 代码枚举值为 working／episodic／semantic；文档采用原契约标签 | 在映射中明确标签与枚举的对应，长期投影只接受契约认可的 Episodic／Semantic；不能仅调整大小写后就认定语义已签收 |
| occurred_at | 现有对象有 created_at、updated_at 等时间 | 由 B 提供业务发生时间；未知为 null，不用写库时间或更新时间代填 |

物理 `metadata_json` 可以是承载多个字段的容器，但摘要语义视图保持固定：`metadata_hash` 仅覆盖 `scope、memory_type、occurred_at`。A 本地 `owner_metadata_evidence_ref` 不发送到索引，也不进入该摘要。

### 2.6 写删请求、操作记录与结果状态

**现有基础：** [ProjectionWorkItem／ProjectionWorkStatus](../../../AgentJYS-main/src/aether_agent_memory/memory/projection.py)、[MemoryProjection](../../../AgentJYS-main/src/aether_agent_memory/core/memory.py)、[ProviderProjectionExecutor](../../../AgentJYS-main/src/aether_agent_memory/adapters/projection_executor.py)。

| 目标对象／字段 | 当前代码基础 | 对象或字段层面的调整 |
|---|---|---|
| P2UpsertInput／P2DeleteInput | 当前写入对象未完整表达目标条件和稳定远端操作身份 | 新增 target、provider_idempotency_key、request_fingerprint、precondition_ref；写入带 payload，删除带 related_upsert_keys |
| P2OperationQueryInput／P2TargetQueryInput | 当前 Python P2 客户端未提供对应的完整逻辑对象 | 分开定义原操作查询和准确对象查询；分别承接动作／原幂等键／操作引用，以及预期载荷指纹等字段 |
| P2MutationResult | 当前 upsert_vectors 返回 None；Proto 插入响应主要为 inserted 计数 | 新增操作类型、准确目标、原操作关联、operation_status、raw_status、target_state、resubmit_assessment 和证据字段 |
| P2TargetState | 当前对象中缺少准确目标的完整存储／索引观察 | 新增 object_present、stored_request_fingerprint、stored_payload、durable、index_queryable、delete_confirmed、late_write_barrier_confirmed 等；未知事实用 null |
| ProjectionTargetBinding／VectorProjectionOperation | 当前 ProjectionWorkItem 表达队列工作，主要含 memory_id、revision、kind、attempts、租约等 | 新增专用目标绑定与机制操作对象，保存五元组、目标摘要、操作键、请求指纹、远端引用和结果关联；队列工作对象通过引用关联它们 |
| ProviderResult | 当前主要有任务 succeeded 和 Memory.vector_projection_status 字符串 | 新增 A 的机制结果对象，使用 ACCEPTED／PENDING／READY／FAILED／UNKNOWN，关联准确操作、目标和证据；B 的领域 ProjectionState 独立维护 |

状态对象应明确分开：`ProjectionWorkStatus` 是队列工作状态，`P2OperationStatus` 是远端原操作状态，`ProviderResult.state` 是 A 的机制判断，`ProjectionState` 是 B 的领域状态。不能统一替换为一个 success 字段。

### 2.7 证据、错误与可选读取观察

新增 `P2Evidence`、`P2Error`、`P2ResubmitAssessment`、`P2ReadPlacement`、`P2CacheTrace` 等独立模型，承接来源、错误效果和观察事实。字段定义沿用 P2 字段表，不向通用 `metadata: dict[str, Any]` 无限制追加。

远端 `observed_at` 可空时保留 null，A 本地接收时间另存；`retryable=true` 不等于允许安全重发；没有预热路径或动作依据时，不生成“已命中”或“未命中”的肯定结论。可选热副本模型可以先定义，是否启用仍按既有范围确认。

## 3. 字段设计必须落实的规则

| 规则 | 对代码的具体要求 |
|---|---|
| 必须出现与允许 null 分开 | Pydantic v2 中，文档要求“字段出现但可空”的字段使用 `field: T \| None` 且不设置省略默认值；`= None` 会允许字段缺失。实际 Proto 没有该字段时，由适配层按契约显式补充 null 或拒绝不完整结果 |
| 序列化保留未知 | 新逻辑契约序列化保留 null；不复用旧 DTO 的 `exclude_none=True` 作为新对象统一输出规则。列表无条目用 []，缺少来源完成证据仍需单独表达 |
| 类型与数值约束 | uint 限非负安全整数，必要字段如 top_k／dimension 另要求 >0；向量值有限且长度匹配；禁止用有损转换后仍保留原摘要的方式兼容 dtype |
| 公共值对象统一 | Hash 拆 algorithm／value／byte_encoding／range；ByteRange 拆 start／end；模型绑定和五元组各用共享定义，避免多个 dict 各自命名 |
| 版本字段不合并 | Memory、内容、模型、Embedding Schema、Projection Schema、本地记录 schema_version 各保留语义；不按版本字符串大小判断新旧 |
| 摘要与身份固定 | target_digest、request_fingerprint、metadata_hash、vector_hash 分别按文档规定的字段与编码计算；不能将随机 request_id、瞬时 trace 或等待期限混入稳定投影身份 |
| 对象状态有条件约束 | 如 P2SearchResult 为 partial 时须有原因；正文 status=ok 时须有字节、元数据及一致范围；机制 READY 须有对应动作的完成依据。条件规则按主设计实现，不能只检查字段存在 |
| 本地与外部字段分界 | input_ref／vector_ref 在 A 本地可解析；向 P2 发送实际载荷。A 的租约、CAS、RecordHeader、证据表引用仅在相应内部对象中使用，不整包外发 |

这些规则适用于新对象。旧对象和旧协议的默认值、字段名通过兼容适配处理，避免直接全局修改造成已有入口或持久化数据误读。

## 4. 对象定义需要落在哪些代码模块

下表是后续实施的承接建议，新增路径目前尚未创建。完整字段继续以既有数据定义及 P2 接口需求为准，本文不另立一套字段真值。

| 模块 | 对象／字段工作 |
|---|---|
| 建议新增 `runtime/contract_types.py` | 承接文档版 Scope、ByteRange、Hash、ProjectionIdentity 等公共值类型及校验；避免与现有 core.Scope 静默混用 |
| 建议新增 `p2/contracts.py` | 定义 P2* 请求、结果、上下文、载荷、证据、错误和观察对象 |
| 建议新增 `b1/semantic/models.py` | 定义 SemanticEmbeddingRequest／Result／Execution、EmbeddingModelBinding；从现有 b1.models 和实际模型配置显式映射 |
| 建议新增 `memory/vector_projection/models.py` | 定义 VectorProjectionRequest／Operation、ProjectionTargetBinding、ProviderResult 及独立机制枚举；沿用数据定义第 21～24 章 |
| 现有 `adapters/p2.py`、`p2/client.py`、`runtime/dtos.py` | 补新旧对象和实际报文的转换，保留必要响应字段，区分未知与默认值；现有 DTO 继续作为旧入口兼容模型 |
| 现有 `runtime/ports.py` | 让新能力签名接收和返回对应强类型对象；旧 Port 的调用方通过兼容层迁移 |
| `engine/proto/aether_engine.proto` 与生成代码 | 仅在 P2 确认真实报文变更后更新共享 Proto 并重新生成；本地新增逻辑 DTO 不自动等于必须新增同名 RPC／字段 |

本地投影对象仍需按数据定义继承 RecordHeader，并保留 schema_version 等字段。它们与 P2CallContext 是不同的对象头，不能相互替代。

## 5. 对象与字段层面的验收

- **结构覆盖：** 每个文档字段都有对应代码属性，或有明确的外部字段／契约适配位置；复合类型能展开到子字段。
- **空值与序列化：** 区分缺字段、null、0、false、[]；新模型往返序列化保持语义，旧数据不能通过自动补默认值变成“已验证”。
- **身份与版本：** 五元组每个字段变化都能被识别；物理 ID 可追溯准确目标；不同版本字段不互相代填。
- **范围与摘要：** 校验 [start,end) 与 Proto 含上界范围的转换；相同语义视图得到一致摘要，排除文档规定的本地证据引用。
- **结果对象：** 缺失绑定或完成证据时不产生机制 READY；任务成功、远端完成和 B 领域 Ready 分别验证。

本次仅完成静态代码与文档对照，以及对象／字段的落地说明；尚未修改业务模型或执行这些验收测试。

## 6. 设计依据

- [A 组接口层功能框架节点](A组_接口层功能框架节点_V0.1.md)：功能边界与节点关系。
- [P2 接口对接需求](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md)：P2* 对象、字段、空值及物理适配语义。
- [召回数据定义](运行时详细设计_V0.1/召回数据定义_V0.1.md)：A 内部对象、第 19～25 章共享向量与投影对象、公共类型和版本规则。
- [Recall 源码对照与开发顺序](Recall源码对照与开发顺序_2026-09-14.md)：已有代码入口与后续实施背景。
