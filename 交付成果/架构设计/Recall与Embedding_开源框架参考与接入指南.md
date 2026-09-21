# Recall 与 Embedding：开源框架参考与接入指南

2026-09-20实现状态修订：研读建议中的 Recall 模块化、CrossEncoder、真实 token 预算及 Milvus 适配代码已实现。本文保留学习路径，采用状态更新如下；详细运行方式见[流程实现说明](Recall与Embedding_流程实现说明.md)。

核查日期：2026-09-20。依据：现有代码、各项目官方仓库及本次核对的源码快照。本文是技术研读与适配建议，不修改既有 PRD 或宣布新能力已经实现。

## 1. 推荐结论

**优先学习 LlamaIndex、FastEmbed、Sentence Transformers；Haystack 补充学习组件协作。Milvus/PyMilvus 用于接入真实向量检索，TEI 留到独立推理服务阶段。** 这些项目分别解决不同问题，不需要全部装进系统。

| 研究对象 | 为什么选 | 重点参考什么 | 本项目采用方式 |
|---|---|---|---|
| LlamaIndex | 检索入口、候选类型、向量查询、融合、后处理有清晰分层 | BaseRetriever、VectorIndexRetriever、QueryFusionRetriever、BaseNodePostprocessor | 优先参考流程和接口；保留我们的 Recall 编排与 ContextPack |
| FastEmbed | 与当前 ONNX/CPU Embedding 实现直接相关，研读后能解释实际推理链 | TextEmbedding、模型专属 Query/Passage 处理、ONNX Session、批量推理 | 当前已间接使用；继续作为推理实现，不交给它管理记忆或恢复 |
| Sentence Transformers | 同时能解释双编码检索和 CrossEncoder 重排序，便于验证模型适配 | encode_query、encode_document、encode、CrossEncoder.rank/predict | 独立实验与可选重排序后端；暂不替换现有 FastEmbed |
| Haystack | 组件输入输出、异步执行、候选合并便于团队划分职责 | Pipeline.run_async、DocumentJoiner | 参考组件组织及测试方法；不额外引入第二套业务任务系统 |
| Milvus / PyMilvus | 承接既定的真实向量存储与查询目标 | search、过滤、输出字段、写入/删除、可见性、超时 | VectorPort SDK适配已实现；默认保留SQLite，真实Milvus服务联调待环境 |
| Hugging Face TEI | 将推理变成独立服务，具备动态批处理和服务级观测能力 | 请求校验、批处理队列、并发限制、健康与 Trace | 后续 Azure/AKS 推理服务候选；当前单实例不必引入 |

LlamaIndex、Haystack 是编排/检索工具；FastEmbed、Sentence Transformers 是模型推理相关库；BGE 是模型；Milvus 是向量数据库；TEI 是推理服务器。不要把它们当成同一层的替代品。

## 2. 与现有代码的关系

以下路径相对 `AgentJYS-main/src/aether_agent_memory/`：

| 现有位置 | 本次检查到的实现 | 参考框架能帮助改进的部分 |
|---|---|---|
| [recall/basic/service.py](../../AgentJYS-main/src/aether_agent_memory/recall/basic/service.py) | 协调独立来源发现、融合、重排序、预算及最终提交；保存访问事件与阶段证据 | LlamaIndex的检索分层已用于模块拆分；继续通过输入输出测试验证边界 |
| [recall/basic/adapters.py](../../AgentJYS-main/src/aether_agent_memory/recall/basic/adapters.py) | SQLiteVectors 在本地做向量查询；LexicalEmbedding 仍服务 Working 排序及显式测试模式 | 同一个VectorPort已有milvus.py实现；保留SQLite本地能力，真实Milvus联调另验 |
| [recall/embedding/p3.py](../../AgentJYS-main/src/aether_agent_memory/recall/embedding/p3.py) | NativeP3Embedding 接新底座，绑定模型空间、校验输入、保存执行证据 | 保留适配壳；底层可接 FastEmbed，未来可接 TEI |
| [recall/embedding/native.py](../../AgentJYS-main/src/aether_agent_memory/recall/embedding/native.py) 与 [backends.py](../../AgentJYS-main/src/aether_agent_memory/recall/embedding/backends.py) | 真实模型推理、Query/Passage 格式化、输入 token 校验及后端封装 | 跟读 FastEmbed 的模型加载、预处理与执行；用 Sentence Transformers 做对照实验 |
| [recall/contracts/ports.py](../../AgentJYS-main/src/aether_agent_memory/recall/contracts/ports.py) | RecallPort、EmbeddingPort、VectorPort | 保持业务依赖这些接口，不让框架对象穿透到三流程公共契约 |

当前主线已接真实 BGE 中文模型，默认512维；默认向量后端为SQLite。来源发现、融合、重排序和预算已拆分为独立模块，真实CrossEncoder可通过配置启用并已完成本地验证；Milvus适配代码及协议故障测试完成，真实服务联调待环境。

研读时发现的旧 ByteTokenizer 字节预算问题已在2026-09-20修正：当前实际使用可配置tokenizer，默认o200k_base。Embedding 输入长度和最终 ContextPack 预算分别使用对应模型的计量规则；下游模型变更仍须单独校准。

## 3. LlamaIndex：重点学习 Recall 怎样拆步骤

### 从这些文件开始

按表中顺序看，不必通读整个仓库。链接固定到本次核查的 commit，避免主分支更新后找不到同一段代码。

| 顺序与源码 | 看什么 | 对应我们的职责 |
|---|---|---|
| 1. [schema.py：QueryBundle / NodeWithScore / TextNode](https://github.com/run-llama/llama_index/blob/f475afd8a9bbda84f252567e045d89d07b5701b3/llama-index-core/llama_index/core/schema.py) | 查询、节点引用、正文和分数如何分开；metadata 怎么参与内容表达 | 定义候选引用和记忆快照的边界，不能把 Node 当作正式 Memory |
| 2. [base/base_retriever.py](https://github.com/run-llama/llama_index/blob/f475afd8a9bbda84f252567e045d89d07b5701b3/llama-index-core/llama_index/core/base/base_retriever.py#L192) | retrieve/aretrieve 如何包装查询，_retrieve/_aretrieve 如何扩展 | WorkingRetriever 与 LongTermRetriever 的统一入口 |
| 3. [indices/vector_store/retrievers/retriever.py](https://github.com/run-llama/llama_index/blob/f475afd8a9bbda84f252567e045d89d07b5701b3/llama-index-core/llama_index/core/indices/vector_store/retrievers/retriever.py#L104) | 查询向量、过滤、Top K、向量命中与文档加载的关系 | 调 EmbeddingPort、VectorPort，再由 MemoryReadPort 读取确切版本 |
| 4. [retrievers/fusion_retriever.py](https://github.com/run-llama/llama_index/blob/f475afd8a9bbda84f252567e045d89d07b5701b3/llama-index-core/llama_index/core/retrievers/fusion_retriever.py#L33) | 多路结果收集、融合模式、RRF 和去重 | 单独提炼 CandidateFusion；固定我们自己的排名语义 |
| 5. [postprocessor/types.py](https://github.com/run-llama/llama_index/blob/f475afd8a9bbda84f252567e045d89d07b5701b3/llama-index-core/llama_index/core/postprocessor/types.py#L12) | 后处理器接收候选和查询、输出处理后的候选 | 排序、预算前筛选、可选重排序；最终授权和有效性校验仍是强制步骤 |

建议先画出：`RecallRequest → 选来源 → 候选列表 → 精确加载 → 融合 → 可选重排序 → 预算装配 → 最终核验 → ContextPack`。这是我们应实现的业务顺序，不要求照搬框架每一条调用。

### 不能直接照搬的地方

本次源码中，`QueryFusionRetriever` 的 `num_queries` 默认是 **4**，默认融合模式为 **SIMPLE**，并非默认 RRF。若只研究单查询融合，要显式关闭查询扩写、明确选择融合算法。设置 `num_queries=1` 可以避免扩写调用，但构造器仍解析 LLM 设置；实验中仍要明确处理 LLM 配置，不能断言它完全没有模型依赖。

其 RRF 按 `node.hash` 合并、排名从 0 开始；我们当前按范围内的记忆引用/版本去重，排名从 1 开始，并使用 `1 / (60 + rank)`。这是需要契约测试覆盖的行为差异，不能仅因同名 RRF 就替换实现。

框架里的 vector store 在某些配置下返回正文、在另一些配置下补读文档。这不表示正文已通过本系统授权或当前版本校验。我们仍必须从 Remember 的读取接口确认权威状态。

### 最小练习

手工构造两路候选：Working 返回 A v2、B v1；长期索引返回 A v1、A v2、C v1。逐步输出每路名次、过滤原因、合并键及最终名次。让 A v1 成为过期版本，C 属于另一个用户，确认两者不能进入 ContextPack。此练习先不用 LLM。

完成后，把现有 `Recall.retrieve()` 中的候选发现、融合、装配提炼成可单测模块；授权、最终核验、事件事务和 RF 执行记录继续留在服务边界。**采用的是模块化方法，不是完整 Query Engine。**

官方 [README](https://github.com/run-llama/llama_index/blob/f475afd8a9bbda84f252567e045d89d07b5701b3/README.md) 当前说明公司重点转向文档解析与提取；框架仍公开提供。这个信息支持控制引入范围，但不能据此宣称框架已停止维护。

## 4. FastEmbed：先吃透已经在运行的 Embedding

### 阅读路径

| 源码 | 必须弄清的问题 |
|---|---|
| [text/text_embedding.py](https://github.com/qdrant/fastembed/blob/cc4d101828eb9ec1f9c68f42716f8a85b114e41e/fastembed/text/text_embedding.py#L157) | embed/query_embed/passage_embed 怎样转交给具体模型；batch_size、parallel 是什么 |
| [text/onnx_embedding.py](https://github.com/qdrant/fastembed/blob/cc4d101828eb9ec1f9c68f42716f8a85b114e41e/fastembed/text/onnx_embedding.py#L222) | 模型选择、加载、实际 embedding 及后处理怎样组织 |
| [common/onnx_model.py](https://github.com/qdrant/fastembed/blob/cc4d101828eb9ec1f9c68f42716f8a85b114e41e/fastembed/common/onnx_model.py#L58) | ONNX Session、providers、线程和会话选项怎样生效 |

把这些步骤串起来：`已校验文本 → 模型所需前缀 → tokenizer → ONNX 推理 → pooling → normalization → 向量`。具体 pooling 和前缀取决于模型及适配实现，不要依据某个通用方法的注释推定所有模型都一样。

### 我们具体怎么用

继续走 `Remember/Recall → EmbeddingPort → NativeP3Embedding → NativeEmbeddingBackend → 推理后端`。Query 与 Passage 角色必须传递到预处理；本项目已经做格式化时，要防止再次调用带自动前缀的方法导致双重前缀。

需要由我们保留的约束：模型/权重与预处理指纹、维度、dtype、pooling/归一化契约、输入绑定、任务 deadline、范围授权、Trace 和结果证据。即使两个模型都输出 512 维，也不能直接共享向量空间。

FastEmbed 同时支持 Query/Passage 接口，不代表同一文本一定产生不同向量；区别由模型规则决定。本项目当前 Query 前缀是其具体绑定的一部分。

### 最小练习

用当前同一模型和 tokenizer，分别处理“我喜欢喝拿铁”和“用户习惯喝什么饮品”。保存角色、实际模型输入、token 数、向量维度、范数和绑定指纹；重复调用确认输入绑定稳定。再输入超长文本，确认按既定规则明确拒绝，而不是未经声明地截断。

后续单独验证调用超时/取消：协程结束不代表 CPU 推理线程已停止；不能提前释放容量、关闭仍被使用的存储，或把迟到结果记成有效成功。现有 native 后端已有相关处理，研读时对照它解释原因。

不要为了用 FastEmbed 就把向量库改成 Qdrant。FastEmbed 输出向量，存储仍可使用 Milvus。

## 5. Sentence Transformers：补充模型适配与重排序

### 当前源码位置

本次主分支已经不是一些旧教程中的 `sentence_transformers/SentenceTransformer.py` 与 `cross_encoder/CrossEncoder.py`。以这些已验证链接为准：

| 源码 | 重点 | 用在哪里 |
|---|---|---|
| [sentence_transformer/model.py](https://github.com/huggingface/sentence-transformers/blob/f3864a53cfafb40a6f03f029ce96da70591e9931/sentence_transformers/sentence_transformer/model.py#L329) | encode_query、encode_document、encode；prompt/task、批量、归一化 | 理解 Embedding Query/Passage 语义，验证备选模型适配 |
| [cross_encoder/model.py](https://github.com/huggingface/sentence-transformers/blob/f3864a53cfafb40a6f03f029ce96da70591e9931/sentence_transformers/cross_encoder/model.py#L641) | rank/predict 同时处理 query 和候选正文 | 在融合后、上下文预算装配前增加可选 Reranker |
| [util/retrieval.py](https://github.com/huggingface/sentence-transformers/blob/f3864a53cfafb40a6f03f029ce96da70591e9931/sentence_transformers/util/retrieval.py#L162) | semantic_search 的相似度与分块计算 | 小样本离线检索对照，不拿它替代生产向量数据库 |

Embedding 通常先把查询和正文分别编码，适合从大量候选中筛选；CrossEncoder 将 query 与每条候选配对计算，更适合对少量候选重新排序。**CrossEncoder 分数不是拿来写入 Milvus 的记忆向量。**

### 接入建议与练习

先保留当前 Top 20 候选、最终最多 5 条的主线，用同一批已获授权且当前有效的候选对比“现有 RRF 顺序”和“融合后 CrossEncoder 顺序”。记录每条候选的原始位置、新位置、使用的模型版本及耗时；不因框架名称就认定中文效果一定更好。

候选重排序前核验访问资格，避免把越权正文送入模型；最终返回前再核验，以覆盖执行期间撤权、纠错或删除。

若实验支持采用，再新增小型 `Reranker` 接口和适配器；这是建议接口，当前代码尚未实现。超时是否允许回退到融合顺序、是否标记 degraded，先写进契约，不能静默当作完整重排序成功。

FastEmbed 与 Sentence Transformers 输出相近不等于可以混用。只有权重、tokenizer、前缀、pooling、归一化等一致且验证通过，才能讨论共享空间；换模型通常需要新空间和重建索引。

## 6. Haystack：学习团队如何把步骤接起来

| 源码 | 参考点 | 对应到我们的改造 |
|---|---|---|
| [core/pipeline/pipeline.py](https://github.com/deepset-ai/haystack/blob/b717d00654af0dc405e5cec35546d734c832042f/haystack/core/pipeline/pipeline.py#L1084) | Pipeline.run_async、组件调度、concurrency_limit、按组件包含输出 | 固定步骤输入输出、并发上限和阶段输出，便利协作和定位 |
| [components/joiners/document_joiner.py](https://github.com/deepset-ai/haystack/blob/b717d00654af0dc405e5cec35546d734c832042f/haystack/components/joiners/document_joiner.py#L46) | 多列表合并、去重、不同融合模式 | 与 LlamaIndex 交叉理解候选融合，约定 score 的含义 |

本次源码中异步入口已在 `Pipeline` 内。旧教程的 `async_pipeline.py` 在本次快照不存在，因此不要拿旧文件名直接定位最新版。

练习：接“Working 候选组件”和“长期候选组件”，让长期路由主动超时，查看哪些步骤已完成、哪些未完成。随后按我们的规则决定 degraded 或 failed；通用 Pipeline 不会替我们决定来源覆盖是否足够。

借鉴组件边界即可；不同时引入 Haystack Pipeline、LlamaIndex Query Engine 和 RF Task 三套主编排。Pipeline 并发限制也不等于业务租户配额，更不自动提供跨进程持久租约和外部副作用恢复。

## 7. Milvus 与 TEI：两项后续接入

### Milvus / PyMilvus：真正的向量检索实现

优先读 [AsyncMilvusClient](https://github.com/milvus-io/pymilvus/blob/afec265f0a929a55e1d7493eee8c149f46855f5f/pymilvus/milvus_client/async_milvus_client.py#L569) 的 search，再看写入、读取、删除、timeout、filter 和 output_fields；同步对照是 [MilvusClient.search](https://github.com/milvus-io/pymilvus/blob/afec265f0a929a55e1d7493eee8c149f46855f5f/pymilvus/milvus_client/milvus_client.py#L422)。本项目长期记忆采用稠密向量检索，学习与接入围绕这条路径展开。

我们应新增实现既有 VectorPort 的 Milvus 适配器。由可信上下文构造 tenant/user/agent 等 scope 过滤条件，输出 `memory_id + version + model_space` 等候选引用，再回 Remember 读权威正文。过滤表达式不能原样采信调用方；数据库过滤不替代对象授权。

练习固定两租户、同租户两用户，故意保留旧版本向量，并在写入后检查可查询性。即使 Milvus 返回成功，也不能直接宣称“业务事实、任务、向量、事件”跨库原子提交；仍由现有事实库、投影任务、Outbox 和版本屏障推进一致性。

若先过滤后剩余候选不足，要按截止时间和有界预算决定补取或返回较少内容，而不是放宽权限或接纳旧版本凑满五条。SDK 与服务端兼容版本需在正式接入时另行锁定；本次只核对源码，并未运行 Milvus。

### TEI：把 Embedding 独立部署

官方 [README](https://github.com/huggingface/text-embeddings-inference/blob/29ccc53ba56c9b4f4de8f19a14858d527fab680d/README.md) 描述动态 token 批处理、推理服务接口及 OpenTelemetry 能力；源码可从 [router/src/lib.rs](https://github.com/huggingface/text-embeddings-inference/blob/29ccc53ba56c9b4f4de8f19a14858d527fab680d/router/src/lib.rs) 与 [core/src/queue.rs](https://github.com/huggingface/text-embeddings-inference/blob/29ccc53ba56c9b4f4de8f19a14858d527fab680d/core/src/queue.rs) 看请求组织和批处理队列。

Azure/AKS 阶段，若多个服务需要共享推理或单独分配 CPU/GPU，可以通过 EmbeddingPort 新增远程适配器。TEI 并不是 AKS 必须组件；当前单机可继续原生调用。先验证选定模型、硬件、镜像是否兼容，不假定现有 BGE 绑定可原样迁移。

服务化后要验证超时、连接中断、批处理排队和 trace_id 传播；模型服务的 Trace 不能替代我们的 Task、投影状态或业务审计。用户已暂缓指标达标工作，本轮无需据此建设新监控平台。

## 8. 推荐学习和落地顺序

| 顺序 | 具体动作 | 完成后应能解释 |
|---|---|---|
| 1 | 对照本地 native/p3 与 FastEmbed，跟一条 Query 和一条 Passage | 文本怎么变成向量，空间由哪些条件定义，哪里会超时或失败 |
| 2 | 读 LlamaIndex schema → retriever → fusion → postprocessor，跑两路手工候选实验 | 检索、加载、融合、资格核验和装配如何分离 |
| 3 | 看 Haystack 的组件连接与异步执行，画我们的阶段输入输出表 | 每个负责人交付什么，错误和部分输出怎么传递 |
| 4 | 单独验证 Sentence Transformers CrossEncoder | 重排序放在哪，输入是什么，失败怎么处理，是否值得增加依赖 |
| 5 | 实现/验证 PyMilvus 的 VectorPort 适配 | 租户过滤、空间绑定、旧向量隔离与投影恢复如何共同成立 |
| 6 | 有独立推理部署需求后，再验证 TEI | 服务化新增了哪些网络与排队失败，如何关联原请求 |

每一步只产出一张调用链、一张采用/自研边界表和一组可解释的输入输出。当前阶段先验证功能、边界和故障行为，不新增生产性能指标门槛。

这些框架之外仍由我们负责：可信身份、记忆版本、来源真实性、纠错/删除屏障、最终资格核验、ContextPack 语义、访问事件事务、持久任务与恢复。这些恰好决定系统是否可靠，不能留给任意通用检索框架隐式处理。

## 9. 版本与证据边界

本次核实官方仓库和下表快照中的具体文件/符号，并对照本地代码；没有安装或跑通全部框架，也没有做横向质量/性能测试。下表是**研读快照，不是建议直接安装的发布版本**。正式接入应选择兼容的稳定发布版本，锁定依赖并重跑契约与故障测试。

| 官方仓库 | 本次 commit（短号） | 仓库许可证 |
|---|---|---|
| [run-llama/llama_index](https://github.com/run-llama/llama_index) | `f475afd8a9bb` | MIT |
| [qdrant/fastembed](https://github.com/qdrant/fastembed) | `cc4d101828eb` | Apache-2.0 |
| [huggingface/sentence-transformers](https://github.com/huggingface/sentence-transformers) | `f3864a53cfaf` | Apache-2.0 |
| [deepset-ai/haystack](https://github.com/deepset-ai/haystack) | `b717d00654af` | Apache-2.0 |
| [milvus-io/pymilvus](https://github.com/milvus-io/pymilvus) | `afec265f0a92` | Apache-2.0 |
| [huggingface/text-embeddings-inference](https://github.com/huggingface/text-embeddings-inference) | `29ccc53ba56c` | Apache-2.0 |

许可证列依据官方仓库元数据；模型权重与所选依赖的使用条件需另看对应发布说明。读取过的源码快照与原始核查记录保存在助手工作区，不混入本交付目录。
