# 【中文研读】阅读主线：按需生成查询向量 → 构造 VectorStoreQuery → 查询向量库 → 按需补正文 → 对齐分数。P3 采用稠密向量路径，上游其他模式保留原文供来源比对。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
"""Base vector store index query."""

from collections.abc import Sequence
from typing import Any, Dict, List, Optional

from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.callbacks.base import CallbackManager
from llama_index.core.constants import DEFAULT_SIMILARITY_TOP_K
from llama_index.core.indices.utils import log_vector_store_query_result
from llama_index.core.indices.vector_store.base import VectorStoreIndex
from llama_index.core.schema import BaseNode, NodeWithScore, ObjectType, QueryBundle
from llama_index.core.vector_stores.types import (
    MetadataFilters,
    VectorStoreQuery,
    VectorStoreQueryMode,
    VectorStoreQueryResult,
)
import llama_index.core.instrumentation as instrument

dispatcher = instrument.get_dispatcher(__name__)


# 【中文研读】类型职责：向量索引到统一检索器的适配；协调 Embedding、向量查询和 DocStore 补读。
class VectorIndexRetriever(BaseRetriever):
    """
    Vector index retriever.

    Args:
        index (VectorStoreIndex): vector store index.
        similarity_top_k (int): number of top k results to return.
        vector_store_query_mode (str): vector store query mode
            See reference for VectorStoreQueryMode for full list of supported modes.
        filters (Optional[MetadataFilters]): metadata filters, defaults to None
        alpha (float): weight for sparse/dense retrieval, only used for
            hybrid query mode.
        doc_ids (Optional[List[str]]): list of documents to constrain search.
        vector_store_kwargs (dict): Additional vector store specific kwargs to pass
            through to the vector store at query time.

    """

    # 【中文研读】方法职责：从索引取得向量库、Embedding 和正文库，保存候选数量与过滤配置
    # 【中文研读】输入参数：index（向量索引实例）；similarity_top_k（最多返回的候选数量）；vector_store_query_mode；filters（结构化过滤条件集合）；alpha；node_ids（限制或读取的节点 ID 集合）；doc_ids（限制或读取的来源文档 ID 集合）；sparse_top_k；hybrid_top_k；callback_manager（运行事件与回调管理器）；object_map（索引 ID 到可检索对象的映射）；embed_model（生成查询向量的模型）；verbose（是否输出调试信息）；**kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def __init__(
        self,
        index: VectorStoreIndex,
        similarity_top_k: int = DEFAULT_SIMILARITY_TOP_K,
        vector_store_query_mode: VectorStoreQueryMode = VectorStoreQueryMode.DEFAULT,
        filters: Optional[MetadataFilters] = None,
        alpha: Optional[float] = None,
        node_ids: Optional[List[str]] = None,
        doc_ids: Optional[List[str]] = None,
        sparse_top_k: Optional[int] = None,
        hybrid_top_k: Optional[int] = None,
        callback_manager: Optional[CallbackManager] = None,
        object_map: Optional[dict] = None,
        embed_model: Optional[BaseEmbedding] = None,
        verbose: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize params."""
        # 【中文研读】处理流程：先绑定依赖，再保存查询选项，最后初始化基类回调与对象映射。
        self._index = index
        self._vector_store = self._index.vector_store
        self._embed_model = embed_model or self._index._embed_model
        self._docstore = self._index.docstore

        self._similarity_top_k = similarity_top_k
        self._vector_store_query_mode = VectorStoreQueryMode(vector_store_query_mode)
        self._alpha = alpha
        self._node_ids = node_ids
        self._doc_ids = doc_ids
        self._filters = filters
        self._sparse_top_k = sparse_top_k
        self._hybrid_top_k = hybrid_top_k
        self._kwargs: Dict[str, Any] = kwargs.get("vector_store_kwargs", {})

        callback_manager = callback_manager or CallbackManager()
        super().__init__(
            callback_manager=callback_manager,
            object_map=object_map,
            verbose=verbose,
        )

    # 【中文研读】方法职责：读取或设置候选数量上限；setter 修改本检索器配置，不修改数据库索引结构。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：int。结果的业务含义与失败分支见下面处理流程。
    @property
    def similarity_top_k(self) -> int:
        """Return similarity top k."""
        # 【中文研读】处理流程：读取或设置候选数量上限；setter 修改本检索器配置，不修改数据库索引结构。
        return self._similarity_top_k

    # 【中文研读】方法职责：读取或设置候选数量上限；setter 修改本检索器配置，不修改数据库索引结构。
    # 【中文研读】输入参数：similarity_top_k（最多返回的候选数量）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @similarity_top_k.setter
    def similarity_top_k(self, similarity_top_k: int) -> None:
        """Set similarity top k."""
        # 【中文研读】处理流程：读取或设置候选数量上限；setter 修改本检索器配置，不修改数据库索引结构。
        self._similarity_top_k = similarity_top_k

    # 【中文研读】方法职责：判断本查询模式是否需要显式查询向量
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：bool。结果的业务含义与失败分支见下面处理流程。
    def _needs_embedding(self) -> bool:
        """Check if the current query mode requires embeddings."""
        # 【中文研读】处理流程：同时检查向量库能力与模式，P3 主线使用需要向量的默认路径。
        return (
            self._vector_store.is_embedding_query
            and self._vector_store_query_mode
            not in (
                VectorStoreQueryMode.TEXT_SEARCH,
                VectorStoreQueryMode.SPARSE,
            )
        )

    # 【中文研读】方法职责：同步检索时按需补查询向量
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    @dispatcher.span
    def _retrieve(
        self,
        query_bundle: QueryBundle,
    ) -> List[NodeWithScore]:
        # 【中文研读】处理流程：已有 embedding 则复用，否则编码 embedding_strs，随后进入向量库查询与正文补读。
        # 【中文研读】先判定是否需要向量；已有向量可避免重复编码，但此上游入口没有验证 P3 模型空间绑定。
        if self._needs_embedding():
            # 【中文研读】只有向量缺失且存在可编码文本时才调用模型；不要把无文本输入误认为模型服务失败。
            if query_bundle.embedding is None and len(query_bundle.embedding_strs) > 0:
                query_bundle.embedding = (
                    self._embed_model.get_agg_embedding_from_queries(
                        query_bundle.embedding_strs
                    )
                )
        return self._get_nodes_with_embeddings(query_bundle)

    # 【中文研读】方法职责：异步生成向量并发起查询
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
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

    # 【中文研读】方法职责：把检索器配置转换为向量适配器请求
    # 【中文研读】输入参数：query_bundle_with_embeddings（已准备好向量的查询对象）。
    # 【中文研读】返回约定：VectorStoreQuery。结果的业务含义与失败分支见下面处理流程。
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

    # 【中文研读】方法职责：决定哪些节点还需到正文库补读
    # 【中文研读】输入参数：query_result（向量库返回的节点、ID 与分数集合）。
    # 【中文研读】返回约定：list[str]。结果的业务含义与失败分支见下面处理流程。
    def _determine_nodes_to_fetch(
        self, query_result: VectorStoreQueryResult
    ) -> list[str]:
        """
        Determine the nodes to fetch from the docstore.

        If the vector store does not store text, we need to fetch every node from the docstore.
        If the vector store stores text, we need to fetch only the nodes that are not text.
        """
        # 【中文研读】处理流程：已有节点时筛出非文本节点，只有 IDs 时经索引映射查正文 ID，没有引用则无需补读。
        # 【中文研读】数据库已返回节点时可利用节点内容，是否补正文由节点类型决定。
        if query_result.nodes:
            # Fetch non-text nodes from the docstore
            return [
                node.node_id
                for node in query_result.nodes  # no folding
                if node.as_related_node_info().node_type
                != ObjectType.TEXT  # TODO: no need to fetch multimodal `Node` if they only include text
            ]
        # 【中文研读】只拿到向量索引 ID 时，必须通过索引映射解析正文节点；不能把相似度命中直接当完整内容。
        elif query_result.ids:
            # Fetch all nodes from the docstore
            return [
                self._index.index_struct.nodes_dict[idx] for idx in query_result.ids
            ]

        else:
            return []

    # 【中文研读】方法职责：将补读正文按 ID 放回原查询顺序
    # 【中文研读】输入参数：query_result（向量库返回的节点、ID 与分数集合）；fetched_nodes（从正文库补读的节点）。
    # 【中文研读】返回约定：Sequence[BaseNode]。结果的业务含义与失败分支见下面处理流程。
    def _insert_fetched_nodes_into_query_result(
        self, query_result: VectorStoreQueryResult, fetched_nodes: List[BaseNode]
    ) -> Sequence[BaseNode]:
        """
        Insert the fetched nodes into the query result.

        If the vector store does not store text, all nodes are inserted into the query result.
        If the vector store stores text, we replace non-text nodes with those fetched from the docstore,
            unless the node was not found in the docstore, in which case we keep the original node.
        """
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
                    # We did not fetch a replacement node, so we keep the original node
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

    # 【中文研读】方法职责：把正文与相同位置的相似度配成 NodeWithScore
    # 【中文研读】输入参数：query_result（向量库返回的节点、ID 与分数集合）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    def _convert_nodes_to_scored_nodes(
        self, query_result: VectorStoreQueryResult
    ) -> List[NodeWithScore]:
        """Create scored nodes from the vector store query result."""
        # 【中文研读】处理流程：分数列表缺省则保留 None，不凭空生成模型分数。
        node_with_scores: List[NodeWithScore] = []

        for ind, node in enumerate(list(query_result.nodes or [])):
            score: Optional[float] = None
            # 【中文研读】按索引位置绑定分数，前提是节点序列和相似度列表顺序一致。
            if query_result.similarities is not None:
                score = query_result.similarities[ind]

            node_with_scores.append(NodeWithScore(node=node, score=score))

        return node_with_scores

    # 【中文研读】方法职责：执行同步向量查询和补读
    # 【中文研读】输入参数：query_bundle_with_embeddings（已准备好向量的查询对象）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    def _get_nodes_with_embeddings(
        self, query_bundle_with_embeddings: QueryBundle
    ) -> List[NodeWithScore]:
        # 【中文研读】处理流程：组装请求 → 查询向量库 → 判断缺失正文 → DocStore 获取 → 写回结果 → 记录查询 → 包装候选。
        query = self._build_vector_store_query(query_bundle_with_embeddings)
        # 【中文研读】此处才进入具体向量数据库适配器，可能发生网络超时或服务错误。
        query_result = self._vector_store.query(query, **self._kwargs)

        nodes_to_fetch = self._determine_nodes_to_fetch(query_result)
        # 【中文研读】有缺失正文才补读；这避免向量库已返回完整文本时再次访问 DocStore。
        if nodes_to_fetch:
            # Fetch any missing nodes from the docstore and insert them into the query result
            fetched_nodes: List[BaseNode] = self._docstore.get_nodes(
                node_ids=nodes_to_fetch, raise_error=False
            )

            query_result.nodes = self._insert_fetched_nodes_into_query_result(
                query_result, fetched_nodes
            )

        log_vector_store_query_result(query_result)

        # 【中文研读】返回统一候选列表，不代表已执行 P3 权限、删除、版本和来源核验。
        return self._convert_nodes_to_scored_nodes(query_result)

    # 【中文研读】方法职责：异步执行向量查询和补读
    # 【中文研读】输入参数：query_bundle_with_embeddings（已准备好向量的查询对象）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    async def _aget_nodes_with_embeddings(
        self, query_bundle_with_embeddings: QueryBundle
    ) -> List[NodeWithScore]:
        # 【中文研读】处理流程：分别 await 向量库与 DocStore，复用同样的节点替换和分数包装逻辑。
        query = self._build_vector_store_query(query_bundle_with_embeddings)
        # 【中文研读】异步数据库调用边界；异常向外传播，框架不会自动决定 P3 是否允许降级。
        query_result = await self._vector_store.aquery(query, **self._kwargs)

        nodes_to_fetch = self._determine_nodes_to_fetch(query_result)
        # 【中文研读】有缺失正文才补读；这避免向量库已返回完整文本时再次访问 DocStore。
        if nodes_to_fetch:
            # Fetch any missing nodes from the docstore and insert them into the query result
            fetched_nodes: List[BaseNode] = await self._docstore.aget_nodes(
                node_ids=nodes_to_fetch, raise_error=False
            )

            query_result.nodes = self._insert_fetched_nodes_into_query_result(
                query_result, fetched_nodes
            )

        log_vector_store_query_result(query_result)

        # 【中文研读】返回统一候选列表，不代表已执行 P3 权限、删除、版本和来源核验。
        return self._convert_nodes_to_scored_nodes(query_result)
