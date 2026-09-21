# Recall：从找到文件到读通一次检索

适用对象：有后端经验、第一次读LlamaIndex的Recall负责人。先读通入口、输入输出与调用关系，再接真实Embedding/Milvus。本文的源码行号对应已下载的GitHub快照 `c60937d3099b89e66ff9040f1c45030bc4e407e9`；运行例子安装的是 `llama-index-core==0.14.24`。二者分别记录，不把仓库main和PyPI发行包当成同一版本。

## 1. 你要找的文件在这里

LlamaIndex是多包仓库：`llama-index-core`是Python项目目录，里面的`llama_index/core`才是import对应的包目录。Milvus位于另一个integration项目，不在core的retriever目录里。

```text
llama_index/                          ← GitHub仓库根目录
├─ llama-index-core/
│  └─ llama_index/core/
│     ├─ schema.py                   ← QueryBundle / TextNode / NodeWithScore
│     ├─ base/base_retriever.py      ← BaseRetriever
│     ├─ indices/vector_store/
│     │  └─ retrievers/retriever.py  ← VectorIndexRetriever
│     ├─ vector_stores/types.py      ← VectorStoreQuery / Result / Filters
│     ├─ retrievers/fusion_retriever.py ← QueryFusionRetriever
│     ├─ postprocessor/types.py      ← BaseNodePostprocessor
│     └─ query_engine/retriever_query_engine.py ← 检索、后处理与回答生成的装配
└─ llama-index-integrations/vector_stores/
   └─ llama-index-vector-stores-milvus/
      └─ llama_index/vector_stores/milvus/base.py ← Milvus适配实现
```

以上文件均已下载，可以直接点击下表阅读。副本是源码阅读材料，不是完整可独立安装的仓库；不要单独运行下载目录里的`retriever.py`。

| 阅读次序 | 本地文件与关键位置 | 先看什么 |
|---|---|---|
| 0 | [schema.py：QueryBundle](../sources/llamaindex/llama-index-core/llama_index/core/schema.py#L1449) | 查询如何表示；同文件1035行NodeWithScore、765行TextNode |
| 1 | [base/base_retriever.py](../sources/llamaindex/llama-index-core/llama_index/core/base/base_retriever.py#L192) | retrieve → _retrieve；然后看aretrieve |
| 2 | [indices/vector_store/retrievers/retriever.py](../sources/llamaindex/llama-index-core/llama_index/core/indices/vector_store/retrievers/retriever.py#L104) | 向量查询、结果补正文、返回节点 |
| 2a | [vector_stores/types.py](../sources/llamaindex/llama-index-core/llama_index/core/vector_stores/types.py#L240) | VectorStoreQuery；37行VectorStoreQueryResult |
| 3 | [retrievers/fusion_retriever.py](../sources/llamaindex/llama-index-core/llama_index/core/retrievers/fusion_retriever.py#L276) | 外层编排；113行RRF实现 |
| 4 | [postprocessor/types.py](../sources/llamaindex/llama-index-core/llama_index/core/postprocessor/types.py#L35) | postprocess_nodes → _postprocess_nodes |
| 5 | [query_engine/retriever_query_engine.py](../sources/llamaindex/llama-index-core/llama_index/core/query_engine/retriever_query_engine.py#L142) | 谁调用后处理，何时开始生成回答 |
| 6 | [Milvus集成的base.py](../sources/llamaindex/llama-index-integrations/vector_stores/llama-index-vector-stores-milvus/llama_index/vector_stores/milvus/base.py#L814) | 最后追到数据库SDK查询 |

本地编辑器如不识别`#L`，用“转到行”输入表内行号。GitHub页面按`t`搜完整文件路径；进入文件后按Ctrl+F搜类名或方法名。不要搜中文“后处理接口”：实际类名是`BaseNodePostprocessor`。

## 2. 用同一个问题带着数据读

查询是：“我出差时喜欢喝什么咖啡？”；当前事实是m_coffee@2=“我现在喜欢无糖咖啡”，旧版本m_coffee@1仍留在向量库里。

先掌握四种数据，不必通读整个schema.py：

| 数据 | 含义 | 在我们的项目里不代表什么 |
|---|---|---|
| QueryBundle | 查询文本及可选查询向量等信息 | 不是可信身份，不自带我们的权限/截止约束 |
| TextNode | 一块文本、节点ID、metadata等 | 不是完整的Memory生命周期对象 |
| NodeWithScore | 节点和检索分数 | 分数高不代表授权、未删除或当前版本 |
| VectorStoreQueryResult | 向量库返回的nodes/ids/similarities等 | 不能直接当最终ContextPack |

注意模型：一个Memory可能分为多个Node；Node的ID/hash不自动等于我们的memory_id+version。metadata中写了tenant_id也只是在存数据，必须由服务端实施授权和过滤。

## 3. 第一轮：只读BaseRetriever.retrieve

在base_retriever.py的192行开始，按调用执行顺序读：

```text
retrieve("我喜欢什么咖啡？")
  → 字符串转成QueryBundle
  → 开启框架的检索事件/回调
  → self._retrieve(query_bundle)
  → _handle_recursive_retrieval(...)
  → 结束检索事件
  → 返回list[NodeWithScore]
```

第一轮只需要看懂：公开入口`retrieve`负责通用包装；子类实现`_retrieve`，决定具体怎么找候选。已有Java后端经验可以把它理解为模板方法。

本轮先略读PromptMixin、dispatcher和递归IndexNode细节，确认普通TextNode会如何通过即可。框架自身的回调不等于已经配置Azure监测，也不是我们持久化的业务访问事件。

阅读后自己回答：

1. 传入str以后，哪个地方转成QueryBundle？
2. BaseRetriever在哪里决定调用Milvus？答案：它不决定，由具体实现或其适配器负责。
3. 如果`_retrieve`抛异常，会不会自动变成空结果？不要假设会，必须沿异常传播检查。
4. `aretrieve`是否保证底层非阻塞？不保证；这个快照的默认`_aretrieve`直接调用同步`_retrieve`，子类应提供真正的异步I/O实现。

## 4. 第二轮：追VectorIndexRetriever的六个方法

按下面顺序阅读同一个retriever.py，不要一开始逐行研究构造器所有参数：

```text
_retrieve                         第104行：是否需要计算查询向量
  → _get_nodes_with_embeddings     第227行
      → _build_vector_store_query 第130行：top_k、filters、query embedding
      → self._vector_store.query  第231行：调用向量库适配器
      → _determine_nodes_to_fetch 第146行：哪些节点要补读正文
      → self._docstore.get_nodes  第236行：必要时读取正文
      → _convert_nodes_to_scored_nodes 第212行
```

输入可概括为QueryBundle，输出为list[NodeWithScore]。具体的VectorStoreQuery字段在vector_stores/types.py中定义。

这一步要观察两个分支：向量库可能直接返回包含文本的nodes，也可能主要返回ids，需要通过索引映射和docstore补读。**因此不能把“每次检索都一定回SQLite补正文”说成框架默认行为。** 我们要求权威正文来自Remember，是P3自己的设计。

打开42行构造器，回头核对四个注入对象/参数：`_vector_store`、`_embed_model`、`_docstore`、`_filters`。它们各自属于哪层，有没有隐式默认值，发生异常时由谁接住？

接着把本项目要求标在边上：

- filters必须由可信上下文追加scope；不能让调用者删除过滤条件。
- 模型空间不是只有512维，还要绑定模型、预处理与版本。
- 返回候选必须保留精确memory_id/version；不能把旧候选补成新正文。
- 通用docstore读取不替代Remember资格校验。

最后才看Milvus integration的814行`query()`，追过滤转换、检索分支与pymilvus调用。直接跳进这个大文件，会把协议翻译和业务编排混在一起。

## 5. 第三轮：Fusion是在外层组合检索器

三个Retriever不是三个顺序执行的固定阶段，它们存在继承与组合关系：

```text
应用调用fusion.retrieve(query)
  → BaseRetriever.retrieve的通用包装
  → QueryFusionRetriever._retrieve
      → WorkingRetriever.retrieve(query)
      → VectorIndexRetriever.retrieve(query)
          → Embedding → VectorStore → 必要时补正文
      → 合并各路结果，按选择的融合模式排序
  → 返回候选
应用再显式执行后处理/自己的流水线
```

从fusion_retriever.py的276行读起，然后看266行同步子查询、249行异步子查询、113行RRF。异步路径还要看`asyncio.gather`的异常传播：不要默认某路失败会被包装成P3的coverage/degraded。

第一遍实验固定`num_queries=1`、`mode=RECIPROCAL_RANK`、`use_async=False`，并显式提供测试LLM。这样不用先理解查询改写、异步调度和模型配置。

这些是该快照的实际机制：

- 构造器的`num_queries`默认4，超过1时生成额外查询。
- 默认融合模式是SIMPLE，不是RRF；需要显式选择。
- 即使num_queries=1，构造器也可能解析LLM默认配置；示例显式给测试LLM，避免依赖环境里的云模型设置。
- RRF实现按每路分数排序，再使用`enumerate`从0开始计名次。
- 合并key是`node.hash`，不是memory_id/version。

例如：working里m_trip排第0，long_term里它排第2。源码得到 `1/60 + 1/62 ≈ 0.0327957`。我们的契约若从第1名计数，则应为 `1/61 + 1/63`。这不是宣布哪一种错误，而是说明复用前必须统一约定。

更关键的是：旧版加糖咖啡和新版无糖咖啡有不同hash，会同时留在融合结果中。**融合处理排名，不负责识别哪个版本有效。** 相同memory的不同chunk也可能有不同hash，直接融合可能给多chunk记忆更多权重；P3需先按同来源memory_id/version规则归并。

## 6. 第四轮：后处理由谁调用

postprocessor/types.py第35行`postprocess_nodes`规范化query参数，再调用子类`_postprocess_nodes`。这说明如何扩展“输入候选→返回候选”的处理器。

单独调用`retriever.retrieve()`不会自动跑你另外定义的后处理器。示例需要显式：

```python
nodes = retriever.retrieve(query)
nodes = processor.postprocess_nodes(nodes, query_str=query)
```

如果使用RetrieverQueryEngine，第142行负责串联postprocessors，第160行`retrieve`调用检索器后执行后处理；第203行`_query`继续调用response synthesizer生成回答。阅读这个文件是为了找到装配位置，不要求P3直接使用完整Query Engine。

后处理里的版本检查可用于学习和早期过滤，但不能替代P3最终事务Guard：核验以后、返回以前仍可能发生删除。正式实现还要在最终提交UoW内再次核对资格、保存Pack和访问事件。

## 7. 现在就能跑的演示

示例：[labs/recall_walkthrough.py](../labs/recall_walkthrough.py)。已在隔离环境的llama-index-core 0.14.24运行。它调用真实的BaseRetriever、VectorIndexRetriever、QueryFusionRetriever和后处理接口，但使用合成候选、MockEmbedding、SimpleVectorStore；没有调用真实模型或Milvus。

在PowerShell执行：

```powershell
Set-Location 'E:/projects/codex/aether/P3_框架研读与工程验证_V1.0'
& '.venv/Scripts/python.exe' -X utf8 -m labs.recall_walkthrough --output artifacts/recall_walkthrough.json
```

观察四段结果：

1. `simple_result`：字符串确实转成QueryBundle进入自定义检索器。
2. `vector_path_result`：真实VectorIndexRetriever走了本地向量适配路径；MockEmbedding的分数没有语义质量意义。
3. `fusion_before_filter`：m_trip合并，但m_coffee的v1和v2同时存在，且v1分数更高。
4. `after_explicit_filter`：手工提供当前版本快照后，只保留v2。

这个示例有意让旧版本先经过融合，以暴露通用框架不会替我们判断有效性。P3正式链路应在读取/排序前尽早校验，并在最终提交时再次校验；不要照搬演示的简化位置。

可做三次小修改加深理解：

- 暂时跳过`postprocess_nodes`，确认旧版本还在。
- 只改变TextNode的id_，不改变文本和metadata，检查hash是否变化；理解节点身份与内容hash不是一回事。
- 把num_queries改成2，例子会主动报错，证明进入了查询生成路径；NoGenerationLLM用于阻止意外模型调用。

不要用删除断言的方式让修改后的例子“通过”；观察错误、解释原因，然后恢复原值。

## 8. 如何打断点

阅读副本在`sources/llamaindex`；实际运行代码在`.venv/Lib/site-packages/llama_index`。**断点必须打在实际import的文件里。** 示例输出`debug_locations`包含当前安装包的绝对路径和行号，避免你在下载副本打断点却始终进不去。

先在示例的`FixedRetriever._retrieve`、`run_demo`中的`working.retrieve`处打断点，查看query_bundle和候选。再按输出路径给BaseRetriever.retrieve、VectorIndexRetriever._retrieve、QueryFusionRetriever._retrieve、BaseNodePostprocessor.postprocess_nodes打断点。

VS Code选择本包`.venv/Scripts/python.exe`为解释器；要进入第三方库，调试配置设置`justMyCode: false`。在调用者处看一次调用栈，比单独从第一行读到底更容易理解职责。

## 9. 分两轮完成，不把所有实验挤在第一天

第一轮（约2～3小时，根据实际进度调整）：数据类型→BaseRetriever→VectorIndexRetriever→Fusion→后处理，跑通上述演示。产出一张调用图，每个节点标输入、输出、依赖以及异常向哪层传播。

第二轮才换真实Embedding/Milvus，执行旧向量残留、同租户跨用户、Milvus中断、固定tokenizer预算和删除竞态实验。这些依赖还没通过，不能把第一轮结果称为真实Recall验收。

读懂的检查题：

1. `_retrieve`是谁调用的，覆写哪个方法能改变检索？
2. QueryFusionRetriever与VectorIndexRetriever是串行关系还是组合关系？
3. 什么条件下会计算Query embedding，什么条件下会补读正文？
4. filters从哪来，它为什么不是授权系统？
5. 相同memory的多chunk与多个版本，hash去重能否正确处理？
6. 为什么“候选融合完成”不等于“ContextPack可以返回”？

先能够结合实际源码回答这六个问题，再做框架接入决定。
