# Recall：一个问题怎样穿过检索器、向量库和融合器

[返回流程总入口](README.md)

本章跟随异步调用链，采用“一个原查询 + 多个来源检索器 + 稠密向量检索 + RRF”的研读配置。Working 与长期来源是 P3 的业务划分；这里借鉴 LlamaIndex 的组合接口，不声称框架自带 P3 Working 检索、授权或版本规则。

## 先看这一条执行路径

```text
调用方 → BaseRetriever.aretrieve（实例为 QueryFusionRetriever）
  → QueryBundle → QueryFusionRetriever._aretrieve
  → _run_async_queries → 每个来源的 BaseRetriever.aretrieve
     长期来源：VectorIndexRetriever._aretrieve
       → 需要时调用 Embedding 接口（编码机制见第 02 章）
       → _aget_nodes_with_embeddings → _build_vector_store_query
       → MilvusVectorStore.aquery → 过滤条件 → _async_default_search
       → Milvus 客户端 → 命中解析 → 必要时补正文 → NodeWithScore
  ← gather 收集各路结果 → RRF 融合
  ← BaseRetriever 处理递归引用/去重 → 返回候选
可选后处理容器：QueryEngine.aretrieve → 顺序后处理
P3：当前资格/来源/冲突核验 → 重排与预算 → ContextPack
```

贯穿示例：问题为“最终批准预算是多少”。Working 来源给出会议上下文，长期来源命中预算记忆。若命中旧版二十万元，框架可能仍把它排得很高；P3 必须按权威版本和删除状态排除它。两路业务来源不等于混合索引。

以下代码从已核对版本逐段摘录；省略导入、英文说明文档及英文整行注释，保留执行语句、字符串与中文研读注释。标为“片段”的代码保留原方法的局部上下文，不能当成独立函数运行。

## 按执行顺序展开

- [步骤 01：装配融合器：明确来源与查询数量](#s01)
- [步骤 02：进入公共接口，开始一次检索](#s02)
- [步骤 03：展开：查询对象携带哪些输入](#s03)
- [步骤 04：融合器先准备查询，暂时还没有融合](#s04)
- [步骤 05：展开：一个查询分别交给各来源](#s05)
- [步骤 06：长期来源：需要时生成查询向量](#s06)
- [步骤 07：展开：判定当前检索模式是否需要向量](#s07)
- [步骤 08：进入查询编排：先构造向量请求](#s08)
- [步骤 09：展开：把 TopK、范围和向量一起传下去](#s09)
- [步骤 10：Milvus 入口：选择结构化过滤还是原始表达式](#s10)
- [步骤 11：展开：范围条件与返回字段如何合并](#s11)
- [步骤 12：展开：把通用过滤转换为 Milvus 格式](#s12)
- [步骤 13：回到 Milvus 入口：执行默认稠密分支](#s13)
- [步骤 14：实际发出向量查询](#s14)
- [步骤 15：解析数据库命中为节点、分数和 ID](#s15)
- [步骤 16：展开：从命中里取稳定引用](#s16)
- [步骤 17：回到检索器：判断是否还需要补正文](#s17)
- [步骤 18：展开：哪些引用需要补读](#s18)
- [步骤 19：展开：补读后恢复原始命中顺序](#s19)
- [步骤 20：展开：把同一位置的正文和分数组合](#s20)
- [步骤 21：公共入口收尾：展开递归节点并去重](#s21)
- [步骤 22：可选分支：引用对象可能是另一个检索器或查询引擎](#s22)
- [步骤 23：各路都返回后：RRF 按名次融合](#s23)
- [步骤 24：展开：RRF 的节点 hash 从哪里来](#s24)
- [步骤 25：组合容器：只检索也可以使用 QueryEngine](#s25)
- [步骤 26：展开：后处理器前后串联](#s26)
- [步骤 27：展开：后处理统一输入](#s27)
- [步骤 28：展开：默认异步后处理怎样兼容同步实现](#s28)
- [步骤 29：对照：完整 QueryEngine 在哪里开始生成回答](#s29)

<a id="s01"></a>

### 01　装配融合器：明确来源与查询数量

**当前执行位置：** `QueryFusionRetriever.__init__`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/retrievers/fusion_retriever.py)，注释版第 41—79 行。

**收到什么：** 检索器列表、融合策略、num_queries、TopK 与模型配置。

**这一段怎么处理：** 保存来源与策略，归一化权重，解析可选查询生成依赖。

```python
def __init__(
    self,
    retrievers: List[BaseRetriever],
    llm: Optional[LLMType] = None,
    query_gen_prompt: Optional[str] = None,
    mode: FUSION_MODES = FUSION_MODES.SIMPLE,
    similarity_top_k: int = DEFAULT_SIMILARITY_TOP_K,
    num_queries: int = 4,
    use_async: bool = True,
    verbose: bool = False,
    callback_manager: Optional[CallbackManager] = None,
    objects: Optional[List[IndexNode]] = None,
    object_map: Optional[dict] = None,
    retriever_weights: Optional[List[float]] = None,
) -> None:
    # 【中文研读】处理流程：默认 num_queries=4，权重缺省均分，否则按总和归一化；即便只做检索，也要留意全局 LLM 的依赖解析。
    self.num_queries = num_queries
    self.query_gen_prompt = query_gen_prompt or QUERY_GEN_PROMPT
    self.similarity_top_k = similarity_top_k
    self.mode = mode
    self.use_async = use_async

    self._retrievers = retrievers
    # 【中文研读】没有配置权重时各检索器均分；自定义权重分支仅做归一化，调用方仍需约束非空列表和合法总权重。
    if retriever_weights is None:
        self._retriever_weights = [1.0 / len(retrievers)] * len(retrievers)
    else:
        total_weight = sum(retriever_weights)
        self._retriever_weights = [w / total_weight for w in retriever_weights]
    self._llm = (
        resolve_llm(llm, callback_manager=callback_manager) if llm else Settings.llm
    )
    super().__init__(
        callback_manager=callback_manager,
        object_map=object_map,
        objects=objects,
        verbose=verbose,
    )
```

**执行后得到什么：** 一个对外仍提供 retrieve/aretrieve 的检索器。

**接下来到哪里：** 一次查询从步骤 02 的基类公共入口进入。

**失败与 P3 责任：** 本章 num_queries=1，避免查询改写；构造器仍解析 LLM 依赖，不能据此宣称完全无模型配置要求。

<a id="s02"></a>

### 02　进入公共接口，开始一次检索

**当前执行位置：** `BaseRetriever.aretrieve`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/base/base_retriever.py)，注释版第 286—320 行。

**收到什么：** 原查询字符串或 QueryBundle。

**这一段怎么处理：** 补回调，把字符串封装，记录开始；await 当前实例的 _aretrieve，结果返回后再处理递归引用并结束事件。

```python
@dispatcher.span
async def aretrieve(self, str_or_query_bundle: QueryType) -> List[NodeWithScore]:
    # 【中文研读】处理流程：包装输入和观测事件与同步入口一致，具体检索与引用展开分别 await。
    self._check_callback_manager()

    dispatcher.event(
        RetrievalStartEvent(
            str_or_query_bundle=str_or_query_bundle,
        )
    )
    if isinstance(str_or_query_bundle, str):
        query_bundle = QueryBundle(str_or_query_bundle)
    else:
        query_bundle = str_or_query_bundle
    with self.callback_manager.as_trace("query"):
        with self.callback_manager.event(
            CBEventType.RETRIEVE,
            payload={EventPayload.QUERY_STR: query_bundle.query_str},
        ) as retrieve_event:
            # 【中文研读】等待子类候选发现；下一步还要展开节点引用，所以这里不是最终交付点。
            nodes = await self._aretrieve(query_bundle=query_bundle)
            nodes = await self._ahandle_recursive_retrieval(
                query_bundle=query_bundle, nodes=nodes
            )
            # 【中文研读】记录这次检索返回的候选；框架回调不是业务事件的事务提交。
            retrieve_event.on_end(
                payload={EventPayload.NODES: nodes},
            )
    dispatcher.event(
        RetrievalEndEvent(
            str_or_query_bundle=str_or_query_bundle,
            nodes=nodes,
        )
    )
    return nodes
```

**执行后得到什么：** 最终候选列表，但此时先进入子类执行。

**接下来到哪里：** QueryBundle 看步骤 03；子类为融合器，步骤 04 继续。

**失败与 P3 责任：** 这是动态分派：方法定义在基类，self 实际是融合器；它没有替用户校验权限。

<a id="s03"></a>

### 03　展开：查询对象携带哪些输入

**当前执行位置：** `QueryBundle`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/schema.py)，注释版第 1949—2006 行。

**收到什么：** 用户文本、可选 embedding 和自定义编码文本。

**这一段怎么处理：** 把查询文本与编码输入放在同一对象；embedding_strs 决定供模型编码的文字。

```python
@dataclass
class QueryBundle(DataClassJsonMixin):

    # 【中文研读】用户查询文本，保留原问题供检索和后处理参考。
    query_str: str
    image_path: Optional[str] = None
    # 【中文研读】可用与原问题不同的文本来生成查询向量；需明确改写与原请求之间的关系。
    custom_embedding_strs: Optional[List[str]] = None
    # 【中文研读】预计算查询向量；使用方还需校验模型空间，字段本身只有数值列表。
    embedding: Optional[List[float]] = None

    # 【中文研读】方法职责：决定哪些文本送入 Embedding
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：List[str]。结果的业务含义与失败分支见下面处理流程。
    @property
    def embedding_strs(self) -> List[str]:
        # 【中文研读】处理流程：有 custom_embedding_strs 就使用它，否则非空查询包装成单元素列表，空查询返回空列表。
        # 【中文研读】没有自定义编码文本时使用原查询；自定义列表即使为空也按原样采用。
        if self.custom_embedding_strs is None:
            if len(self.query_str) == 0:
                return []
            return [self.query_str]
        else:
            return self.custom_embedding_strs

    # 【中文研读】方法职责：把可选查询图片路径包装成列表
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：List[ImageType]。结果的业务含义与失败分支见下面处理流程。
    @property
    def embedding_image(self) -> List[ImageType]:
        # 【中文研读】处理流程：无路径返回空，有路径返回单元素列表。
        if self.image_path is None:
            return []
        return [self.image_path]

    # 【中文研读】方法职责：读取并返回 self.query_str；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def __str__(self) -> str:
        # 【中文研读】处理流程：读取并返回 self.query_str；此入口不发起检索或存储写入。
        return self.query_str
```

**执行后得到什么：** 传给检索器的统一 QueryBundle。

**接下来到哪里：** 回到公共接口，再进入融合器。

**失败与 P3 责任：** 对象可以携带向量不表示向量模型空间已验证；P3 的可信上下文应独立受控。

<a id="s04"></a>

### 04　融合器先准备查询，暂时还没有融合

**当前执行位置：** `QueryFusionRetriever._aretrieve`，方法内部片段。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/retrievers/fusion_retriever.py)，注释版第 370—378 行。

**收到什么：** QueryBundle。

**这一段怎么处理：** 保留原查询；num_queries>1 时才生成变体；随后调用 _run_async_queries。

```python
async def _aretrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
    # 【中文研读】处理流程：可选异步生成查询，然后并发执行所有组合，按融合规则输出 TopK。
    queries: List[QueryBundle] = [query_bundle]
    # 【中文研读】num_queries=1 可跳过查询生成；默认 4 会进入此分支，仍需检查构造器的模型依赖。
    if self.num_queries > 1:
        queries.extend(await self._aget_queries(query_bundle.query_str))

    results = await self._run_async_queries(queries)
```

**执行后得到什么：** 等待所有来源完成的调用。

**接下来到哪里：** 步骤 05 展开来源并发调度；其余融合分支在来源返回后解释。

**失败与 P3 责任：** 本章固定一个查询，查询改写分支不执行；不能把额外模型请求隐含加入主链。

<a id="s05"></a>

### 05　展开：一个查询分别交给各来源

**当前执行位置：** `QueryFusionRetriever._run_async_queries`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/retrievers/fusion_retriever.py)，注释版第 305—322 行。

**收到什么：** 查询列表和来源检索器列表。

**这一段怎么处理：** 建立“查询×来源”协程组合及位置键，gather 后把结果绑定回对应来源。

```python
async def _run_async_queries(
    self, queries: List[QueryBundle]
) -> Dict[Tuple[str, int], List[NodeWithScore]]:
    # 【中文研读】处理流程：先收集协程，gather 并发执行，再按原组合顺序恢复字典；某一路异常并不会自动变成带覆盖状态的降级结果。
    tasks, task_queries = [], []
    for query in queries:
        for i, retriever in enumerate(self._retrievers):
            tasks.append(retriever.aretrieve(query))
            task_queries.append((query.query_str, i))

    # 【中文研读】等待所有并发检索；默认异常传播，不自动给失败来源制造空候选。
    task_results = await asyncio.gather(*tasks)

    results = {}
    for query_tuple, query_result in zip(task_queries, task_results):
        results[query_tuple] = query_result

    return results
```

**执行后得到什么：** 以 (query_str, 来源编号) 为键的候选列表字典。

**接下来到哪里：** 每个来源也先经过 BaseRetriever.aretrieve；长期来源进入步骤 06。

**失败与 P3 责任：** gather 异常不自动转成来源覆盖状态，P3 需要明确区分失败和正常无结果。

<a id="s06"></a>

### 06　长期来源：需要时生成查询向量

**当前执行位置：** `VectorIndexRetriever._aretrieve`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/indices/vector_store/retrievers/retriever.py)，注释版第 145—159 行。

**收到什么：** 同一 QueryBundle。

**这一段怎么处理：** 取已有 embedding；需要且缺失时调用模型接口；把文本和向量重新包装后继续查询。

```python
@dispatcher.span
async def _aretrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
    # 【中文研读】处理流程：使用异步模型调用，将查询文本和向量重新包装为 QueryBundle 后继续；原包的其他可选字段并未全部复制。
    embedding = query_bundle.embedding
    # 【中文研读】先判定是否需要向量；已有向量可避免重复编码，但此上游入口没有验证 P3 模型空间绑定。
    if self._needs_embedding():
        # 【中文研读】只有向量缺失且存在可编码文本时才调用模型；不要把无文本输入误认为模型服务失败。
        if query_bundle.embedding is None and len(query_bundle.embedding_strs) > 0:
            embed_model = self._embed_model
            embedding = await embed_model.aget_agg_embedding_from_queries(
                query_bundle.embedding_strs
            )
    return await self._aget_nodes_with_embeddings(
        QueryBundle(query_str=query_bundle.query_str, embedding=embedding)
    )
```

**执行后得到什么：** 带查询向量的 QueryBundle。

**接下来到哪里：** 是否需要向量见步骤 07；编码计算展开在第 02 章；随后步骤 08。

**失败与 P3 责任：** 上游异步重包装没有复制所有原字段；P3 请求上下文、模型空间和 deadline 不能因此丢失。

<a id="s07"></a>

### 07　展开：判定当前检索模式是否需要向量

**当前执行位置：** `VectorIndexRetriever._needs_embedding`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/indices/vector_store/retrievers/retriever.py)，注释版第 110—120 行。

**收到什么：** 向量库能力和检索器模式。

**这一段怎么处理：** 依据能力与模式返回布尔值。本章固定 DEFAULT 稠密路径，满足时生成向量。

```python
def _needs_embedding(self) -> bool:
    # 【中文研读】处理流程：同时检查向量库能力与模式，P3 主线使用需要向量的默认路径。
    return (
        self._vector_store.is_embedding_query
        and self._vector_store_query_mode
        not in (
            VectorStoreQueryMode.TEXT_SEARCH,
            VectorStoreQueryMode.SPARSE,
        )
    )
```

**执行后得到什么：** 是否走编码分支。

**接下来到哪里：** 返回上一步；需要编码时走 Embedding 接口，不需要重复编码时复用现有向量。

**失败与 P3 责任：** 这里只解释上游判定函数，不引入其其他查询模式。

<a id="s08"></a>

### 08　进入查询编排：先构造向量请求

**当前执行位置：** `VectorIndexRetriever._aget_nodes_with_embeddings`，方法内部片段。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/indices/vector_store/retrievers/retriever.py)，注释版第 314—321 行。

**收到什么：** 已带向量的 QueryBundle。

**这一段怎么处理：** 调用 _build_vector_store_query，再 await 具体向量库 aquery。

```python
async def _aget_nodes_with_embeddings(
    self, query_bundle_with_embeddings: QueryBundle
) -> List[NodeWithScore]:
    # 【中文研读】处理流程：分别 await 向量库与 DocStore，复用同样的节点替换和分数包装逻辑。
    query = self._build_vector_store_query(query_bundle_with_embeddings)
    # 【中文研读】异步数据库调用边界；异常向外传播，框架不会自动决定 P3 是否允许降级。
    query_result = await self._vector_store.aquery(query, **self._kwargs)
```

**执行后得到什么：** 等待 VectorStoreQueryResult。

**接下来到哪里：** 请求组装看步骤 09；数据库路径从步骤 10 开始。

**失败与 P3 责任：** 调用成功前没有候选结果；不能提前记录召回成功。

<a id="s09"></a>

### 09　展开：把 TopK、范围和向量一起传下去

**当前执行位置：** `VectorIndexRetriever._build_vector_store_query`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/indices/vector_store/retrievers/retriever.py)，注释版第 164—179 行。

**收到什么：** 查询向量以及检索器保存的配置。

**这一段怎么处理：** 组装查询对象；此时不访问网络。

```python
def _build_vector_store_query(
    self, query_bundle_with_embeddings: QueryBundle
) -> VectorStoreQuery:
    # 【中文研读】处理流程：传递向量、TopK、ID 范围和过滤条件；本方法只组装对象，不联网。
    return VectorStoreQuery(
        query_embedding=query_bundle_with_embeddings.embedding,
        similarity_top_k=self._similarity_top_k,
        node_ids=self._node_ids,
        doc_ids=self._doc_ids,
        query_str=query_bundle_with_embeddings.query_str,
        mode=self._vector_store_query_mode,
        alpha=self._alpha,
        filters=self._filters,
        sparse_top_k=self._sparse_top_k,
        hybrid_top_k=self._hybrid_top_k,
    )
```

**执行后得到什么：** VectorStoreQuery。

**接下来到哪里：** 进入 MilvusVectorStore.aquery 的默认稠密路径。

**失败与 P3 责任：** 上游 filters 来自装配；P3 必须强制补入可信 scope，不能把用户提供的 filters 当作已授权范围。

<a id="s10"></a>

### 10　Milvus 入口：选择结构化过滤还是原始表达式

**当前执行位置：** `MilvusVectorStore.aquery`，方法内部片段。[出处](../第三方源码中文注释/llamaindex/llama-index-integrations/vector_stores/llama-index-vector-stores-milvus/llama_index/vector_stores/milvus/base.py)，注释版第 1007—1020 行。

**收到什么：** DEFAULT 模式请求，且已装配异步客户端。

**这一段怎么处理：** 调用过滤准备函数；结构化条件非空时优先采用，原始 string_expr 会被忽略并告警。

```python
filter_string_expr, output_fields = self._prepare_before_search(query, **kwargs)
# 【中文研读】存在原始表达式扩展入口；正式业务应由服务端构造并约束，不能直接接收用户任意表达式。
custom_string_expr = kwargs.pop("string_expr", "")
# 【中文研读】结构化条件优先，有条件时忽略原始表达式并告警；并非自动合并两份表达式。
if len(filter_string_expr) != 0:
    if len(custom_string_expr) != 0:
        logger.warning(
            "string_expr in vector_store_kwargs is ignored because filters are provided."
        )
    string_expr = filter_string_expr
else:
    string_expr = custom_string_expr
```

**执行后得到什么：** string_expr 和 output_fields。

**接下来到哪里：** 步骤 11 展开条件构造，然后进入步骤 13 的默认搜索分支。

**失败与 P3 责任：** 本摘录从模式校验之后开始；默认模式和异步客户端是前置条件，不能绕过。

<a id="s11"></a>

### 11　展开：范围条件与返回字段如何合并

**当前执行位置：** `MilvusVectorStore._prepare_before_search`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-integrations/vector_stores/llama-index-vector-stores-milvus/llama_index/vector_stores/milvus/base.py)，注释版第 1045—1091 行。

**收到什么：** filters、doc_ids、node_ids、output_fields。

**这一段怎么处理：** 转换元数据条件；把文档与节点范围用 AND 合并；必要时补正文返回字段。

```python
def _prepare_before_search(
    self, query: VectorStoreQuery, **kwargs
) -> Tuple[str, List[str]]:
    # 【中文研读】处理流程：元信息过滤、文档/节点 ID 约束用 AND 连接，返回字段不足时补正文键。
    expr = []
    output_fields = ["*"]
    if query.filters is not None or "milvus_scalar_filters" in kwargs:
        expr.append(
            _to_milvus_filter(
                query.filters,
                (
                    kwargs["milvus_scalar_filters"]
                    if "milvus_scalar_filters" in kwargs
                    else None
                ),
            )
        )
    # 【中文研读】将文档范围并入查询条件；原源码使用字符串拼接，正式接入应验证转义与输入范围。
    if query.doc_ids is not None and len(query.doc_ids) != 0:
        expr_list = ['"' + entry + '"' for entry in query.doc_ids]
        expr.append(f"{self.doc_id_field} in [{','.join(expr_list)}]")
    if query.node_ids is not None and len(query.node_ids) != 0:
        expr_list = ['"' + entry + '"' for entry in query.node_ids]
        expr.append(f"{MILVUS_ID_FIELD} in [{','.join(expr_list)}]")
    outputs_limited = False
    if query.output_fields is not None:
        output_fields = query.output_fields
        outputs_limited = True
    elif len(self.output_fields) > 0:
        output_fields = [*self.output_fields]
        outputs_limited = True
    # 【中文研读】限制输出字段时仍保留正文键，否则命中无法还原为可读节点。
    if self.text_key not in output_fields and outputs_limited:
        output_fields.append(self.text_key)
    string_expr = ""
    if len(expr) != 0:
        string_expr = f" and ".join(expr)
    return string_expr, output_fields
```

**执行后得到什么：** 数据库可用的过滤表达式与字段列表。

**接下来到哪里：** 表达式转换助手见步骤 12，随后回到 Milvus 入口。

**失败与 P3 责任：** 查询范围需要安全转义和服务端约束；字符串拼接不能当作自动防越权机制。

<a id="s12"></a>

### 12　展开：把通用过滤转换为 Milvus 格式

**当前执行位置：** `_to_milvus_filter`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-integrations/vector_stores/llama-index-vector-stores-milvus/llama_index/vector_stores/milvus/base.py)，注释版第 79—105 行。

**收到什么：** 通用 MetadataFilters 或 Milvus 标量条件。

**这一段怎么处理：** 委托过滤标准化与表达式转换，产生查询过滤表达式。

```python
def _to_milvus_filter(
    standard_filters: MetadataFilters, scalar_filters: ScalarMetadataFilters = None
) -> str:
    # 【中文研读】处理流程：分别解析，两类都存在时用 AND 连接，按条件数加括号，没有条件返回空字符串。
    standard_filters_list, joined_standard_filters = parse_standard_filters(
        standard_filters
    )
    scalar_filters_list, joined_scalar_filters = parse_scalar_filters(scalar_filters)

    filters = standard_filters_list + scalar_filters_list

    if len(standard_filters_list) > 0 and len(scalar_filters_list) > 0:
        joined_filters = f" {joined_standard_filters} and {joined_scalar_filters} "
        return f"({joined_filters})" if len(filters) > 1 else joined_filters
    elif len(standard_filters_list) > 0 and len(scalar_filters_list) == 0:
        return (
            f"({joined_standard_filters})"
            if len(filters) > 1
            else joined_standard_filters
        )
    elif len(standard_filters_list) == 0 and len(scalar_filters_list) > 0:
        return (
            f"({joined_scalar_filters})" if len(filters) > 1 else joined_scalar_filters
        )
    else:
        return ""
```

**执行后得到什么：** 给上一步拼接使用的条件字符串。

**接下来到哪里：** 返回 _prepare_before_search，随后回到 aquery。

**失败与 P3 责任：** 格式转换不验证业务授权；从可信上下文导出 scope 是 P3 的责任。

<a id="s13"></a>

### 13　回到 Milvus 入口：执行默认稠密分支

**当前执行位置：** `MilvusVectorStore.aquery`，方法内部片段。[出处](../第三方源码中文注释/llamaindex/llama-index-integrations/vector_stores/llama-index-vector-stores-milvus/llama_index/vector_stores/milvus/base.py)，注释版第 1037—1040 行。

**收到什么：** 过滤表达式与查询向量。

**这一段怎么处理：** 当前选择的是原方法 else 中的默认搜索路径；await 返回三组列表，再包装统一结果。

```python
nodes, similarities, ids = await self._async_default_search(
        query, string_expr, output_fields, **kwargs
    )
return VectorStoreQueryResult(nodes=nodes, similarities=similarities, ids=ids)
```

**执行后得到什么：** VectorStoreQueryResult(nodes, similarities, ids)。

**接下来到哪里：** 真正搜索展开在步骤 14，返回解析见步骤 15—16。

**失败与 P3 责任：** 此片段只摘出已确定的默认路径，不代表删除了上游其他分支或改写其算法。

<a id="s14"></a>

### 14　实际发出向量查询

**当前执行位置：** `MilvusVectorStore._async_default_search`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-integrations/vector_stores/llama-index-vector-stores-milvus/llama_index/vector_stores/milvus/base.py)，注释版第 1127—1156 行。

**收到什么：** 集合、单个查询向量、过滤条件、TopK 和返回字段。

**这一段怎么处理：** 检查异步客户端并 await aclient.search；获取一组命中后调用共同解析函数。

```python
async def _async_default_search(
    self,
    query: VectorStoreQuery,
    string_expr: str,
    output_fields: List[str],
    **kwargs,
) -> Tuple[List[BaseNode], List[float], List[str]]:
    # 【中文研读】处理流程：await 网络请求，然后与同步路径共用命中解析。
    assert self._async_milvusclient is not None, (
        "Async Client should be non-null to perform async operations. Pass `use_async_client = True` to the constructor to instantiate an async client"
    )
    res = await self.aclient.search(
        collection_name=self.collection_name,
        data=[query.query_embedding],
        filter=string_expr,
        limit=query.similarity_top_k,
        output_fields=output_fields,
        search_params=kwargs.get("milvus_search_config", self.search_config),
        anns_field=self.embedding_field,
        partition_names=kwargs.get("milvus_partition_names"),
    )
    logger.debug(
        f"Successfully searched embedding in collection: {self.collection_name}"
        f" Num Results: {len(res[0])}"
    )
    nodes, similarities, ids = self._parse_from_milvus_results(res)
    return nodes, similarities, ids
```

**执行后得到什么：** 来自数据库的原始 hit 集合。

**接下来到哪里：** 步骤 15 解析 hit。

**失败与 P3 责任：** 向量库中旧引用仍可能命中；网络失败也不会自动变成允许降级，需要 P3 来源策略判断。

<a id="s15"></a>

### 15　解析数据库命中为节点、分数和 ID

**当前执行位置：** `MilvusVectorStore._parse_from_milvus_results`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-integrations/vector_stores/llama-index-vector-stores-milvus/llama_index/vector_stores/milvus/base.py)，注释版第 1702—1742 行。

**收到什么：** 数据库返回的 hit 列表。

**这一段怎么处理：** 从 hit 取分数和标识，恢复节点内容，保持三份列表位置一致。

```python
def _parse_from_milvus_results(
    self, results: List
) -> Tuple[List[BaseNode], List[float], List[str]]:
    # 【中文研读】处理流程：只处理首组结果，恢复节点元信息及正文，按同一遍历顺序收集 distance 与 ID。
    if len(results) > 1:
        logger.warning(
            "More than one result found in Milvus search. Only parsing the first result."
        )
    nodes = []
    similarities = []
    ids = []
    # 【中文研读】当前解析器只遍历第一条查询的结果，不能把它当作已经支持任意批量查询结果解包。
    for hit in results[0]:
        if "_node_content" in hit["entity"]:
            metadata = {
                "_node_content": hit["entity"].get("_node_content", None),
                "_node_type": hit["entity"].get("_node_type", None),
            }
            for key in self.output_fields:
                metadata[key] = hit["entity"].get(key)
            node = metadata_dict_to_node(metadata)
        else:
            node = TextNode(
                metadata={key: hit["entity"].get(key) for key in self.output_fields}
            )

        if self.text_key in hit["entity"]:
            text = hit["entity"].get(self.text_key)
            node.text = text

        nodes.append(node)
        # 【中文研读】驱动字段叫 distance；具体数值方向由度量方式决定，不是自动统一的相关性概率。
        similarities.append(hit["distance"])
        ids.append(self._get_id_from_hit(hit))
    return nodes, similarities, ids
```

**执行后得到什么：** nodes / similarities / ids。

**接下来到哪里：** ID 获取看步骤 16，随后返回 aquery 与向量检索器。

**失败与 P3 责任：** 这些节点还不是当前有效的正式记忆；P3 需以权威记忆读取替代无条件信任向量正文。

<a id="s16"></a>

### 16　展开：从命中里取稳定引用

**当前执行位置：** `MilvusVectorStore._get_id_from_hit`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-integrations/vector_stores/llama-index-vector-stores-milvus/llama_index/vector_stores/milvus/base.py)，注释版第 1747—1752 行。

**收到什么：** 单个 hit。

**这一段怎么处理：** 按适配器定义的 ID 字段从命中结果取值。

```python
def _get_id_from_hit(self, hit: Dict) -> str:
    # 【中文研读】处理流程：有 id 则直接读取，否则使用首个字段的值；接入方必须核对实际返回结构。
    if "id" in hit:
        return hit["id"]
    else:
        return hit[next(iter(hit))]
```

**执行后得到什么：** 当前 hit 对应的节点 ID。

**接下来到哪里：** 回到解析循环，沿调用栈返回 VectorIndexRetriever。

**失败与 P3 责任：** 节点 ID 的存在不等于携带了 P3 当前版本，应显式保存 memory_id 和 version。

<a id="s17"></a>

### 17　回到检索器：判断是否还需要补正文

**当前执行位置：** `VectorIndexRetriever._aget_nodes_with_embeddings`，方法内部片段。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/indices/vector_store/retrievers/retriever.py)，注释版第 322—337 行。

**收到什么：** 向量库返回的结果。

**这一段怎么处理：** 确定需补读节点；有缺失才请求 DocStore；补回正确位置，包装带分候选。

```python
nodes_to_fetch = self._determine_nodes_to_fetch(query_result)
# 【中文研读】有缺失正文才补读；这避免向量库已返回完整文本时再次访问 DocStore。
if nodes_to_fetch:
    fetched_nodes: List[BaseNode] = await self._docstore.aget_nodes(
        node_ids=nodes_to_fetch, raise_error=False
    )

    query_result.nodes = self._insert_fetched_nodes_into_query_result(
        query_result, fetched_nodes
    )

log_vector_store_query_result(query_result)

# 【中文研读】返回统一候选列表，不代表已执行 P3 权限、删除、版本和来源核验。
return self._convert_nodes_to_scored_nodes(query_result)
```

**执行后得到什么：** List[NodeWithScore]。

**接下来到哪里：** 步骤 18—20 展开补读判断、顺序恢复与包装；完成后回到来源公共入口。

**失败与 P3 责任：** DocStore 补读成功仍不代表 P3 授权/删除/版本验证已完成。

<a id="s18"></a>

### 18　展开：哪些引用需要补读

**当前执行位置：** `VectorIndexRetriever._determine_nodes_to_fetch`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/indices/vector_store/retrievers/retriever.py)，注释版第 184—211 行。

**收到什么：** 含 nodes 或 ids 的向量结果。

**这一段怎么处理：** 已有节点时按类型判断；只有 ID 时借索引结构映射正文 ID。

```python
def _determine_nodes_to_fetch(
    self, query_result: VectorStoreQueryResult
) -> list[str]:
    # 【中文研读】处理流程：已有节点时筛出非文本节点，只有 IDs 时经索引映射查正文 ID，没有引用则无需补读。
    # 【中文研读】数据库已返回节点时可利用节点内容，是否补正文由节点类型决定。
    if query_result.nodes:
        return [
            node.node_id
            for node in query_result.nodes  # no folding
            if node.as_related_node_info().node_type
            != ObjectType.TEXT  # TODO: no need to fetch multimodal `Node` if they only include text
        ]
    # 【中文研读】只拿到向量索引 ID 时，必须通过索引映射解析正文节点；不能把相似度命中直接当完整内容。
    elif query_result.ids:
        return [
            self._index.index_struct.nodes_dict[idx] for idx in query_result.ids
        ]

    else:
        return []
```

**执行后得到什么：** 需要向 DocStore 请求的节点 ID 列表。

**接下来到哪里：** DocStore.aget_nodes 是存储边界，返回后进入步骤 19。

**失败与 P3 责任：** 向量库已经返回文本时可能跳过 DocStore；P3 仍需权威版本核验。

<a id="s19"></a>

### 19　展开：补读后恢复原始命中顺序

**当前执行位置：** `VectorIndexRetriever._insert_fetched_nodes_into_query_result`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/indices/vector_store/retrievers/retriever.py)，注释版第 216—261 行。

**收到什么：** 原查询结果与补读节点。

**这一段怎么处理：** 先按 ID 建映射，再沿原命中次序替换；无法解析必需 ID 时抛错。

```python
def _insert_fetched_nodes_into_query_result(
    self, query_result: VectorStoreQueryResult, fetched_nodes: List[BaseNode]
) -> Sequence[BaseNode]:
    # 【中文研读】处理流程：建立 ID 字典，已有节点尽量替换，只有 ID 的结果必须能完整解析，否则抛错。
    # 【中文研读】先建正文 ID 到节点的映射，以便恢复查询返回顺序，避免按补读顺序错误配对分数。
    fetched_nodes_by_id: Dict[str, BaseNode] = {
        str(node.node_id): node for node in fetched_nodes
    }
    new_nodes: List[BaseNode] = []

    # 【中文研读】数据库已返回节点时可利用节点内容，是否补正文由节点类型决定。
    if query_result.nodes:
        for node in list(query_result.nodes):
            node_id_str = str(node.node_id)
            # 【中文研读】先建正文 ID 到节点的映射，以便恢复查询返回顺序，避免按补读顺序错误配对分数。
            if node_id_str in fetched_nodes_by_id:
                new_nodes.append(fetched_nodes_by_id[node_id_str])
            else:
                new_nodes.append(node)
    # 【中文研读】只拿到向量索引 ID 时，必须通过索引映射解析正文节点；不能把相似度命中直接当完整内容。
    elif query_result.ids:
        for node_id in query_result.ids:
            # 【中文研读】向量引用无法映射到正文时显式失败；P3 还需要区分已失效引用与依赖故障。
            if node_id not in self._index.index_struct.nodes_dict:
                raise KeyError(f"Node ID {node_id} not found in index. ")
            node_id_str = str(self._index.index_struct.nodes_dict[node_id])
            # 【中文研读】先建正文 ID 到节点的映射，以便恢复查询返回顺序，避免按补读顺序错误配对分数。
            if node_id_str in fetched_nodes_by_id:
                new_nodes.append(fetched_nodes_by_id[node_id_str])
            else:
                raise KeyError(
                    f"Node ID {node_id_str} not found in fetched nodes. "
                )
    elif query_result.ids is None and query_result.nodes is None:
        raise ValueError(
            "Vector store query result should return at least one of nodes or ids."
        )
    return new_nodes
```

**执行后得到什么：** 与相似度位置对应的正文节点列表。

**接下来到哪里：** 步骤 20 将节点和相似度绑定。

**失败与 P3 责任：** 不能按数据库补读返回顺序盲目配分数；不然会把别条正文与高分匹配。

<a id="s20"></a>

### 20　展开：把同一位置的正文和分数组合

**当前执行位置：** `VectorIndexRetriever._convert_nodes_to_scored_nodes`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/indices/vector_store/retrievers/retriever.py)，注释版第 266—281 行。

**收到什么：** 已恢复顺序的节点与相似度数组。

**这一段怎么处理：** 按索引逐个生成 NodeWithScore，缺省分数保留 None。

```python
def _convert_nodes_to_scored_nodes(
    self, query_result: VectorStoreQueryResult
) -> List[NodeWithScore]:
    # 【中文研读】处理流程：分数列表缺省则保留 None，不凭空生成模型分数。
    node_with_scores: List[NodeWithScore] = []

    for ind, node in enumerate(list(query_result.nodes or [])):
        score: Optional[float] = None
        # 【中文研读】按索引位置绑定分数，前提是节点序列和相似度列表顺序一致。
        if query_result.similarities is not None:
            score = query_result.similarities[ind]

        node_with_scores.append(NodeWithScore(node=node, score=score))

    return node_with_scores
```

**执行后得到什么：** 统一候选列表。

**接下来到哪里：** 公共入口处理递归引用后，各来源列表交回 gather，随后步骤 23 融合。

**失败与 P3 责任：** NodeWithScore 的 score 是相关性分数，不是事实正确性或授权证明。

<a id="s21"></a>

### 21　公共入口收尾：展开递归节点并去重

**当前执行位置：** `BaseRetriever._ahandle_recursive_retrieval`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/base/base_retriever.py)，注释版第 199—237 行。

**收到什么：** 一个来源返回的节点列表；外层融合器返回后也会经过此步骤。

**这一段怎么处理：** 普通节点直接保留；IndexNode 找引用对象再检索；最后按 node_id 去重。

```python
async def _ahandle_recursive_retrieval(
    self, query_bundle: QueryBundle, nodes: List[NodeWithScore]
) -> List[NodeWithScore]:
    # 【中文研读】处理流程：循环中逐项 await，并没有并发 gather；最终仍按 node_id，而非 P3 的 memory_id/version 去重。
    retrieved_nodes: List[NodeWithScore] = []
    for n in nodes:
        node = n.node
        # 【中文研读】原代码将 None 和数值 0 都替换成 1.0；学习时注意 Python 的 or 语义，不要误认为只处理缺失分数。
        score = n.score or 1.0
        # 【中文研读】索引节点可能引用另一个可检索对象；普通内容节点可以直接保留。
        if isinstance(node, IndexNode):
            obj = node.obj or self.object_map.get(node.index_id, None)
            if obj is not None:
                if self._verbose:
                    print_text(
                        f"Retrieval entering {node.index_id}: {obj.__class__.__name__}\n",
                        color="llama_turquoise",
                    )
                retrieved_nodes.extend(
                    await self._aretrieve_from_object(
                        obj, query_bundle=query_bundle, score=score
                    )
                )
            else:
                retrieved_nodes.append(n)
        else:
            retrieved_nodes.append(n)

    # 【中文研读】集合记录已交付的节点 ID；列表推导式用短路逻辑完成首次保留，后续重复 ID 被排除。
    seen = set()
    return [
        n
        for n in retrieved_nodes
        if not (
            n.node.node_id in seen or seen.add(n.node.node_id)  # type: ignore[func-returns-value]
        )
    ]
```

**执行后得到什么：** 处理过引用的候选列表。

**接下来到哪里：** 若确实有 IndexNode，步骤 22 展开对象分派；普通 P3 记忆节点直接返回。

**失败与 P3 责任：** 按 node_id 去重与按 memory_id+version 不是天然等价；P3 主链不应无意引入生成内容。

<a id="s22"></a>

### 22　可选分支：引用对象可能是另一个检索器或查询引擎

**当前执行位置：** `BaseRetriever._aretrieve_from_object`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/base/base_retriever.py)，注释版第 128—153 行。

**收到什么：** IndexNode 所指的实际对象和查询。

**这一段怎么处理：** 根据对象类型返回节点、递归检索，或调用查询引擎得到文本。

```python
async def _aretrieve_from_object(
    self,
    obj: Any,
    query_bundle: QueryBundle,
    score: float,
) -> List[NodeWithScore]:
    # 【中文研读】处理流程：节点直接包装，查询引擎与子检索器使用异步入口；生成的回答文本不是原始记忆事实。
    # 【中文研读】类型分派从最具体的包装节点开始；不同对象最终统一为 List[NodeWithScore]。
    if isinstance(obj, NodeWithScore):
        return [obj]
    elif isinstance(obj, BaseNode):
        return [NodeWithScore(node=obj, score=score)]
    elif isinstance(obj, BaseQueryEngine):
        # 【中文研读】异步查询引擎同样可能生成回答；异步只改变等待方式，不增加事实或授权保证。
        response = await obj.aquery(query_bundle)
        return [
            NodeWithScore(
                node=TextNode(text=str(response), metadata=response.metadata or {}),
                score=score,
            )
        ]
    elif isinstance(obj, BaseRetriever):
        return await obj.aretrieve(query_bundle)
    else:
        raise ValueError(f"Object {obj} is not retrievable.")
```

**执行后得到什么：** 被展开的节点列表。

**接下来到哪里：** 返回上一步，最终交回来源结果汇总。

**失败与 P3 责任：** 查询引擎分支可能生成新回答，不是权威原文；P3 需限制允许的对象类型。

<a id="s23"></a>

### 23　各路都返回后：RRF 按名次融合

**当前执行位置：** `QueryFusionRetriever._reciprocal_rerank_fusion`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/retrievers/fusion_retriever.py)，注释版第 143—183 行。

**收到什么：** 按来源分组的 NodeWithScore 列表。

**这一段怎么处理：** 各路先按分数排序，按节点 hash 累加 1/(rank+60)，再按融合分数排序并改写节点分数。

```python
def _reciprocal_rerank_fusion(
    self, results: Dict[Tuple[str, int], List[NodeWithScore]]
) -> List[NodeWithScore]:
    # 【中文研读】处理流程：各路按分数降序，按节点 hash 累加 1/(rank+60)，再按融合分数排序并写回节点分数。
    # 【中文研读】平滑常数减少头部名次波动的影响；当前代码 enumerate 从 0 开始，第一名贡献 1/60，P3 契约需单独固定起点。
    k = 60.0  # `k` is a parameter used to control the impact of outlier rankings.
    fused_scores = {}
    hash_to_node = {}

    for nodes_with_scores in results.values():
        for rank, node_with_score in enumerate(
            sorted(nodes_with_scores, key=lambda x: x.score or 0.0, reverse=True)
        ):
            # 【中文研读】这里的归并身份是节点 hash；P3 需按 memory_id + version 判断同一业务候选。
            hash = node_with_score.node.hash
            hash_to_node[hash] = node_with_score
            if hash not in fused_scores:
                fused_scores[hash] = 0.0
            # 【中文研读】同一节点在多路排名中累加贡献；不直接相加各模型的原始相似度。
            fused_scores[hash] += 1.0 / (rank + k)

    reranked_results = dict(
        sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
    )

    reranked_nodes: List[NodeWithScore] = []
    for hash, score in reranked_results.items():
        reranked_nodes.append(hash_to_node[hash])
        # 【中文研读】原 NodeWithScore 对象的 score 被改写；复用同一对象引用时要留意原始分数不会自动保留。
        reranked_nodes[-1].score = score

    return reranked_nodes
```

**执行后得到什么：** 融合候选列表。

**接下来到哪里：** 回到 _aretrieve 按 similarity_top_k 截取，再经公共入口收尾返回；可选后处理见步骤 25。

**失败与 P3 责任：** 上游名次从 0 起算，首名贡献 1/60；P3 契约需要独立固定且保留原始分数。

<a id="s24"></a>

### 24　展开：RRF 的节点 hash 从哪里来

**当前执行位置：** `TextNode.hash`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/schema.py)，注释版第 1014—1019 行。

**收到什么：** 节点文本与 metadata。

**这一段怎么处理：** 根据节点内容计算 hash。

```python
@property
def hash(self) -> str:
    # 【中文研读】处理流程：相同正文但 metadata 不同可得到不同 hash，不能直接代替业务版本身份。
    # 【中文研读】TextNode 的 hash 同时依赖文本和元信息；按 hash 去重可能受 metadata 变化影响。
    doc_identity = str(self.text) + str(self.metadata)
    return str(sha256(doc_identity.encode("utf-8", "surrogatepass")).hexdigest())
```

**执行后得到什么：** 内容相关的归并键。

**接下来到哪里：** 返回 RRF 的 hash 归并。

**失败与 P3 责任：** 内容相同不等于同一业务记忆版本；因此 P3 不直接以这个 hash 作为候选唯一身份。

<a id="s25"></a>

### 25　组合容器：只检索也可以使用 QueryEngine

**当前执行位置：** `RetrieverQueryEngine.aretrieve`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/query_engine/retriever_query_engine.py)，注释版第 199—205 行。

**收到什么：** QueryBundle 与已装配的 retriever、后处理器。

**这一段怎么处理：** await 检索器候选，然后按顺序执行后处理。

```python
async def aretrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
    # 【中文研读】处理流程：等待检索完成后再按序处理，返回候选列表。
    # 【中文研读】统一异步接口隐藏具体来源，但是否真正非阻塞仍由具体实现决定。
    nodes = await self._retriever.aretrieve(query_bundle)
    return await self._async_apply_node_postprocessors(
        nodes, query_bundle=query_bundle
    )
```

**执行后得到什么：** 经过后处理的节点列表。

**接下来到哪里：** 处理器循环见步骤 26。

**失败与 P3 责任：** 本方法不生成回答，但默认也没有 P3 ContextPack 的资格与预算规则。

<a id="s26"></a>

### 26　展开：后处理器前后串联

**当前执行位置：** `RetrieverQueryEngine._async_apply_node_postprocessors`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/query_engine/retriever_query_engine.py)，注释版第 176—185 行。

**收到什么：** 候选列表与处理器序列。

**这一段怎么处理：** 后一处理器接收前一处理器的输出，因此顺序 await，而非并行修改同一个列表。

```python
async def _async_apply_node_postprocessors(
    self, nodes: List[NodeWithScore], query_bundle: QueryBundle
) -> List[NodeWithScore]:
    # 【中文研读】处理流程：异步不代表并行，因为后一阶段依赖前一阶段的输出。
    # 【中文研读】处理器按用户提供的列表顺序执行或配置；列表顺序就是管道顺序。
    for node_postprocessor in self._node_postprocessors:
        nodes = await node_postprocessor.apostprocess_nodes(
            nodes, query_bundle=query_bundle
        )
    return nodes
```

**执行后得到什么：** 最后一个处理器输出的候选。

**接下来到哪里：** 每个处理器先进入步骤 27 的公共接口。

**失败与 P3 责任：** 先过滤再重排与先重排再过滤影响成本和安全边界，P3 必须明确装配顺序。

<a id="s27"></a>

### 27　展开：后处理统一输入

**当前执行位置：** `BaseNodePostprocessor.apostprocess_nodes`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/postprocessor/types.py)，注释版第 89—105 行。

**收到什么：** 候选列表、query_bundle 或 query_str。

**这一段怎么处理：** 拒绝两份查询同时出现，必要时包装文本，然后 await 子类处理。

```python
async def apostprocess_nodes(
    self,
    nodes: List[NodeWithScore],
    query_bundle: Optional[QueryBundle] = None,
    query_str: Optional[str] = None,
) -> List[NodeWithScore]:
    # 【中文研读】处理流程：执行相同参数互斥检查和包装，再等待子类异步实现。
    # 【中文研读】拒绝同时给出两份查询，避免处理器不知道该以哪个输入为准。
    if query_str is not None and query_bundle is not None:
        raise ValueError("Cannot specify both query_str and query_bundle")
    # 【中文研读】仅给字符串时补成统一对象；未给查询的处理器仍可只处理节点。
    elif query_str is not None:
        query_bundle = QueryBundle(query_str)
    else:
        pass
    return await self._apostprocess_nodes(nodes, query_bundle)
```

**执行后得到什么：** 子类筛选或排序后的候选。

**接下来到哪里：** 默认异步适配见步骤 28，具体重排计算见第 04 章。

**失败与 P3 责任：** 基类未实现授权/版本筛选，只有接口；需要 P3 自己提供相应处理规则。

<a id="s28"></a>

### 28　展开：默认异步后处理怎样兼容同步实现

**当前执行位置：** `BaseNodePostprocessor._apostprocess_nodes`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/postprocessor/types.py)，注释版第 110—118 行。

**收到什么：** 节点列表与查询。

**这一段怎么处理：** 通过 to_thread 执行具体子类的同步 _postprocess_nodes。

```python
async def _apostprocess_nodes(
    self,
    nodes: List[NodeWithScore],
    query_bundle: Optional[QueryBundle] = None,
) -> List[NodeWithScore]:
    # 【中文研读】处理流程：asyncio.to_thread 避免直接阻塞事件循环，但取消等待不等于强行停止线程工作。
    # 【中文研读】线程适配只提供执行方式，不提供持久化检查点、幂等或重试。
    return await asyncio.to_thread(self._postprocess_nodes, nodes, query_bundle)
```

**执行后得到什么：** 同步处理函数的结果。

**接下来到哪里：** 返回处理器循环；完整 QueryEngine 是否继续生成回答见步骤 29。

**失败与 P3 责任：** 取消等待不保证线程已停止；超时和副作用需要调用方处理。

<a id="s29"></a>

### 29　对照：完整 QueryEngine 在哪里开始生成回答

**当前执行位置：** `RetrieverQueryEngine._aquery`，完整方法或类型摘录。[出处](../第三方源码中文注释/llamaindex/llama-index-core/llama_index/core/query_engine/retriever_query_engine.py)，注释版第 274—291 行。

**收到什么：** 查询及已经装配好的回答合成器。

**这一段怎么处理：** 先调用 aretrieve，再将候选交给 asynthesize；记录回答事件并返回。

```python
@dispatcher.span
async def _aquery(self, query_bundle: QueryBundle) -> RESPONSE_TYPE:
    # 【中文研读】处理流程：先等待检索，再等待回答合成，记录同一查询事件结果。
    with self.callback_manager.event(
        CBEventType.QUERY, payload={EventPayload.QUERY_STR: query_bundle.query_str}
    ) as query_event:
        nodes = await self.aretrieve(query_bundle)

        # 【中文研读】异步答案生成边界；候选中的业务授权与版本必须在我们自己的流程中落实。
        response = await self._response_synthesizer.asynthesize(
            query=query_bundle,
            nodes=nodes,
        )

        query_event.on_end(payload={EventPayload.RESPONSE: response})

    return response
```

**执行后得到什么：** 框架回答响应。

**接下来到哪里：** P3 Recall 在自己的最终核验与组包后交付 ContextPack，下游再决定是否生成回答。

**失败与 P3 责任：** 不能把 response 生成成功等同于 PRD 的 Recall 交付成功；也不能声称 QueryEngine 无法扩展。

## 本章出现的外部调用与停止展开的位置

| 调用或能力 | 在这条链中的作用 | 为什么在这里画边界 |
|---|---|---|
| Embedding.aget_agg_embedding_from_queries | 查询文本到查询向量的模型接口 | 不是直接调用 FastEmbed；需要兼容适配器。第 02 章展开一种可用底层实现 |
| Milvus client.search / filter 标准化 | 真正网络检索和表达式转换 | 数据库客户端边界；不展开 Milvus 服务端索引或网络协议 |
| DocStore.aget_nodes / metadata_dict_to_node | 补读正文、从元数据恢复节点 | 存储与序列化边界；P3 应替换为权威精确版本读取 |
| BaseRetriever 动态分派 / IndexNode 对象 | 在同一入口下调用不同来源 | Working 具体算法、递归指向的其他引擎由装配决定，不存在统一可摘的业务实现 |
| dispatcher.span / callback_manager | 记录检索开始、结束等运行事件 | 框架回调不是 Task/Outbox，也不自动满足 P3 全链 trace 的传播和落盘要求 |
| P3 来源覆盖、最终资格、ContextPack | 决定能否交付当前结果 | 项目规则层；不能把通用节点列表当作已完成这层保障 |

## 对照 P3 应怎样使用

我们采用“每个来源提供统一输入输出”的组织方式，但需要比 List[NodeWithScore] 更完整的结果封套：来源成功/失败/超时、候选精确版本、覆盖状态及错误原因。

scope 过滤在查询前由可信上下文构建；正文从权威存储按精确版本加载；交付前再核验当前授权、版本与删除状态。不能允许客户端任意传过滤表达式，也不能因向量命中就认定正文仍有效。

RRF 可以参考算法结构，但项目的归并键为 memory_id + version，名次起点和并列规则明确固定；上游这里按节点 hash、名次从零开始。算法名称相同不能替代契约测试。

本章末尾展示通用 QueryEngine 的回答合成位置，说明它能扩展，但默认没有替 P3 做 ContextPack 的产品规则。第四章继续解释可插入的重排和计数能力。
