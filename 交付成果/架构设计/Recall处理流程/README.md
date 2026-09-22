# Recall 当前处理流程（draw.io）

依据：2026-09-21 工作区当前新三流程运行代码，主入口是 `recall/basic/service.py` 的 `Recall`。左侧保留当前实际处理链，右侧标出每一步的框架参考、真实调用及项目自研边界。

**可编辑文件：[Recall_当前处理流程.drawio](Recall_当前处理流程.drawio)**。在 draw.io / diagrams.net 中打开后，用底部页签切换四个页面；节点、条件、箭头与文字均为原生可编辑元素。节点的自定义 `source` 数据记录对应源码位置。

| 页面 | 内容 | 预览 |
|---|---|---|
| 01 主流程 | 准入与幂等、并发候选、RRF、资格核验、重排、组包、提交和失败 | [PNG](01_main.png) · [SVG](01_main.svg) |
| 02 候选发现 | Working 与长期来源、Embedding、向量过滤、权威加载、扩查与覆盖状态 | [PNG](02_sources.png) · [SVG](02_sources.svg) |
| 03 重排与结果 | 可选重排及降级；完整记忆预算；available / degraded / empty 与失败条件 | [PNG](03_decision.png) · [SVG](03_decision.svg) |
| 04 历史与追溯 | 原结果重新核验、过期请求终止、阶段记录、trace 与访问事件 | [PNG](04_ops.png) · [SVG](04_ops.svg) |

实线表示处理和返回路径；虚线表示横切关联或异常入口。第 04 页右栏是记录之间的关联关系，不表示日志触发业务事件提交。PNG 是白底阅读预览，SVG 与 draw.io 使用同一份节点和连线数据。

## 阅读时要注意的当前行为

- Working 当前使用字符/双字符哈希特征的本地相似度基线；长期来源调用 EmbeddingPort 和 VectorPort。Native/lexical、Milvus/SQLite 由服务装配配置决定，图中未声称当前已连通真实外部依赖。
- 默认配置的候选上限为每路 20、最终最多 5 条、发现上限 100；请求默认预算 1024 token。部署配置可以覆盖默认值，不能把所有配置都理解为默认。`recall.milvus.json` 已配置 required 重排和 30 秒重排超时，通用默认则关闭重排。
- 先从权威存储加载并筛候选，再做 RRF，随后在重排前执行 final_guard。实际收集的读取记录产生 `read` 事件；进入已提交 ContextPack 的记忆产生 `packed` 事件。
- 重排 fallback 仅允许依赖不可用或超时降级；分数数量错误、非有限数值等合约问题仍失败。普通未分类异常由外层失败处理接收。
- `assemble` 按排序遍历候选：超预算跳过整条，继续尝试后续条目；到条数上限或遍历完毕后再判断结果。图第 03 页将循环体合并表达，不能把一次跳过理解为立即结束整个组包。
- 当前每个 ContextGroup 放一条完整记忆，未自动形成多成员冲突组。该能力不能画成现有已完成步骤。
- 已完成请求复用原包之前重新核验当前权限和记忆资格；原包失效就拒绝，不直接交付旧正文。
- 当前过期处理将 accepted/running 且到期的请求标记为 failed。没有自动重新执行整条 Recall，也没有从任意历史节点恢复执行的机制。
- 本地日志与 span 落在 SQLite `node_logs`，按授权范围关联查询；它不是已经接通的 OpenTelemetry Collector 或 Azure Monitor。恢复依据仍是持久业务状态，不能只凭一条 trace 推断业务成功。

## 各步骤参考了什么

图中每页右侧新增技术来源说明卡，对应左侧同名步骤。绿色表示存在真实库调用但受配置控制；紫色表示设计或概念参考；黄色表示设计模式；灰色表示项目自研及其边界。它们不改变原有执行顺序，也不证明当前机器已连通外部依赖。

### 01 主流程

| 对应步骤 | 参考 / 调用及本项目职责 |
|---|---|
| 01 / 02 / 06 · 准入、请求记录、资格核验 | 项目自研：Recall.recall / Remember.final_guard<br>当前身份、精确版本、删除与来源资格由 P3 判断<br>LangMem 不负责 Recall 授权或有效性<br>SQLite 直接用于持久化；规则由项目实现 |
| 03 / 04 · 来源选择与并发候选 | 设计参考：LlamaIndex · BaseRetriever.aretrieve<br>QueryFusionRetriever._run_async_queries<br>参考：统一候选接口 → 多路检索 → 汇总<br>本地：Sources.discover + Recall.retrieve<br>来源选择、覆盖状态、统一截止时间由 P3 实现 |
| 05 · RRF 融合 | 设计参考：LlamaIndex · QueryFusionRetriever<br>._reciprocal_rerank_fusion<br>参考：每路按名次贡献倒数分，再合并排序<br>本地 fuse：完整 MemoryRef 去重，名次从 1 开始<br>研读版按节点 hash、名次从 0；未直接调用该类 |
| 07 · 可选重排 | 直接调用：Sentence Transformers<br>CrossEncoder.predict（配置启用时）<br>问题与每条正文组成文本对，获得相关性分数<br>P3 自行排序并控制超时、并发与降级<br>BaseNodePostprocessor 仅作为后处理接口参考 |
| 08 · 计数与 ContextPack | 直接调用：tiktoken.get_encoding → Encoding.encode<br>可选 Hugging Face Tokenizer.encode<br>框架只提供 token 计数；P3 assemble 控制取舍<br>最多条数、整条跳过与 outcome 判断是项目逻辑<br>未调用 LlamaIndex Query Engine 生成回答 |
| 09 / 贯穿节点 · 提交、事件与追溯 | 直接使用 SQLite；借鉴 Transactional Outbox 模式<br>同事务：业务结果 + 待投递事件<br>Trace 参考 OpenTelemetry / W3C 的关联概念<br>当前落地：本地 node_logs 与阶段记录<br>未接入 OTel SDK / Collector 或 Temporal Replay |

### 02 来源发现

| 对应步骤 | 参考 / 调用及本项目职责 |
|---|---|
| 来源选择 / 并发任务 | 设计参考：LlamaIndex · BaseRetriever.aretrieve<br>QueryFusionRetriever._run_async_queries<br>参考可组合检索器与异步汇总<br>P3 自定 auto 规则、source_timeout 与 coverage<br>未启用框架的默认多查询生成 |
| Working · 读取与本地排序 | 项目自研：Sources.working<br>LexicalEmbedding.features → 特征点积 → Top K<br>字符 / 双字符哈希特征，不是 BGE 模型<br>不调用 LlamaIndex，也不建设 BM25 混合索引 |
| 长期 · Query 编码（ONNX 后端启用时） | 直接调用：FastEmbed · TextEmbedding.query_embed<br>Remember 正文编码对应 passage_embed<br>FastEmbed 内部：分词 → ONNX 图推理 → 向量后处理<br>底层直接使用 ONNX Runtime · InferenceSession.run<br>本地入口：FastEmbedOnnxBackend.embed<br>BGE 是可配置模型权重，不是编排框架 |
| 长期 · 编码结果绑定核验 | 项目自研：Sources.long_term<br>检查 operation_id / input_hash / model_space<br>检查维度、数量与有限数值<br>编码成功不等于结果可以安全用于当前请求<br>FastEmbed / ONNX Runtime 不提供这层业务约束 |
| 长期 · 向量库查询（Milvus 配置启用时） | 直接调用：pymilvus · MilvusClient.search<br>本地：MilvusVectors.search → call("search")<br>scope 与 model_space 过滤；IP 相似度；Top K<br>未使用 LlamaIndex 的 MilvusVectorStore 适配器<br>SQLiteVectors 是另一种本地装配选择 |
| 长期 · 候选引用 → 权威正文 → 资格 | 设计参考：LlamaIndex · VectorIndexRetriever<br>._aretrieve / _aget_nodes_with_embeddings<br>参考：向量检索与正文加载分离<br>P3：Remember.load 读取精确版本的权威正文<br>READY、scope、内容 hash 与版本核验由 P3 补充 |
| 扩查与来源覆盖汇总 | 项目自研：Sources.long_term / Recall.retrieve<br>过滤后不足时扩大 limit，最多 max_discovery<br>记录 complete / partial / unavailable<br>不把依赖中断冒充正常空结果<br>属于 P3 对检索框架的工程与产品约束 |

### 03 重排与结果

| 对应步骤 | 参考 / 调用及本项目职责 |
|---|---|
| 重排入口 · 可替换后处理接口 | 设计参考：LlamaIndex · BaseNodePostprocessor<br>postprocess_nodes / apostprocess_nodes<br>参考“候选 → 可插拔后处理 → 新候选”的分层<br>本地使用自己的 Reranker 接口与 Recall.rerank<br>不实例化 LlamaIndex 后处理器 |
| 模型评分 · CrossEncoder | 直接调用：sentence_transformers.CrossEncoder<br>.predict([(query, document), ...])<br>联合编码文本对 → 模型前向 → 每对一个分数<br>predict 返回分数，P3 负责稳定排序与结果检查<br>当前并未调用 CrossEncoder.rank |
| 跳过、超时与失败降级 | 项目自研：Recall.rerank / CrossEncoderReranker<br>通用配置默认 disabled；部署可启用 required/fallback<br>fallback 仅接受超时或依赖不可用<br>合约错误仍失败；真实推理结束后才释放并发槽<br>这些保证不是 CrossEncoder.predict 自动提供的 |
| 长度预算 · tokenizer | 直接调用：tiktoken.get_encoding("o200k_base")<br>Encoding.encode → token 数<br>可选：Hugging Face Tokenizer.encode<br>本地 ModelTokenizer.count 统一封装<br>对追加序号、正文、换行后的完整候选上下文计数 |
| 组包 · 整条记忆取舍 | 项目自研：components.assemble<br>超预算跳过整条，再尝试后续候选<br>当前每个 ContextGroup 只有一条记忆<br>没有调用 Query Engine 的回答生成 / Synthesizer<br>自动多成员冲突组仍不是当前已实现能力 |
| 结果判定 · available / degraded / empty / 错误 | 项目自研：Recall.retrieve<br>结合实际内容、来源覆盖与降级原因判定<br>无内容且来源缺失 → 依赖失败<br>来源完整但有候选且放不下 → 预算不足<br>通用检索框架的空列表不能代替上述产品结论 |

### 04 历史与追溯

| 对应步骤 | 参考 / 调用及本项目职责 |
|---|---|
| 历史结果 · 当前授权与有效性 | 项目自研：Recall.result / Remember.final_guard<br>重新核验当前权限、版本、生命周期与来源<br>失效结果拒绝返回；不能把缓存当作当前事实<br>OWASP 授权原则可作设计依据，不是已接入的库 |
| 过期处理 · 持久状态扫描 | 项目自研：ThreeFlows.tick → Recall.recover_expired<br>SQLite 扫描到期的 accepted / running 请求并终止<br>Temporal 仅为学习对象，未提供 Workflow Replay<br>controller-runtime 参考属于 Operate，不执行此流程<br>本步骤不自动从中断节点续跑 Recall |
| Trace / 节点日志 · 参考概念与规范 | 概念参考：OpenTelemetry 的 Trace / Span<br>规范参考：W3C Trace Context 的关联标识<br>本地实现：observed / Telemetry → node_logs<br>记录开始、返回、失败、取消及安全摘要<br>未接 OTel SDK / OTLP，也未证明跨 HTTP 传播 |
| 阶段与诊断 · 持久事实 | 直接使用 SQLite；阶段推进与查询为项目自研<br>Recall.stage 更新请求 / recall_stages / diagnostics<br>日志帮助解释执行，业务表确定当前结果<br>不能仅因 span 显示成功就判定业务已提交 |
| 访问事件 · Transactional Outbox | 借鉴成熟模式，不是某个框架或正式协议标准<br>本地 Recall.access → 事务内追加事件<br>分发器投递，Operate 按事件 ID 去重消费<br>read 与 packed 分别表示读取与最终采用<br>可靠交接依赖持久事实与幂等，不依赖采样日志 |

研读入口：[技术选型与框架研读指南](../../ADR版本/P3_技术选型与框架研读指南_V1.0.md)；[第三方源码流程拆解](../第三方源码流程拆解/README.md)。上游方法名对应本项目已保存的研读源码，不代表与未来上游版本始终一致。

## 图中节点与源码对照

| 源码 | 关键符号 | 图中事实 |
|---|---|---|
| [recall/basic/service.py](../../../AgentJYS-main/src/aether_agent_memory/recall/basic/service.py) | `Recall.recall / retrieve / rerank / result / recover_expired / stage / access` | 请求幂等、来源编排、资格核验、重排、结果判定、事务提交与过期处理 |
| [recall/basic/retrievers.py](../../../AgentJYS-main/src/aether_agent_memory/recall/basic/retrievers.py) | `Sources.discover / working / long_term` | Working 本地排序；长期 Query 编码、向量查询、精确正文加载和限量扩查 |
| [recall/basic/components.py](../../../AgentJYS-main/src/aether_agent_memory/recall/basic/components.py) | `fuse / assemble` | 按 MemoryRef 的 RRF；完整单条记忆组包与预算 |
| [recall/basic/adapters.py](../../../AgentJYS-main/src/aether_agent_memory/recall/basic/adapters.py) | `LexicalEmbedding.features / SQLiteVectors` | Working 字符/双字符特征；本地向量适配能力 |
| [recall/basic/config.py](../../../AgentJYS-main/src/aether_agent_memory/recall/basic/config.py) | `RecallSettings` | 默认 20 候选、最多 5 条、发现上限 100、重排默认关闭 |
| [recall/contracts/models.py](../../../AgentJYS-main/src/aether_agent_memory/recall/contracts/models.py) | `RecallRequest / ContextPack / Coverage` | 默认 1024 token；结果与来源覆盖状态 |
| [remember/basic/service.py](../../../AgentJYS-main/src/aether_agent_memory/remember/basic/service.py) | `Remember.final_guard / load / working` | 权威版本、授权、生命周期与来源有效性检查 |
| [runtime/flows/host.py](../../../AgentJYS-main/src/aether_agent_memory/runtime/flows/host.py) | `ThreeFlows.__init__ / tick` | 装配 Embedding、向量、重排实现；周期调用过期处理和事件分发 |
| [runtime/foundation/identity.py](../../../AgentJYS-main/src/aether_agent_memory/runtime/foundation/identity.py) | `Identity.revalidate / authorize / permits` | 当前主体、对象权限与截止时间 |
| [runtime/foundation/telemetry.py](../../../AgentJYS-main/src/aether_agent_memory/runtime/foundation/telemetry.py) | `observed / Telemetry` | 本地结构化日志与 span 关联；不写正文凭证 |
| [recall/embedding/backends.py](../../../AgentJYS-main/src/aether_agent_memory/recall/embedding/backends.py) | `FastEmbedOnnxBackend.embed` | 按用途直接调用 query_embed / passage_embed；ONNX Runtime 负责图推理 |
| [recall/basic/milvus.py](../../../AgentJYS-main/src/aether_agent_memory/recall/basic/milvus.py) | `MilvusVectors.search / call` | 配置启用时直接调用 pymilvus MilvusClient.search |
| [recall/basic/reranking.py](../../../AgentJYS-main/src/aether_agent_memory/recall/basic/reranking.py) | `CrossEncoderReranker.compute` | 直接调用 CrossEncoder.predict；不调用 rank |
| [recall/basic/tokenization.py](../../../AgentJYS-main/src/aether_agent_memory/recall/basic/tokenization.py) | `ModelTokenizer.count` | tiktoken 或可选 tokenizers 负责计数 |

## 本次校验

4 页、110 个业务/说明节点、85 条连线（原处理链不变，新增技术来源说明卡）。draw.io XML 可解析，连线端点有效；节点未超画布；四页中文预览未发现文本超框，已目视检查布局和主要分支。预览由同源 SVG 渲染，未使用 draw.io 桌面软件导出，因此不将其表述为桌面应用导入测试。

本次仅制作流程图，未修改业务代码，也未运行模型、Milvus 或恢复故障演练。图表示当前代码结构与分支，不构成 PRD 全功能或真实运行验收结论。
