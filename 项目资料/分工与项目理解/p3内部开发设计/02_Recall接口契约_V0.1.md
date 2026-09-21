# Recall 接口契约 V0.1

日期：2026-09-16。本文依据[详细流程](../Recall流程/运行时详细设计_V0.1/召回流程详细设计_V0.1.md)和[数据定义](../Recall流程/运行时详细设计_V0.1/召回数据定义_V0.1.md)，用于实现与评审，不宣布外部接口已签收。

`RIF-01～14` 仅是本文索引。已有 Python 名称表示当前真实代码；标为“逻辑草案”的输出/调用语义尚不表示已有类、方法或 HTTP 路由。B/RF/P2 可以用其认可的既有接口满足需求。

## 1. 所有接口共同遵守的约束

| 维度 | 契约要求 |
|---|---|
| 身份与范围 | 明确 tenant、principal、授权证据及 Scope；显式 session/task 的归属和一致性必须确认。不能从客户端声称的 ID 推导允许 |
| 版本 | 区分 Memory/content/model/schema/策略版本、执行 state_version 和租约；不能拿其中一种替代另一种 |
| 幂等 | 保存原请求身份与语义摘要；同键同语义附着，同键不同语义冲突。重试不能刷新原 deadline、预算、来源或模型空间 |
| 时间 | 使用带时区时间；外部调用不能超过执行剩余预算，并为最终复核/可靠收尾预留时间。读调用与恢复调用额度分别管理 |
| 限额 | 候选数、TopK、正文单片/总字节数、token、并发和重试均有上限；不能只在下载完成后检查字节限制 |
| 完整性 | 完整空集、部分完成、明确无效、未知与失败分开。空列表本身不能证明没有记忆 |
| 副作用未知 | 超时、断连或回包丢失不能推断回滚。先查原绑定/原操作，不换 key 或 attempt 重发 |
| 证据 | 输出保留来源、版本、范围、摘要、契约版本与观察时间；存在 reference 不等于其证据内容已有效 |
| 取消 | 区分调用方停止等待与实际执行停止。尤其本地 CPU 推理取消后仍可能占用资源 |
| 协议 | 本文优先定义进程内逻辑契约。网络层状态码、字段序列化、服务地址另行按既定 Owner 契约映射 |

源代码完整模型位置：[Recall 模型](../../../AgentJYS-main/src/aether_agent_memory/recall/models.py)、[公共值类型](../../../AgentJYS-main/src/aether_agent_memory/runtime/contract_types.py)、[读取值对象](../../../AgentJYS-main/src/aether_agent_memory/runtime/recall_values.py)。字节范围按既有 `ByteRange` 约定处理，转换到 Provider 协议时须显式核对，不能自行猜测闭区间/半开区间。

## 2. 接口目录

| ID | 接口/阶段 | Owner / 调用关系 | 当前形态 |
|---|---|---|---|
| RIF-01 | Recall 请求受理 | A；入口调用 | `RecallAdmissionService.admit` 已实现 |
| RIF-02 | 授权与再次核验 | 授权 Owner 提供，A 消费 | `RecallAuthorizationPort`；真实适配待接 |
| RIF-03 | 准入 | A 定规则、RF 提供原子快照 | `RecallAdmissionPort.check` 已实现默认策略 |
| RIF-04 | 执行、检查点与阶段推进 | A 定业务规则、RF 存储 | `RecallExecutionStorePort` 与启动骨架 |
| RIF-05 | 请求复核及 Query 阶段 | A 内部 | Query 实验实现；需接新状态推进边界 |
| RIF-06 | 共享 Embedding | A 提供；A Query/B Passage 消费 | 模型、Port、共享服务及原生 ONNX 已有 |
| RIF-07 | Working 候选发现 | B 提供、A 消费 | RB-01 能力需求；逻辑草案 |
| RIF-08 | 长期向量候选发现 | P2 提供、A 适配 | `P2SearchInput/Result` 逻辑 DTO 已有 |
| RIF-09 | 候选事实与最终/重放复核 | B 提供、A 消费 | RB-02/04/05；部分本地复核记录模型已有 |
| RIF-10 | 正文映射、读取与校验 | B 映射、Provider 字节、A 校验 | P2 读取 DTO 已有；阶段处理器待实现 |
| RIF-11 | 排序与冲突处理 | A 排序，B 提供语义依据 | 逻辑草案 |
| RIF-12 | Context 组装 | A 内部 | 新流程处理器待实现 |
| RIF-13 | 最终提交、事件、查询与重放 | A 编排、RF 原子存储/通道 | 数据模型与重放 Port 边界已有，正文重放/终结待实现 |
| RIF-14 | 向量投影提交/查询/删除机制 | A 提供、B 编排消费、P2 执行 | `VectorProjectionPort` 和模型已有，协调器待实现 |

## 3. RIF-01：请求受理

**当前签名：** `async RecallAdmissionService.admit(request: RecallInput) -> RecallAdmission`。

| 输入字段 | 约束 |
|---|---|
| request_id、trace_id、idempotency_key | 必须显式传入；request_id/trace_id 不替代业务幂等键 |
| query | 非空，保留原文作为输入摘要依据；受 max_query_chars 约束 |
| scope | tenant_id 必需；project/agent/session/task 按公共模型可为空，非空项必须验证 |
| principal_ref、authorization_ref | 标识调用身份/证据；授权适配器再次验证，不能直接信任客户端 |
| deadline_at、token_budget | 显式预算；正 token 预算不接受 bool，服务端策略进一步限制 |
| retrieval_constraints | 类型及业务时间筛选；类型未给出按策略展开，显式空列表拒绝，反向时间范围拒绝 |
| tokenizer_id/version、template_version | 现有实验输入允许省略并由服务端解析；显式传入须匹配原绑定。面向外部的新 DTO 是否隐藏这些字段由接入契约决定 |

客户端不得指定 `retrieval_mode`、`source_selection` 或 `allowed_sources`。三模式由 `scope_union_v1` 根据已确认范围、类型和授权决定；不依赖 Query 关键词、缓存命中或服务健康。

输出 `RecallAdmission` 包含内部 request/index/execution/policy/reserved_events，是内部绑定，不是 Context，也不是向外原样序列化的 HTTP 响应。对外执行引用的字段和状态码映射关联 AL-P401/P403。

失败边界：模型校验错误在接入层映射为请求错误；业务 `RecallError.code` 区分授权、准入、幂等等原因。`RecallAdmissionUnconfirmedError` 是保存结果尚未查证的传输层暂不可用，不是第五种业务终态。

## 4. RIF-02 / RIF-03：授权与准入

**授权已有签名：** `async authorize(request: RecallInput) -> RecallAuthorization`。

授权返回完整 scope、principal_ref、evidence_ref、scope_valid、working_read、long_term_read、valid_until。三个可空布尔字段的 `None` 表示不能确认；不能按 False 的“不适用”路径继续选路。显式 session/task 非法时不能丢弃编号后继续。

重试、执行复核、最终输出及重放均按对应边界重新确认权限；受理时的一次允许不覆盖整个执行生命周期。执行复核如何读取 B 的当前事实关联 AL-RF01/B03。

**准入已有签名：** `check(snapshot: AdmissionSnapshot, policy: RecallPolicy) -> None`。

- snapshot 含当前 active_executions 和 reserved_events。
- check 是同步、无外部副作用的纯判断；在 RF 原子受理边界内执行，不能先查额度再竞争写入。
- 已有请求绑定优先附着，不重复预留；新请求准入失败不创建执行。
- 当前 `BoundedRecallAdmission` 与内存替身可验证规则；跨进程额度和数据库隔离仍由 RF 验收。

## 5. RIF-04：执行、检查点与阶段推进

完整已有签名见 [ports.py](../../../AgentJYS-main/src/aether_agent_memory/recall/ports.py)。

| 方法 | 输入/输出 | 必须保证 |
|---|---|---|
| find | tenant_id + request_key → 既有 RecallAdmission 或 None | None 必须证明无绑定及无在途受理；未解决意图报 unknown，旧副本 not_found 不算证明 |
| get | tenant_id + recall_id → 既有执行绑定 | 校验租户与执行引用 |
| admit | 请求键、候选绑定、gate、授权有效期 → 既有/新绑定 | 原子保存请求、索引、执行、策略与额度；并发仅一个执行 |
| claim | tenant、recall_id、expected_version、owner、lease_until → RecallExecution | 领取/续接条件与 deadline 在提交边界核验 |
| save_checkpoint | ExecutionGuard + RecallCheckpoint + output_json → RecallExecution | 输出与检查点、执行引用一致保存；绑定输入/输出摘要与原策略，不可随意覆盖 |
| advance | ExecutionGuard + target → RecallExecution | 校验合法边、state_version、lease_token、有效租约与上一阶段检查点；不是最终发布接口 |

`ExecutionGuard` 为 tenant_id、recall_id、state_version、lease_token。失租或旧版本不能推进，也不能通过重新构造对象绕过检查。

十三态沿用代码：CREATED；八个 RUNNING 阶段；四个业务终态。当前 `RecallExecutionService.start` 只负责从 CREATED 进入 `RUNNING_REQUEST_VALIDATION`。后续阶段遵循“执行效果先记录、输出可核验、检查点确认、再推进”。分支跳过也必须有真实跳过原因和检查点，不伪造向量。

## 6. RIF-05：执行复核与 Query 阶段

**现有实验入口：** `RecallQueryService.prepare(raw) -> PreparedQuery`；输出 recall_id、可空 query_result、可空 search_input。

**下一批内部交接草案：** 阶段处理器接收已受理的请求引用和 ExecutionGuard，读取原策略/预算，完成当前权限与输入绑定复核，保存输出检查点，再调用存储 Port 推进。具体处理器类名在实现时确定，不另建第二条受理路径。

| 模式 | Query 行为 | 输出约束 |
|---|---|---|
| working_only | 跳过推理和长期搜索 | query_result/search_input 为空，有明确跳过证据，后续仍需 Working 来源事实 |
| long_term_only | 附着同一共享 Query 执行并核验结果 | 模型、用途、维度、空间、输入摘要及结果引用匹配才构造 P2SearchInput |
| combined | Query 与 Working 来源按既定预算编排 | Query 失败记长期缺口；在请求仍安全且预算允许时继续 Working，不改写原模式 |

当前实验 `prepare` 会直接写 atomic-record 状态，不能与新租约/CAS骨架并行充当两个状态 Owner。需要先迁入 RIF-04，再交付 combined 降级、收尾预留和读取记账。请求级授权失败不能用“降级继续 Working”绕过。

## 7. RIF-06：共享 Embedding

| 已有接口 | 签名/输出 | 消费规则 |
|---|---|---|
| EmbeddingInputPort | authorize(request) → bool；resolve(request) → ResolvedEmbeddingInput | Query 由 A 从已受理输入解析；Passage 由 B 批准并解析固定正文/片段。只有明确 True 允许 |
| SemanticEmbeddingCapability | embed(request) → SemanticEmbeddingResult；vector_for(request, result) → list[float] | 先绑定共享执行，再获取/校验向量；保留原 caller_request_ref，不以新 attempt 获取新额度 |
| EmbeddingBackendPort | compute(request, text) → ComputedEmbedding | 被共享服务调用；不执行分块、Memory 变更或投影写入 |

`SemanticEmbeddingRequest` 必需信息：tenant/caller/caller_request/trace/authorization，usage=Query 或 Passage，input_ref、source_hash、input_binding_digest、input_binding_ref，model_binding、deadline_at、execution_policy_ref、reuse_digest。完整模型见 [embedding/models.py](../../../AgentJYS-main/src/aether_agent_memory/recall/embedding/models.py)。

`model_binding` 固定 model_id/version、dimension、dtype、embedding_schema_version、preprocessing_version、retrieval_space_ref、model_contract_ref。结果返回输入绑定、结果/向量引用、向量摘要和验证证据。向量生成成功不等于可检索，也不等于 B 的领域 Ready。

重试唯一 Owner 是共享 Embedding 服务。上层重试附着同一请求；计算后端不得另开隐式重试或 fallback。当前服务只有显式 `TransientEmbeddingError` 才按原额度重试，不能把未知错误全部标成可重试。

原生 ONNX 已运行 BGE 中文 512 维 Query/Passage。NativeEmbeddingBackend 在启动时加载真实模型，用 `bind(approved_binding, records=rf_records)` 校验部署；count_tokens 使用真实 tokenizer，包含前缀和特殊 token。原生实例一个实际工作线程，装配的并发策略应匹配；Query/Passage 使用独立实例。详情见[真实推理说明](../../../AgentJYS-main/docs/recall_embedding_native.md)。

错误码沿用 `EMBEDDING_INPUT_INVALID`、`EMBEDDING_BINDING_MISMATCH`、`EMBEDDING_BUSY`、`EMBEDDING_COMPUTE_FAILED`、`EMBEDDING_DEADLINE_EXCEEDED`。Recall 按来源与请求级影响映射，不把它们直接当最终业务状态。

## 8. RIF-07 / RIF-08：候选发现

### Working Read：B 提供，RB-01，逻辑交接草案

- 请求至少交接：调用关联、授权 Scope、合法 session/task、类型/时间筛选、候选上限、允许接收字节上限及调用 deadline。
- 响应至少交接：逐候选的稳定身份、Memory/内容版本、TTL、来源引用、正文或批准的内容引用、来源范围是否完整及证据。
- 完整空集可确认 Working 为空；部分结果保留候选与缺口；授权或版本未知不作为可信正文。
- 内联正文在接收前纳入字节预算，不能宣称“Working 不计读取字节”；进入后续校验不重复扣费。
- 不要求 B 接收 Recall 的内部 mode；B 提供范围内事实，A 决定是否调用。实际方法与字段需 B 签收 AL-B01/B03。

### Vector Search：P2 提供，RP2-01

已有逻辑 `P2SearchInput` 包含 query_vector、usage=Query、model_binding、retrieval_space_ref、scope、memory_types、occurred_after/before、top_k。

已有 `P2SearchResult` 包含 retrieval_space_ref、requested_k、completion、hits、partial_reason、ranking_contract_ref、query_binding_evidence、completion_evidence。completion 为 complete/partial/failed；每个 hit 保留 projection_ref、provider_rank、raw_score、model/schema 版本及来源证据。

命中只代表候选，不代表 Memory 合格、正文可读或进入 Context。必须继续 RIF-09/10。P2 DTO 是本地逻辑模型，不宣称真实 RPC 已兼容；调用还需要对应 P2CallContext/授权适配，不能因为 search DTO 无 principal 字段而丢掉授权。

**A 内部候选汇合草案：** 输出来源候选集合、各来源有界完成事实、排除/缺口记录、稳定来源引用。按批准的准确身份去重，保留多来源证据；不能仅按 memory_id 把不同版本/范围合并。完整字段沿用数据字典，阶段聚合 DTO 在下一批实现时核对后落地。

## 9. RIF-09：候选资格、语义依据与最终复核

这是 B 提供的能力，A 执行调用和消费。RB-02/04/05、AL-B03/B04/B05。

| 调用时点 | 输入 | 需要的结果 |
|---|---|---|
| 初次资格核验 | 当前请求授权、准确候选 Memory/版本/表示/范围 | 当前 Type/状态/TTL、领域可读与模型兼容、逐项资格及证据 |
| 语义/冲突处理 | 已确认候选及其证据 | B 既有质量/衰减策略版本、冲突成员、允许的回退方式 |
| 最终复核 | 全部已加载可组装候选与替补的准确身份，加当前请求授权 | 逐项当前权限/版本/状态/TTL与证据窗口；不能只核验最终初选项 |
| 终态重放复核 | 原执行/结果引用、当前主体与拟重放项身份 | 只读安全复核；不延长原执行或重新检索补正文 |

逐项必须能区分有效、权威无效和无法核实；实际 wire 枚举由 B 签收。响应漏项、批次超时或证据过期均不能沿用初查结果补齐。候选级未知记缺口；请求级授权失败阻断整包输出。

本地已有 `FinalValidationBatch/Item` 存结果记录，但还没有真实 B 适配和完整处理器。B 批量上限、复核窗口及版本映射待 AL-B03 确认；A 不自行设定跨服务线性一致保证。

## 10. RIF-10：正文映射、读取和校验

顺序：B 批准映射 → 预留调用/字节额度 → 读取准确版本/范围 → 校验实际字节 → 保存读取记账及检查点。

- B 输出应包含批准的 representation/content_ref/content_version、范围、编码及可信 expected_hash。A 不以内容 Provider 自报摘要作为唯一的预期依据。
- 已有 `P2ContentReadInput`：content、approved_range、expected_hash、version_condition_ref、max_response_bytes。
- 已有 `P2ContentReadResult`：status、content、data、returned_range、returned_bytes、meta、read_path、placement、cache_trace、evidence。结果里的字节数、范围、版本、编码和摘要都需要校验。
- 断流、超限、版本变化、实际接收量未知分开记录；重试受原读取预算约束。错误候选的正文不能作为另一个版本采用。
- Working 已内联提供可信正文时不重复下载；仍执行等价校验及最终复核。
- 可选 Prewarm 只能读取 B 批准的同版本/同范围内容。Provider 已回退与调用方负责回退必须区分，不重复回退或重复扣费。基础链路可先关闭 Prewarm。

本地有 `RecallReadLedger/Attempt` 与 P2 DTO；执行记账与真实有界读取适配仍待实现。关联 AL-B02、AL-P203、AL-RF04；预热另关联 AL-C01/C04。

## 11. RIF-11 / RIF-12：排序与 Context 组装

**排序是 A 内部阶段，逻辑草案：** 输入已校验的候选正文组、来源覆盖、B 的质量/衰减/冲突依据以及原策略版本；输出稳定有序内容组、排除原因和未解决缺口。B 拥有冲突真值与领域策略；A 拥有融合、最终排序和预算选择。不得悄悄丢掉冲突成员后宣称冲突已解决。

**组装是 A 内部阶段，逻辑草案：** 输入排序结果、最终复核后的有效候选、原 tokenizer/template 与 token_budget；输出待提交 Context、引用/出处、预算计数、来源覆盖及拟议结果分类。只采用检查通过且仍在复核有效窗口内的内容。

有合格内容但所有完整组都放不下，是预算无法满足，不能伪装 COMPLETE_EMPTY。合格内容不足或来源不完整的处理沿用主设计。候选选择、token 统计和终态分类都必须可由记录重算。

当前旧系统有 Context/检索实现，但新 `recall/` 下这两个完整阶段尚未交付。可参考旧算法，经接口与状态规则核对后迁移，不能直接调用 B2 绕过新边界。

## 12. RIF-13：可靠收尾、事件与终态重放

### 最终提交逻辑草案

输入为原执行及租约/版本、固定 commit_id/generation、待提交结果及摘要、最终复核证据、拟议终态、最小 Trace 和已产生的 Outbox 事件。RF 必须原子保存这些一致状态，并支持按原提交身份查询结果；具体方法需 AL-RF02 签收，不能通过普通 `advance` 直接发布终态。

提交结果未知时，保留未终态与原提交身份，先查询。最终化未确认可靠完成时，不发布 Context 或业务终态，只返回暂不可用语义。超期收尾不能把旧执行变成一次新召回。

| 业务终态 | 核心条件 |
|---|---|
| COMPLETE_AVAILABLE | 有安全内容，必需来源完整，无未解决缺口 |
| COMPLETE_EMPTY | 必需来源完整且证据确认无合格内容，不是故障导致的空 |
| DEGRADED_AVAILABLE | 有安全内容，但仍有来源/内容/语义等未解决缺口 |
| FAILED_UNAVAILABLE | 安全失败、预算无法满足或无法形成可信上下文 |

这些状态均以可靠最终提交完成为发布前提；详细判断顺序沿用主设计 4.1，不能仅按返回条目数决定。

### 访问事件、失效与重放

- A 本地事件映射为 RF 已签收的 AccessTrace Schema，经可靠 Outbox 补发，C 独立消费。记录命中、加载、选中、发出等不同阶段；无调用方回执不声称实际模型使用。
- 删除/过期/版本失效由 B 提供权威事实，A 清理自己拥有的派生内容并限制重放；清理通知不能替代每次访问复核。通道及保留策略关联 AL-B06/RF02/RF03。
- 已有 `RecallReplayPort.revalidate(tenant_id, recall_id, principal_ref) -> None` 只是未来复核边界，不提供正文重放。
- 终态重放需要当前授权/事实复核及原结果仍可读取。正文已清除不能重新检索拼成“原结果”；响应语义与最小幂等记录保留关联 AL-P401/P403。

## 13. RIF-14：向量投影机制

已有签名：`submit(VectorProjectionRequest) -> ProviderResult`；`query(tenant_id, caller_ref, authorization_ref, operation_id) -> ProviderResult`。删除通过 request.operation_kind 表达，不另假定一个 HTTP delete 路由。

| 部分 | 必需绑定 |
|---|---|
| 请求身份 | tenant/caller/caller_request、授权和 owner_evidence、trace、原策略、等待 deadline、request_fingerprint |
| 准确目标 | provider_ref、retrieval_space_ref，加五元组 memory_id/chunk_id/memory_version/model_version/projection_schema_version |
| upsert 载荷 | 已验证 Passage 结果引用、representation/content/version/range、正文证据、检索元数据及 metadata_hash |
| 结果 | operation_id、观察版本、准确目标、ACCEPTED/PENDING/READY/FAILED/UNKNOWN、绑定/完成/删除屏障证据、retry_advice |

操作效果未知先查原 operation/key；普通 not_found 不授权重发。只有能确认原操作无效果、不会迟到生效且原键允许安全重交时，才按已签收规则执行。query 也要验证当前调用权限。

READY 表示 A 确认的机制事实，由 B 再核验当前 Memory、版本、模型空间和可查询性后决定领域 Ready。旧操作 READY 不能证明当前缺失对象已修复。

当前设计明确 `build_intent=rebuild` 的同版本重建尚不支持，需拒绝并不调用 P2；不得以新 task/attempt 换键绕过。具体拒绝码、代际和删除屏障按主设计与 AL-B08/P206/P207 统一。delete 需要确认索引退出及旧在途写不能复活目标。

**交付边界：** 当前仅模型和 Port，尚无上述完整协调器。P2 DTO 在 [p2/contracts.py](../../../AgentJYS-main/src/aether_agent_memory/p2/contracts.py)；真实 wire 映射与原子恢复能力不能由这些 DTO 的存在推断。
