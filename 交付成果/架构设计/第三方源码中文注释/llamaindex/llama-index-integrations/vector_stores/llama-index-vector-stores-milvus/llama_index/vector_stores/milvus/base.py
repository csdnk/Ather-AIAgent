# 【中文研读】研读采用路径：构造客户端与 collection → add 写向量及节点元信息 → query/_default_search 稠密检索 → 解析命中。重点参考驱动边界和过滤映射；原库的其他可选模式仅保留原文，不纳入 P3 方案。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
"""
Milvus vector store index.

An index that is built within Milvus.

"""

import logging
from typing import Any, Dict, List, Optional, Union, Tuple
from copy import deepcopy
from enum import Enum

from llama_index.core.bridge.pydantic import Field, PrivateAttr
from llama_index.core.indices.query.embedding_utils import get_top_k_mmr_embeddings
from llama_index.core.schema import BaseNode, TextNode
from llama_index.core.utils import iter_batch
from llama_index.vector_stores.milvus.utils import (
    get_default_sparse_embedding_function,
    BaseSparseEmbeddingFunction,
    BaseMilvusBuiltInFunction,
    BM25BuiltInFunction,
    ScalarMetadataFilters,
    parse_standard_filters,
    parse_scalar_filters,
    DEFAULT_SPARSE_EMBEDDING_KEY,
)
from llama_index.core.vector_stores.types import (
    BasePydanticVectorStore,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
    VectorStoreQuery,
    VectorStoreQueryMode,
    VectorStoreQueryResult,
)
from llama_index.core.vector_stores.utils import (
    DEFAULT_TEXT_KEY,
    DEFAULT_DOC_ID_KEY,
    DEFAULT_EMBEDDING_KEY,
    metadata_dict_to_node,
    node_to_metadata_dict,
)
from pymilvus import (
    CollectionSchema,
    MilvusClient,
    AsyncMilvusClient,
    DataType,
    AnnSearchRequest,
)
from pymilvus.client.types import LoadState
from pymilvus.milvus_client.index import IndexParams

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 100
MILVUS_ID_FIELD = "id"
DEFAULT_MMR_PREFETCH_FACTOR = 4.0

try:
    from pymilvus import WeightedRanker, RRFRanker
except Exception as e:
    WeightedRanker = None
    RRFRanker = None


# 【中文研读】类型职责：决定是否检查并创建索引的配置枚举，不代表业务投影已通过 ready 核验。
class IndexManagement(Enum):
    """Enumeration representing the supported index management operations."""

    NO_VALIDATION = "no_validation"
    CREATE_IF_NOT_EXISTS = "create_if_not_exists"


# 【中文研读】方法职责：将标准过滤与 Milvus 标量过滤合成表达式
# 【中文研读】输入参数：standard_filters（框架通用过滤条件）；scalar_filters（Milvus 专有标量条件）。
# 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
def _to_milvus_filter(
    standard_filters: MetadataFilters, scalar_filters: ScalarMetadataFilters = None
) -> str:
    """Translate metadata filters to Milvus specific spec."""
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


# 【中文研读】方法职责：上游非主线索引度量辅助函数
# 【中文研读】输入参数：func。
# 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
def _get_index_metric_type(
    func: Union[BaseSparseEmbeddingFunction, BaseMilvusBuiltInFunction],
):
    # 【中文研读】处理流程：依据函数类型选择对应度量，保留实现供来源比对，P3 研读稠密主线可跳过。
    if isinstance(func, BM25BuiltInFunction):
        return "BM25"
    else:
        return "IP"


similarity_metrics_map = {
    "ip": "IP",
    "l2": "L2",
    "euclidean": "L2",
    "cosine": "COSINE",
}


# 【中文研读】类型职责：将 LlamaIndex 的节点与向量接口映射到 pymilvus；构造器可能建库或删集合，方法会产生真实数据库副作用。
class MilvusVectorStore(BasePydanticVectorStore):
    """
    The Milvus Vector Store.

    In this vector store we store the text, its embedding and
    a its metadata in a Milvus collection. This implementation
    allows the use of an already existing collection.
    It also supports creating a new one if the collection doesn't
    exist or if `overwrite` is set to True.

    Args:
        uri (str): The URI to connect to, comes in the form of
            "https://address:port" for Milvus or Zilliz Cloud service,
            or "path/to/local/milvus.db" for the lite local Milvus. Defaults to
            "./milvus_llamaindex.db".
        token (str): The token for log in. Empty if not using rbac, if
            using rbac it will most likely be "username:password". Defaults to "".
        collection_name (str): The name of the collection where data will be
            stored. Defaults to "llamalection".
        overwrite (bool, optional): Whether to overwrite existing collection with same
            name. Defaults to False.
        upsert_mode (bool, optional): Whether to upsert documents into existing collection with same node id. Defaults to False.
        doc_id_field (str, optional): The name of the doc_id field for the collection,
            defaults to DEFAULT_DOC_ID_KEY.
        text_key (str, optional): What key text is stored in in the passed collection.
            Used when bringing your own collection. Defaults to DEFAULT_TEXT_KEY.
        scalar_field_names (list, optional): The names of the extra scalar fields to be included in the collection schema.
        scalar_field_types (list, optional): The types of the extra scalar fields.
        enable_dense (bool): A boolean flag to enable or disable dense embedding. Defaults to True.
        dim (int, optional): The dimension of the embedding vectors for the collection.
            Required when creating a new collection with enable_sparse is False.
        embedding_field (str, optional): The name of the dense embedding field for the
            collection, defaults to DEFAULT_EMBEDDING_KEY.
        index_config (dict, optional): The configuration used for building the
            dense embedding index. Defaults to None.
        search_config (dict, optional): The configuration used for searching
            the Milvus dense index. Note that this must be compatible with the index
            type specified by `index_config`. Defaults to None.
        similarity_metric (str, optional): The similarity metric to use for dense embedding,
            currently supports IP, COSINE and L2.
        enable_sparse (bool): A boolean flag to enable or disable sparse embedding. Defaults to False.
        sparse_embedding_field (str): The name of sparse embedding field, defaults to DEFAULT_SPARSE_EMBEDDING_KEY.
        sparse_embedding_function (Union[BaseSparseEmbeddingFunction, BaseMilvusBuiltInFunction], optional):
            If enable_sparse is True, this object should be provided to convert text to a sparse embedding.
            Defaults to None, which uses BM25 as the default sparse embedding function,
            or BGEM3 given existing collection without built-in functions.
        sparse_index_config (dict, optional): The configuration used to build the sparse embedding index.
            Defaults to None.
        collection_properties (dict, optional): The collection properties such as TTL
            (Time-To-Live) and MMAP (memory mapping). Defaults to None.
            It could include:
            - 'collection.ttl.seconds' (int): Once this property is set, data in the
                current collection expires in the specified time. Expired data in the
                collection will be cleaned up and will not be involved in searches or queries.
            - 'mmap.enabled' (bool): Whether to enable memory-mapped storage at the collection level.
        index_management (IndexManagement): Specifies the index management strategy to use. Defaults to "create_if_not_exists".
        batch_size (int): Configures the number of documents processed in one
            batch when inserting data into Milvus. Defaults to DEFAULT_BATCH_SIZE.
        consistency_level (str, optional): Which consistency level to use for a newly
            created collection. Defaults to "Session".
        hybrid_ranker (str): Specifies the type of ranker used in hybrid search queries.
            Currently only supports ['RRFRanker','WeightedRanker']. Defaults to "RRFRanker".
        hybrid_ranker_params (dict, optional): Configuration parameters for the hybrid ranker.
            The structure of this dictionary depends on the specific ranker being used:
            - For "RRFRanker", it should include:
                - 'k' (int): A parameter used in Reciprocal Rank Fusion (RRF). This value is used
                             to calculate the rank scores as part of the RRF algorithm, which combines
                             multiple ranking strategies into a single score to improve search relevance.
            - For "WeightedRanker", it expects:
                - 'weights' (list of float): A list of exactly two weights:
                     1. The weight for the dense embedding component.
                     2. The weight for the sparse embedding component.
                  These weights are used to adjust the importance of the dense and sparse components of the embeddings
                  in the hybrid retrieval process.
            Defaults to an empty dictionary, implying that the ranker will operate with its predefined default settings.
        use_asyc_client (bool, optional): Whether or not to set the async client. Setting this to `True` outside of asynchronous environments will cause errors.

    Raises:
        ImportError: Unable to import `pymilvus`.
        MilvusException: Error communicating with Milvus, more can be found in logging
            under Debug.

    Returns:
        MilvusVectorstore: Vectorstore that supports add, delete, and query.

    Examples:
        `pip install llama-index-vector-stores-milvus`

        ```python
        from llama_index.vector_stores.milvus import MilvusVectorStore

        # Setup MilvusVectorStore
        vector_store = MilvusVectorStore(
            dim=1536,
            collection_name="your_collection_name",
            uri="http://milvus_address:port",
            token="your_milvus_token_here",
            overwrite=True
        )
        ```

    """

    stores_text: bool = True
    stores_node: bool = True

    # 【中文研读】连接位置；构造器默认值也可指本地 Milvus 数据库，不能把默认参数当已部署远端服务。
    uri: str = "./milvus_llamaindex.db"
    # 【中文研读】连接凭证，日志和研读样例都不应输出真实值。
    token: str = ""
    # 【中文研读】向量集合名称，类似表级组织；业务租户隔离需额外范围约束。
    collection_name: str = "llamacollection"
    # 【中文研读】集合向量维度，必须与模型输出一致；维度相同仍不证明模型空间相同。
    dim: Optional[int]
    embedding_field: str = DEFAULT_EMBEDDING_KEY
    doc_id_field: str = DEFAULT_DOC_ID_KEY
    similarity_metric: str = "IP"
    # 【中文研读】数据库查询一致性配置，影响写后可见性；不替代 Remember ready 核验。
    consistency_level: str = "Session"
    # 【中文研读】启用会在构造时删除同名已有集合；不是普通更新参数。
    overwrite: bool = False
    # 【中文研读】决定 add 调用 insert 还是 upsert；不等同请求业务幂等已实现。
    upsert_mode: bool = False
    text_key: str = DEFAULT_TEXT_KEY
    output_fields: List[str] = Field(default_factory=list)
    index_config: Optional[dict]
    sparse_index_config: Optional[dict]
    search_config: Optional[dict]
    collection_properties: Optional[dict]
    # 【中文研读】一次提交的记录批量；跨多个批次的完成不构成整体原子提交。
    batch_size: int = DEFAULT_BATCH_SIZE
    enable_dense: bool = True
    enable_sparse: bool = False
    sparse_embedding_field: str = DEFAULT_SPARSE_EMBEDDING_KEY
    sparse_embedding_function: Optional[
        Union[BaseMilvusBuiltInFunction, BaseSparseEmbeddingFunction]
    ]
    hybrid_ranker: str
    hybrid_ranker_params: dict = {}
    index_management: IndexManagement = IndexManagement.CREATE_IF_NOT_EXISTS
    scalar_field_names: Optional[List[str]]
    scalar_field_types: Optional[List[DataType]]
    use_async_client: bool = True

    _milvusclient: MilvusClient = PrivateAttr()
    _async_milvusclient: Optional[AsyncMilvusClient] = PrivateAttr()
    _collection_initialized: bool = PrivateAttr(default=False)

    # 【中文研读】方法职责：建立客户端并准备集合
    # 【中文研读】输入参数：uri（数据库连接位置）；token（连接凭证）；collection_name（向量集合名称）；overwrite（是否删除并重建已有集合）；upsert_mode（是否使用覆盖式写入）；collection_properties（集合级配置）；doc_id_field（来源文档 ID 字段名）；text_key（正文存储字段名）；scalar_field_names（额外标量字段名称）；scalar_field_types（对应标量字段类型）；enable_dense（是否启用稠密向量字段）；dim（向量维度）；embedding_field（向量字段名）；enable_sparse；sparse_embedding_field；sparse_embedding_function；index_management（索引检查与创建策略）；batch_size（一次写入批量）；index_config（稠密索引参数）；sparse_index_config；search_config（搜索执行参数）；similarity_metric（向量距离或相似度度量）；consistency_level（查询一致性等级）；output_fields（数据库需返回的字段）；hybrid_ranker；hybrid_ranker_params；use_async_client（是否创建异步数据库客户端）；**kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def __init__(
        self,
        uri: str = "./milvus_llamaindex.db",
        token: str = "",
        collection_name: str = "llamacollection",
        overwrite: bool = False,
        upsert_mode: bool = False,
        collection_properties: Optional[dict] = None,
        doc_id_field: str = DEFAULT_DOC_ID_KEY,
        text_key: str = DEFAULT_TEXT_KEY,
        scalar_field_names: Optional[List[str]] = None,
        scalar_field_types: Optional[List[DataType]] = None,
        enable_dense: bool = True,
        dim: Optional[int] = None,
        embedding_field: str = DEFAULT_EMBEDDING_KEY,
        enable_sparse: bool = False,
        sparse_embedding_field: str = DEFAULT_SPARSE_EMBEDDING_KEY,
        sparse_embedding_function: Optional[BaseSparseEmbeddingFunction] = None,
        index_management: IndexManagement = IndexManagement.CREATE_IF_NOT_EXISTS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        index_config: Optional[dict] = None,
        sparse_index_config: Optional[dict] = None,
        search_config: Optional[dict] = None,
        similarity_metric: str = "IP",
        consistency_level: str = "Session",
        output_fields: Optional[List[str]] = None,
        hybrid_ranker: str = "RRFRanker",
        hybrid_ranker_params: dict = {},
        use_async_client: bool = True,
        **kwargs: Any,
    ) -> None:
        """Init params."""
        # 【中文研读】处理流程：保存参数 → 同步/可选异步客户端 → 处理 overwrite → 检查现有集合 → 缺失则建 Schema 与索引 → 设置集合属性。
        super().__init__(
            collection_name=collection_name,
            enable_dense=enable_dense,
            dim=dim,
            embedding_field=embedding_field,
            doc_id_field=doc_id_field,
            consistency_level=consistency_level,
            overwrite=overwrite,
            upsert_mode=upsert_mode,
            text_key=text_key,
            output_fields=output_fields or [],
            index_config=index_config if index_config else {},
            search_config=search_config if search_config else {},
            collection_properties=collection_properties,
            batch_size=batch_size,
            enable_sparse=enable_sparse,
            sparse_embedding_field=sparse_embedding_field,
            sparse_embedding_function=sparse_embedding_function,
            sparse_index_config=sparse_index_config if sparse_index_config else {},
            hybrid_ranker=hybrid_ranker,
            hybrid_ranker_params=hybrid_ranker_params,
            index_management=index_management,
            scalar_field_names=scalar_field_names,
            scalar_field_types=scalar_field_types,
            use_async_client=use_async_client,
        )
        # Connect to Milvus instance
        # 【中文研读】真实连接适配从这里开始；安装客户端包本身不会自动部署远端 Milvus。
        self._milvusclient = MilvusClient(
            uri=uri,
            token=token,
            **kwargs,  # pass additional arguments such as server_pem_path
        )

        # As of writing, milvus sets alias internally in the async client.
        # This will cause an error if not removed.
        kwargs.pop("alias", None)
        if use_async_client:
            self._async_milvusclient = AsyncMilvusClient(
                uri=uri,
                token=token,
                **kwargs,  # pass additional arguments such as server_pem_path
            )
        else:
            self._async_milvusclient = None

        # Delete previous collection if overwriting
        # 【中文研读】构造对象也可能删数据；研读时不要把初始化默认视为只读操作，P3 不应把此开关暴露给普通用户。
        if overwrite and collection_name in self.client.list_collections():
            self.client.drop_collection(collection_name)

        # Check if the collection exists
        if collection_name in self.client.list_collections():
            self._collection_initialized = True
            self._create_index_if_required()
        else:
            self._collection_initialized = False

        # Set default args
        self.similarity_metric = similarity_metrics_map.get(
            similarity_metric.lower(), "L2"
        )
        if self.enable_dense and self.embedding_field is None:
            logger.warning("Dense embedding field name is not provided, using default.")
            self.embedding_field = DEFAULT_EMBEDDING_KEY
        if self.enable_sparse:
            if self.sparse_embedding_field is None:
                logger.warning(
                    "Sparse embedding field name is not provided, using default."
                )
                self.sparse_embedding_field = DEFAULT_SPARSE_EMBEDDING_KEY
            if self.sparse_embedding_function is None:
                logger.warning(
                    "Sparse embedding function is not provided, using default."
                )
                collection_info = (
                    self.client.describe_collection(collection_name)
                    if self._collection_initialized
                    else None
                )
                self.sparse_embedding_function = get_default_sparse_embedding_function(
                    input_field_names=self.text_key,
                    output_field_names=self.sparse_embedding_field,
                    collection_info=collection_info,
                )

        # Create the collection & index if it does not exist
        # 【中文研读】没有集合时创建 Schema 和索引；这可能需要服务端权限，并产生持久副作用。
        if not self._collection_initialized:
            # Prepare schema
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=True)
            schema = self._add_fields_to_schema(schema)  # add fields
            schema = self._add_functions_to_schema(schema)  # add functions
            schema.verify()  # check schema

            # Prepare index
            index_params = self.client.prepare_index_params()
            if self.index_management is not IndexManagement.NO_VALIDATION:
                if self.enable_dense:
                    index_params = self._add_dense_index_params(index_params)
                if self.enable_sparse:
                    index_params = self._add_sparse_index_params(index_params)

            # Create collection
            self.client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
                index_params=index_params,
                consistency_level=self.consistency_level,
            )
            logger.debug(
                f"Successfully created a new collection: {self.collection_name}"
            )
            self._collection_initialized = True

        # Set properties
        if collection_properties:
            if self.client.get_load_state(collection_name) == LoadState.Loaded:
                self.client.release_collection(collection_name)
                self.client.alter_collection_properties(
                    collection_name=collection_name,
                    properties=collection_properties,
                )
                self.client.load_collection(collection_name)
            else:
                self.client.alter_collection_properties(
                    collection_name=collection_name,
                    properties=collection_properties,
                )

        logger.debug(
            f"Successfully set properties for collection: {self.collection_name}"
        )

    # 【中文研读】方法职责：读取并返回 'MilvusVectorStore'；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def class_name(cls) -> str:
        """Class name."""
        # 【中文研读】处理流程：读取并返回 'MilvusVectorStore'；此入口不发起检索或存储写入。
        return "MilvusVectorStore"

    # 【中文研读】方法职责：读取并返回 self._milvusclient；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：MilvusClient。结果的业务含义与失败分支见下面处理流程。
    @property
    def client(self) -> MilvusClient:
        """Get client."""
        # 【中文研读】处理流程：读取并返回 self._milvusclient；此入口不发起检索或存储写入。
        return self._milvusclient

    # 【中文研读】方法职责：读取并返回 self._async_milvusclient；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Optional[AsyncMilvusClient]。结果的业务含义与失败分支见下面处理流程。
    @property
    def aclient(self) -> Optional[AsyncMilvusClient]:
        """Get async client."""
        # 【中文研读】处理流程：读取并返回 self._async_milvusclient；此入口不发起检索或存储写入。
        return self._async_milvusclient

    # 【中文研读】方法职责：将节点分批写入 Milvus
    # 【中文研读】输入参数：nodes（候选节点列表）；**add_kwargs（透传选项）。
    # 【中文研读】返回约定：List[str]。结果的业务含义与失败分支见下面处理流程。
    def add(self, nodes: List[BaseNode], **add_kwargs: Any) -> List[str]:
        """
        Add the embeddings and their nodes into Milvus.

        Args:
            nodes (List[BaseNode]): List of nodes with embeddings
                to insert.
            **add_kwargs (Any): Additional keyword arguments.
                - `milvus_partition_name` (Optional[str]): Specific Milvus partition.

        Raises:
            MilvusException: Failed to insert data.

        Returns:
            List[str]: List of ids inserted.

        """
        # 【中文研读】处理流程：节点转元信息与正文，附 ID 和已有向量，选择 insert/upsert，逐批调用，可选 flush，返回节点 IDs。
        insert_list = []
        insert_ids = []

        if self.enable_sparse is True and self.sparse_embedding_function is None:
            logger.fatal(
                "sparse_embedding_function is None when enable_sparse is True."
            )

        # Process that data we are going to insert
        # 【中文研读】逐节点转换数据：向量已在 node 上，不是在稠密写入分支中调用 BGE 生成。
        for node in nodes:
            entry = node_to_metadata_dict(
                node, remove_text=True, text_field=self.text_key
            )
            entry[self.text_key] = node.dict()[self.text_key]
            entry[MILVUS_ID_FIELD] = node.node_id
            if self.enable_dense:
                # 【中文研读】将预先生成的稠密向量写入约定字段；P3 适配器仍需提前检查维度、数值与模型空间。
                entry[self.embedding_field] = node.embedding
            if self.enable_sparse:
                if isinstance(
                    self.sparse_embedding_function, BaseSparseEmbeddingFunction
                ):
                    entry[self.sparse_embedding_field] = (
                        self.sparse_embedding_function.encode_documents([node.text])[0]
                    )
                else:  # BaseMilvusBuiltInFunction
                    pass

            insert_ids.append(node.node_id)
            insert_list.append(entry)

        # 【中文研读】明确区分插入与覆盖更新；框架选了 upsert 不代表授权、版本 CAS 或删除屏障已经具备。
        if self.upsert_mode:
            executor_wrapper = self.client.upsert
        else:
            executor_wrapper = self.client.insert

        # Insert or Upsert the data into milvus
        # 【中文研读】分批调用：前一批成功、后一批失败时可能部分生效，恢复不能假定所有记录一起回滚。
        for insert_batch in iter_batch(insert_list, self.batch_size):
            executor_wrapper(
                self.collection_name,
                insert_batch,
                partition_name=add_kwargs.get("milvus_partition_name"),
            )
        # 【中文研读】检查刷盘选项；同步路径可调用 flush，异步路径此时已写入再抛不支持错误，不能把异常等同完全未生效。
        if add_kwargs.get("force_flush", False):
            self.client.flush(self.collection_name)
        logger.debug(
            f"Successfully inserted embeddings into: {self.collection_name} "
            f"Num Inserted: {len(insert_list)}"
        )
        return insert_ids

    # 【中文研读】方法职责：异步分批写入节点
    # 【中文研读】输入参数：nodes（候选节点列表）；**add_kwargs（透传选项）。
    # 【中文研读】返回约定：List[str]。结果的业务含义与失败分支见下面处理流程。
    async def async_add(
        self,
        nodes: List[BaseNode],
        **add_kwargs: Any,
    ) -> List[str]:
        """Asynchronous version of the add method."""
        # 【中文研读】处理流程：要求异步客户端，组装同样实体后逐批 await；此快照不支持异步 force_flush，且检查发生在写入后。
        assert self._async_milvusclient is not None, (
            "Async Client should be non-null to perform async operations. Pass `use_async_client = True` to the constructor to instantiate an async client"
        )
        insert_list = []
        insert_ids = []

        if self.enable_sparse is True and self.sparse_embedding_function is None:
            logger.fatal(
                "sparse_embedding_function is None when enable_sparse is True."
            )

        # Process that data we are going to insert
        # 【中文研读】逐节点转换数据：向量已在 node 上，不是在稠密写入分支中调用 BGE 生成。
        for node in nodes:
            entry = node_to_metadata_dict(
                node, remove_text=True, text_field=self.text_key
            )
            entry[self.text_key] = node.dict()[self.text_key]
            entry[MILVUS_ID_FIELD] = node.node_id
            if self.enable_dense:
                # 【中文研读】将预先生成的稠密向量写入约定字段；P3 适配器仍需提前检查维度、数值与模型空间。
                entry[self.embedding_field] = node.embedding
            if self.enable_sparse:
                if isinstance(
                    self.sparse_embedding_function, BaseSparseEmbeddingFunction
                ):
                    entry[self.sparse_embedding_field] = (
                        self.sparse_embedding_function.encode_documents([node.text])[0]
                    )
                else:  # BaseMilvusBuiltInFunction
                    pass

            insert_ids.append(node.node_id)
            insert_list.append(entry)

        # 【中文研读】明确区分插入与覆盖更新；框架选了 upsert 不代表授权、版本 CAS 或删除屏障已经具备。
        if self.upsert_mode:
            executor_wrapper = self.aclient.upsert
        else:
            executor_wrapper = self.aclient.insert

        # Insert or Upsert the data into milvus
        # 【中文研读】分批调用：前一批成功、后一批失败时可能部分生效，恢复不能假定所有记录一起回滚。
        for insert_batch in iter_batch(insert_list, self.batch_size):
            await executor_wrapper(
                self.collection_name,
                insert_batch,
                partition_name=add_kwargs.get("milvus_partition_name"),
            )
        # 【中文研读】检查刷盘选项；同步路径可调用 flush，异步路径此时已写入再抛不支持错误，不能把异常等同完全未生效。
        if add_kwargs.get("force_flush", False):
            raise NotImplementedError("force_flush is not supported in async mode.")
            # await self.aclient.flush(self.collection_name)
        logger.debug(
            f"Successfully inserted embeddings into: {self.collection_name} "
            f"Num Inserted: {len(insert_list)}"
        )
        return insert_ids

    # 【中文研读】方法职责：按来源文档 ID 找到向量记录再删除
    # 【中文研读】输入参数：ref_doc_id（来源文档 ID）；**delete_kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def delete(self, ref_doc_id: str, **delete_kwargs: Any) -> None:
        """
        Delete nodes using with ref_doc_id.

        Args:
            ref_doc_id (str): The doc_id of the document to delete.
            **delete_kwargs (Any): Additional keyword arguments.
                - `milvus_partition_name` (Optional[str]): Specific Milvus partition.

        Raises:
            MilvusException: Failed to delete the doc.

        """
        # Adds ability for multiple doc delete in future.
        # 【中文研读】处理流程：规范为 ID 列表，查询匹配主键，有匹配才调用 delete；两次数据库操作不是一个 P3 事务。
        doc_ids: List[str]
        if isinstance(ref_doc_id, list):
            doc_ids = ref_doc_id  # type: ignore
        else:
            doc_ids = [ref_doc_id]

        # Begin by querying for the primary keys to delete
        doc_ids = ['"' + entry + '"' for entry in doc_ids]
        entries = self.client.query(
            collection_name=self.collection_name,
            filter=f"{self.doc_id_field} in [{','.join(doc_ids)}]",
        )
        if len(entries) > 0:
            ids = [entry["id"] for entry in entries]
            self.client.delete(
                collection_name=self.collection_name,
                pks=ids,
                partition_name=delete_kwargs.get("milvus_partition_name"),
            )
            logger.debug(f"Successfully deleted embedding with doc_id: {doc_ids}")

    # 【中文研读】方法职责：异步执行按文档删除
    # 【中文研读】输入参数：ref_doc_id（来源文档 ID）；**delete_kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    async def adelete(self, ref_doc_id: str, **delete_kwargs: Any) -> None:
        """Asynchronous version of the delete method."""
        # 【中文研读】处理流程：await 查询记录主键，存在匹配后 await 删除，不能自动恢复并发版本竞态。
        assert self._async_milvusclient is not None, (
            "Async Client should be non-null to perform async operations. Pass `use_async_client = True` to the constructor to instantiate an async client"
        )
        # Adds ability for multiple doc delete in future.
        doc_ids: List[str]
        if isinstance(ref_doc_id, list):
            doc_ids = ref_doc_id  # type: ignore
        else:
            doc_ids = [ref_doc_id]

        # Begin by querying for the primary keys to delete
        doc_ids = ['"' + entry + '"' for entry in doc_ids]
        entries = await self.aclient.query(
            collection_name=self.collection_name,
            filter=f"{self.doc_id_field} in [{','.join(doc_ids)}]",
        )
        if len(entries) > 0:
            ids = [entry["id"] for entry in entries]
            await self.aclient.delete(
                collection_name=self.collection_name,
                pks=ids,
                partition_name=delete_kwargs.get("milvus_partition_name"),
            )
            logger.debug(f"Successfully deleted embedding with doc_id: {doc_ids}")

    # 【中文研读】方法职责：按节点 IDs 和过滤树删除
    # 【中文研读】输入参数：node_ids（限制或读取的节点 ID 集合）；filters（结构化过滤条件集合）；**delete_kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def delete_nodes(
        self,
        node_ids: Optional[List[str]] = None,
        filters: Optional[MetadataFilters] = None,
        **delete_kwargs: Any,
    ) -> None:
        """
        Deletes nodes.

        Args:
            node_ids (Optional[List[str]], optional): IDs of nodes to delete. Defaults to None.
            filters (Optional[MetadataFilters], optional): Metadata filters. Defaults to None.
            **delete_kwargs (Any): Additional keyword arguments.
                - `milvus_partition_name` (Optional[str]): Specific Milvus partition.

        """
        # 【中文研读】处理流程：深拷贝过滤条件，追加 ID 约束，转换表达式后调用数据库；调用方必须防止未经授权的宽范围删除。
        # 【中文研读】复制过滤树再追加条件，避免修改调用方后续复用的对象。
        filters_cpy = deepcopy(filters) or MetadataFilters(filters=[])

        if node_ids:
            filters_cpy.filters.append(
                MetadataFilter(key="id", value=node_ids, operator=FilterOperator.IN)
            )

        if filters_cpy is not None:
            filter = _to_milvus_filter(filters_cpy)
        else:
            filter = None

        self.client.delete(
            collection_name=self.collection_name,
            filter=filter,
            partition_name=delete_kwargs.get("milvus_partition_name"),
            **delete_kwargs,
        )
        logger.debug(f"Successfully deleted node_ids: {node_ids}")

    # 【中文研读】方法职责：异步按节点/过滤条件删除
    # 【中文研读】输入参数：node_ids（限制或读取的节点 ID 集合）；filters（结构化过滤条件集合）；**delete_kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    async def adelete_nodes(
        self,
        node_ids: Optional[List[str]] = None,
        filters: Optional[MetadataFilters] = None,
        **delete_kwargs: Any,
    ) -> None:
        """Asynchronous version of the delete_nodes method."""
        # 【中文研读】处理流程：同样组装表达式后 await 数据库，范围安全仍由业务层保证。
        assert self._async_milvusclient is not None, (
            "Async Client should be non-null to perform async operations. Pass `use_async_client = True` to the constructor to instantiate an async client"
        )
        # 【中文研读】复制过滤树再追加条件，避免修改调用方后续复用的对象。
        filters_cpy = deepcopy(filters) or MetadataFilters(filters=[])

        if node_ids:
            filters_cpy.filters.append(
                MetadataFilter(key="id", value=node_ids, operator=FilterOperator.IN)
            )

        if filters_cpy is not None:
            filter = _to_milvus_filter(filters_cpy)
        else:
            filter = None

        await self.aclient.delete(
            collection_name=self.collection_name,
            filter=filter,
            partition_name=delete_kwargs.get("milvus_partition_name"),
            **delete_kwargs,
        )
        logger.debug(f"Successfully deleted node_ids: {node_ids}")

    # 【中文研读】方法职责：删除整个 collection
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def clear(self) -> None:
        """Clears db."""
        # 【中文研读】处理流程：直接 drop_collection，不是只清空某个用户的记忆。
        self.client.drop_collection(self.collection_name)

    # 【中文研读】方法职责：异步删除整个 collection
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    async def aclear(self) -> None:
        """Asynchronous version of the clear method."""
        # 【中文研读】处理流程：确认异步客户端存在，再执行 drop_collection。
        assert self._async_milvusclient is not None, (
            "Async Client should be non-null to perform async operations. Pass `use_async_client = True` to the constructor to instantiate an async client"
        )
        await self.aclient.drop_collection(self.collection_name)

    # 【中文研读】方法职责：按 ID 或过滤条件读取节点
    # 【中文研读】输入参数：node_ids（限制或读取的节点 ID 集合）；filters（结构化过滤条件集合）；**kwargs（透传选项）。
    # 【中文研读】返回约定：List[BaseNode]。结果的业务含义与失败分支见下面处理流程。
    def get_nodes(
        self,
        node_ids: Optional[List[str]] = None,
        filters: Optional[MetadataFilters] = None,
        **kwargs,
    ) -> List[BaseNode]:
        """
        Get nodes by node ids or metadata filters.

        Args:
            node_ids (Optional[List[str]], optional): IDs of nodes to retrieve. Defaults to None.
            filters (Optional[MetadataFilters], optional): Metadata filters. Defaults to None.
            **kwargs: Additional keyword arguments.
                - `milvus_partition_names` (Optional[List[str]]): Specific Milvus partitions.

        Raises:
            ValueError: Neither or both of node_ids and filters are provided.

        Returns:
            List[BaseNode]:

        """
        # 【中文研读】处理流程：限制查询条件组合，读取实体，优先恢复序列化节点，否则用正文构造 TextNode，最后补向量。
        # 【中文研读】精确读取要求至少一个定位条件，避免无意把全部集合当成单次读取。
        if node_ids is None and filters is None:
            raise ValueError("Either node_ids or filters must be provided.")

        # 【中文研读】复制过滤树再追加条件，避免修改调用方后续复用的对象。
        filters_cpy = deepcopy(filters) or MetadataFilters(filters=[])
        milvus_filter = _to_milvus_filter(filters_cpy)

        # 【中文研读】此读取实现不支持 ID 与过滤同时使用；P3 适配须验证权限检查如何补足，不能默默忽略范围。
        if node_ids is not None and milvus_filter:
            raise ValueError("Only one of node_ids or filters can be provided.")

        res = self.client.query(
            ids=node_ids,
            collection_name=self.collection_name,
            filter=milvus_filter,
            partition_names=kwargs.get("milvus_partition_names"),
        )

        nodes = []
        for item in res:
            try:
                text_content = item.get(self.text_key)
            except Exception:
                raise ValueError(
                    "The passed in text_key value does not exist "
                    "in the retrieved entity."
                )
            if "_node_content" in item:
                node = metadata_dict_to_node(item, text=text_content)
            elif text_content:
                node = TextNode(
                    text=text_content,
                    metadata={key: item.get(key) for key in self.output_fields},
                )
            else:
                raise ValueError(
                    "Node content not found in metadata dict and no text content found."
                )
            node.embedding = item.get(self.embedding_field, None)
            nodes.append(node)
        return nodes

    # 【中文研读】方法职责：异步精确读取
    # 【中文研读】输入参数：node_ids（限制或读取的节点 ID 集合）；filters（结构化过滤条件集合）；**kwargs（透传选项）。
    # 【中文研读】返回约定：List[BaseNode]。结果的业务含义与失败分支见下面处理流程。
    async def aget_nodes(
        self,
        node_ids: Optional[List[str]] = None,
        filters: Optional[MetadataFilters] = None,
        **kwargs,
    ) -> List[BaseNode]:
        """Asynchronous version of the get_nodes method."""
        # 【中文研读】处理流程：await 获取实体，按同样规则恢复节点内容及可选向量，不自动核对 P3 当前版本。
        assert self._async_milvusclient is not None, (
            "Async Client should be non-null to perform async operations. Pass `use_async_client = True` to the constructor to instantiate an async client"
        )
        # 【中文研读】精确读取要求至少一个定位条件，避免无意把全部集合当成单次读取。
        if node_ids is None and filters is None:
            raise ValueError("Either node_ids or filters must be provided.")

        # 【中文研读】复制过滤树再追加条件，避免修改调用方后续复用的对象。
        filters_cpy = deepcopy(filters) or MetadataFilters(filters=[])
        milvus_filter = _to_milvus_filter(filters_cpy)

        # 【中文研读】此读取实现不支持 ID 与过滤同时使用；P3 适配须验证权限检查如何补足，不能默默忽略范围。
        if node_ids is not None and milvus_filter:
            raise ValueError("Only one of node_ids or filters can be provided.")

        res = await self.aclient.query(
            ids=node_ids,
            collection_name=self.collection_name,
            filter=milvus_filter,
            partition_names=kwargs.get("milvus_partition_names"),
        )

        nodes = []
        for item in res:
            try:
                text_content = item.get(self.text_key)
            except Exception:
                raise ValueError(
                    "The passed in text_key value does not exist "
                    "in the retrieved entity."
                )
            if "_node_content" in item:
                node = metadata_dict_to_node(item, text=text_content)
            elif text_content:
                node = TextNode(
                    text=text_content,
                    metadata={key: item.get(key) for key in self.output_fields},
                )
            else:
                raise ValueError(
                    "Node content not found in metadata dict and no text content found."
                )
            node.embedding = item.get(self.embedding_field, None)
            nodes.append(node)
        return nodes

    # 【中文研读】方法职责：同步查询总入口
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；**kwargs（透传选项）。
    # 【中文研读】返回约定：VectorStoreQueryResult。结果的业务含义与失败分支见下面处理流程。
    def query(self, query: VectorStoreQuery, **kwargs: Any) -> VectorStoreQueryResult:
        """
        Query index for top k most similar nodes.

        Args:
            query_embedding (List[float]): query embedding
            similarity_top_k (int): top k most similar nodes
            doc_ids (Optional[List[str]]): list of doc_ids to filter by
            node_ids (Optional[List[str]]): list of node_ids to filter by
            output_fields (Optional[List[str]]): list of fields to return
            embedding_field (Optional[str]): name of embedding field
            milvus_partition_names (Optional[List[str]]): specific Milvus partitions.

        """
        # 【中文研读】处理流程：验证模式 → 构造过滤和输出字段 → 选择检索实现 → 封装节点/分数/IDs；P3 使用 _default_search 路径。
        if query.mode == VectorStoreQueryMode.DEFAULT:
            pass
        elif query.mode in [
            VectorStoreQueryMode.HYBRID,
            VectorStoreQueryMode.SPARSE,
            VectorStoreQueryMode.TEXT_SEARCH,
        ]:
            if self.enable_sparse is False:
                raise ValueError(
                    f"The query mode requires sparse embedding, but enable_sparse is False."
                )
        elif query.mode == VectorStoreQueryMode.MMR:
            pass
        else:
            raise ValueError(f"Milvus does not support {query.mode} yet.")

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

        # Perform the search
        if query.mode == VectorStoreQueryMode.MMR:
            nodes, similarities, ids = self._mmr_search(
                query, string_expr, output_fields, **kwargs
            )
        elif query.mode in [
            VectorStoreQueryMode.SPARSE,
            VectorStoreQueryMode.TEXT_SEARCH,
        ]:
            nodes, similarities, ids = self._sparse_search(
                query, string_expr, output_fields, **kwargs
            )
        elif query.mode == VectorStoreQueryMode.HYBRID:
            nodes, similarities, ids = self._hybrid_search(
                query, string_expr, output_fields, **kwargs
            )
        else:
            nodes, similarities, ids = self._default_search(
                query, string_expr, output_fields, **kwargs
            )
        return VectorStoreQueryResult(nodes=nodes, similarities=similarities, ids=ids)

    # 【中文研读】方法职责：异步查询总入口
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；**kwargs（透传选项）。
    # 【中文研读】返回约定：VectorStoreQueryResult。结果的业务含义与失败分支见下面处理流程。
    async def aquery(
        self, query: VectorStoreQuery, **kwargs: Any
    ) -> VectorStoreQueryResult:
        """Asynchronous version of the query method."""
        # 【中文研读】处理流程：检查异步客户端，复用过滤规则，等待相应检索实现后统一封装结果。
        assert self._async_milvusclient is not None, (
            "Async Client should be non-null to perform async operations. Pass `use_async_client = True` to the constructor to instantiate an async client"
        )
        if query.mode == VectorStoreQueryMode.DEFAULT:
            pass
        elif query.mode in [
            VectorStoreQueryMode.HYBRID,
            VectorStoreQueryMode.SPARSE,
            VectorStoreQueryMode.TEXT_SEARCH,
        ]:
            if self.enable_sparse is False:
                raise ValueError(
                    f"The query mode requires sparse embedding, but enable_sparse is False."
                )
        elif query.mode == VectorStoreQueryMode.MMR:
            pass
        else:
            raise ValueError(f"Milvus does not support {query.mode} yet.")

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

        # Perform the search
        if query.mode == VectorStoreQueryMode.MMR:
            nodes, similarities, ids = await self._async_mmr_search(
                query, string_expr, output_fields, **kwargs
            )
        elif query.mode in [
            VectorStoreQueryMode.SPARSE,
            VectorStoreQueryMode.TEXT_SEARCH,
        ]:
            nodes, similarities, ids = await self._async_sparse_search(
                query, string_expr, output_fields, **kwargs
            )
        elif query.mode == VectorStoreQueryMode.HYBRID:
            nodes, similarities, ids = await self._async_hybrid_search(
                query, string_expr, output_fields, **kwargs
            )
        else:
            nodes, similarities, ids = await self._async_default_search(
                query, string_expr, output_fields, **kwargs
            )
        return VectorStoreQueryResult(nodes=nodes, similarities=similarities, ids=ids)

    # 【中文研读】方法职责：构造数据库条件和需返回字段
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Tuple[str, List[str]]。结果的业务含义与失败分支见下面处理流程。
    def _prepare_before_search(
        self, query: VectorStoreQuery, **kwargs
    ) -> Tuple[str, List[str]]:
        """
        Prepare the expression and output fields for search.
        """
        # 【中文研读】处理流程：元信息过滤、文档/节点 ID 约束用 AND 连接，返回字段不足时补正文键。
        expr = []
        output_fields = ["*"]
        # Parse the filter
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
        # Parse any docs we are filtering on
        # 【中文研读】将文档范围并入查询条件；原源码使用字符串拼接，正式接入应验证转义与输入范围。
        if query.doc_ids is not None and len(query.doc_ids) != 0:
            expr_list = ['"' + entry + '"' for entry in query.doc_ids]
            expr.append(f"{self.doc_id_field} in [{','.join(expr_list)}]")
        # Parse any nodes we are filtering on
        if query.node_ids is not None and len(query.node_ids) != 0:
            expr_list = ['"' + entry + '"' for entry in query.node_ids]
            expr.append(f"{MILVUS_ID_FIELD} in [{','.join(expr_list)}]")
        # Limit output fields
        outputs_limited = False
        if query.output_fields is not None:
            output_fields = query.output_fields
            outputs_limited = True
        elif len(self.output_fields) > 0:
            output_fields = [*self.output_fields]
            outputs_limited = True
        # Add the text key to output fields if necessary
        # 【中文研读】限制输出字段时仍保留正文键，否则命中无法还原为可读节点。
        if self.text_key not in output_fields and outputs_limited:
            output_fields.append(self.text_key)
        # Convert to string expression
        string_expr = ""
        if len(expr) != 0:
            string_expr = f" and ".join(expr)
        return string_expr, output_fields

    # 【中文研读】方法职责：执行稠密向量 Search
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；string_expr（转换后的数据库过滤表达式）；output_fields（数据库需返回的字段）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Tuple[List[BaseNode], List[float], List[str]]。结果的业务含义与失败分支见下面处理流程。
    def _default_search(
        self,
        query: VectorStoreQuery,
        string_expr: str,
        output_fields: List[str],
        **kwargs,
    ) -> Tuple[List[BaseNode], List[float], List[str]]:
        """
        Perform default search: dense embedding search.
        """
        # 【中文研读】处理流程：传入一条查询向量、过滤、TopK、向量字段与配置，解析第一组命中后返回三份对应列表。
        res = self.client.search(
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

    # 【中文研读】方法职责：通过异步客户端执行稠密 Search
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；string_expr（转换后的数据库过滤表达式）；output_fields（数据库需返回的字段）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Tuple[List[BaseNode], List[float], List[str]]。结果的业务含义与失败分支见下面处理流程。
    async def _async_default_search(
        self,
        query: VectorStoreQuery,
        string_expr: str,
        output_fields: List[str],
        **kwargs,
    ) -> Tuple[List[BaseNode], List[float], List[str]]:
        """
        Perform asynchronous default search.
        """
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

    # 【中文研读】方法职责：上游可选多样性选择路径
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；string_expr（转换后的数据库过滤表达式）；output_fields（数据库需返回的字段）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Tuple[List[BaseNode], List[float], List[str]]。结果的业务含义与失败分支见下面处理流程。
    def _mmr_search(
        self,
        query: VectorStoreQuery,
        string_expr: str,
        output_fields: List[str],
        **kwargs,
    ) -> Tuple[List[BaseNode], List[float], List[str]]:
        """
        Perform MMR search.
        """
        # 【中文研读】处理流程：多取候选及向量，在客户端平衡相关性和重复度后选 TopK；不属于当前必需接入。
        mmr_threshold = kwargs.get("mmr_threshold")
        if (
            kwargs.get("mmr_prefetch_factor") is not None
            and kwargs.get("mmr_prefetch_k") is not None
        ):
            raise ValueError(
                "'mmr_prefetch_factor' and 'mmr_prefetch_k' "
                "cannot coexist in a call to query()"
            )
        else:
            if kwargs.get("mmr_prefetch_k") is not None:
                prefetch_k0 = int(kwargs["mmr_prefetch_k"])
            else:
                prefetch_k0 = int(
                    query.similarity_top_k
                    * kwargs.get("mmr_prefetch_factor", DEFAULT_MMR_PREFETCH_FACTOR)
                )
        res = self.client.search(
            collection_name=self.collection_name,
            data=[query.query_embedding],
            filter=string_expr,
            limit=prefetch_k0,
            output_fields=output_fields,
            search_params=kwargs.get("milvus_search_config", self.search_config),
            anns_field=self.embedding_field,
            partition_names=kwargs.get("milvus_partition_names"),
        )
        nodes = res[0]
        node_embeddings = []
        node_ids = []
        # 【中文研读】逐节点转换数据：向量已在 node 上，不是在稠密写入分支中调用 BGE 生成。
        for node in nodes:
            node_embeddings.append(node["entity"]["embedding"])
            node_ids.append(node["id"])
        mmr_similarities, mmr_ids = get_top_k_mmr_embeddings(
            query_embedding=query.query_embedding,
            embeddings=node_embeddings,
            similarity_top_k=query.similarity_top_k,
            embedding_ids=node_ids,
            mmr_threshold=mmr_threshold,
        )
        node_dict = dict(list(zip(node_ids, nodes)))
        selected_nodes = [node_dict[id] for id in mmr_ids if id in node_dict]
        similarities = mmr_similarities  # Passing the MMR similarities instead of the original similarities
        ids = mmr_ids
        nodes, _, _ = self._parse_from_milvus_results([selected_nodes])
        logger.debug(
            f"Successfully performed MMR on embeddings in collection: {self.collection_name}"
        )
        return nodes, similarities, ids

    # 【中文研读】方法职责：异步取得多样性候选
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；string_expr（转换后的数据库过滤表达式）；output_fields（数据库需返回的字段）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Tuple[List[BaseNode], List[float], List[str]]。结果的业务含义与失败分支见下面处理流程。
    async def _async_mmr_search(
        self,
        query: VectorStoreQuery,
        string_expr: str,
        output_fields: List[str],
        **kwargs,
    ) -> Tuple[List[BaseNode], List[float], List[str]]:
        """
        Perform asynchronous MMR search.
        """
        # 【中文研读】处理流程：await 预取，随后本地执行 MMR 并按选中 ID 恢复节点；await 后的计算仍在当前执行流程中。
        assert self._async_milvusclient is not None, (
            "Async Client should be non-null to perform async operations. Pass `use_async_client = True` to the constructor to instantiate an async client"
        )
        mmr_threshold = kwargs.get("mmr_threshold")
        if (
            kwargs.get("mmr_prefetch_factor") is not None
            and kwargs.get("mmr_prefetch_k") is not None
        ):
            raise ValueError(
                "'mmr_prefetch_factor' and 'mmr_prefetch_k' "
                "cannot coexist in a call to query()"
            )
        else:
            if kwargs.get("mmr_prefetch_k") is not None:
                prefetch_k0 = int(kwargs["mmr_prefetch_k"])
            else:
                prefetch_k0 = int(
                    query.similarity_top_k
                    * kwargs.get("mmr_prefetch_factor", DEFAULT_MMR_PREFETCH_FACTOR)
                )

        res = await self.aclient.search(
            collection_name=self.collection_name,
            data=[query.query_embedding],
            filter=string_expr,
            limit=prefetch_k0,
            output_fields=output_fields,
            search_params=kwargs.get("milvus_search_config", self.search_config),
            anns_field=self.embedding_field,
            partition_names=kwargs.get("milvus_partition_names"),
        )
        nodes = res[0]
        node_embeddings = []
        node_ids = []
        # 【中文研读】逐节点转换数据：向量已在 node 上，不是在稠密写入分支中调用 BGE 生成。
        for node in nodes:
            node_embeddings.append(node["entity"]["embedding"])
            node_ids.append(self._get_id_from_hit(node))

        mmr_similarities, mmr_ids = get_top_k_mmr_embeddings(
            query_embedding=query.query_embedding,
            embeddings=node_embeddings,
            similarity_top_k=query.similarity_top_k,
            embedding_ids=node_ids,
            mmr_threshold=mmr_threshold,
        )
        node_dict = dict(list(zip(node_ids, nodes)))
        selected_nodes = [node_dict[id] for id in mmr_ids if id in node_dict]
        similarities = mmr_similarities  # Passing the MMR similarities instead of the original similarities
        ids = mmr_ids
        nodes, _, _ = self._parse_from_milvus_results([selected_nodes])
        logger.debug(
            f"Successfully performed MMR on embeddings in collection: {self.collection_name}"
        )
        return nodes, similarities, ids

    # 【中文研读】方法职责：上游其他可选查询路径，非 P3 采用范围
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；string_expr（转换后的数据库过滤表达式）；output_fields（数据库需返回的字段）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Tuple[List[BaseNode], List[float], List[str]]。结果的业务含义与失败分支见下面处理流程。
    def _sparse_search(
        self,
        query: VectorStoreQuery,
        string_expr: str,
        output_fields: List[str],
        **kwargs,
    ) -> Tuple[List[BaseNode], List[float], List[str]]:
        """
        Perform sparse search or full text search.
        """
        # 【中文研读】处理流程：按配置准备查询数据，调用指定字段检索，复用命中解析。
        search_params = {"params": {"drop_ratio_search": 0.2}}
        if isinstance(self.sparse_embedding_function, BaseSparseEmbeddingFunction):
            sparse_emb = self.sparse_embedding_function.encode_queries(
                [query.query_str]
            )[0]
            query_data = [sparse_emb]
        elif isinstance(self.sparse_embedding_function, BaseMilvusBuiltInFunction):
            query_data = [query.query_str]
        res = self.client.search(
            collection_name=self.collection_name,
            data=query_data,
            anns_field=self.sparse_embedding_field,
            limit=query.similarity_top_k,
            filter=string_expr,
            output_fields=output_fields,
            search_params=search_params,
            partition_names=kwargs.get("milvus_partition_names"),
        )
        nodes, similarities, ids = self._parse_from_milvus_results(res)
        return nodes, similarities, ids

    # 【中文研读】方法职责：对应可选路径的异步数据库调用，非 P3 采用范围
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；string_expr（转换后的数据库过滤表达式）；output_fields（数据库需返回的字段）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Tuple[List[BaseNode], List[float], List[str]]。结果的业务含义与失败分支见下面处理流程。
    async def _async_sparse_search(
        self,
        query: VectorStoreQuery,
        string_expr: str,
        output_fields: List[str],
        **kwargs,
    ) -> Tuple[List[BaseNode], List[float], List[str]]:
        """
        Perform asynchronous sparse search.
        """
        # 【中文研读】处理流程：准备查询数据，await 搜索后解析结果。
        assert self._async_milvusclient is not None, (
            "Async Client should be non-null to perform async operations. Pass `use_async_client = True` to the constructor to instantiate an async client"
        )
        search_params = {"params": {"drop_ratio_search": 0.2}}
        if isinstance(self.sparse_embedding_function, BaseSparseEmbeddingFunction):
            sparse_emb = self.sparse_embedding_function.encode_queries(
                [query.query_str]
            )[0]
            query_data = [sparse_emb]
        elif isinstance(self.sparse_embedding_function, BaseMilvusBuiltInFunction):
            query_data = [query.query_str]
        res = await self.aclient.search(
            collection_name=self.collection_name,
            data=query_data,
            anns_field=self.sparse_embedding_field,
            limit=query.similarity_top_k,
            filter=string_expr,
            output_fields=output_fields,
            search_params=search_params,
            partition_names=kwargs.get("milvus_partition_names"),
        )
        nodes, similarities, ids = self._parse_from_milvus_results(res)
        return nodes, similarities, ids

    # 【中文研读】方法职责：上游其他组合查询路径，非 P3 采用范围
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；string_expr（转换后的数据库过滤表达式）；output_fields（数据库需返回的字段）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Tuple[List[BaseNode], List[float], List[str]]。结果的业务含义与失败分支见下面处理流程。
    def _hybrid_search(
        self,
        query: VectorStoreQuery,
        string_expr: str,
        output_fields: List[str],
        **kwargs,
    ) -> Tuple[List[BaseNode], List[float], List[str]]:
        """
        Perform hybrid search.
        """
        # 【中文研读】处理流程：构造多份搜索请求，选择合并器，校验客户端能力后调用数据库并解析结果。
        if isinstance(self.sparse_embedding_function, BaseSparseEmbeddingFunction):
            sparse_emb = self.sparse_embedding_function.encode_queries(
                [query.query_str]
            )[0]
            query_data = [sparse_emb]
            sparse_metric_type = "IP"
        elif isinstance(self.sparse_embedding_function, BaseMilvusBuiltInFunction):
            query_data = [query.query_str]
            sparse_metric_type = "BM25"
        sparse_req = AnnSearchRequest(
            data=query_data,
            anns_field=self.sparse_embedding_field,
            param={"metric_type": sparse_metric_type},
            limit=query.similarity_top_k,
            expr=string_expr,  # Apply metadata filters to sparse search
        )
        dense_search_params = {
            "metric_type": self.similarity_metric,
            "params": self.search_config,
        }
        dense_emb = query.query_embedding
        dense_req = AnnSearchRequest(
            data=[dense_emb],
            anns_field=self.embedding_field,
            param=dense_search_params,
            limit=query.similarity_top_k,
            expr=string_expr,  # Apply metadata filters to dense search
        )
        if WeightedRanker is None or RRFRanker is None:
            logger.error("Hybrid retrieval is only supported in Milvus 2.4.0 or later.")
            raise ValueError(
                "Hybrid retrieval is only supported in Milvus 2.4.0 or later."
            )
        if self.hybrid_ranker == "WeightedRanker":
            if self.hybrid_ranker_params == {}:
                self.hybrid_ranker_params = {"weights": [1.0, 1.0]}
            ranker = WeightedRanker(*self.hybrid_ranker_params["weights"])
        elif self.hybrid_ranker == "RRFRanker":
            if self.hybrid_ranker_params == {}:
                self.hybrid_ranker_params = {"k": 60}
            ranker = RRFRanker(self.hybrid_ranker_params["k"])
        else:
            raise ValueError(f"Unsupported ranker: {self.hybrid_ranker}")
        if not hasattr(self.client, "hybrid_search"):
            raise ValueError(
                "Your pymilvus version does not support hybrid search. please update it by `pip install -U pymilvus`"
            )
        res = self.client.hybrid_search(
            self.collection_name,
            [dense_req, sparse_req],
            ranker=ranker,
            limit=query.similarity_top_k,
            output_fields=output_fields,
            partition_names=kwargs.get("milvus_partition_names"),
        )
        logger.debug(
            f"Successfully searched embedding in collection: {self.collection_name}"
            f" Num Results: {len(res[0])}"
        )
        nodes, similarities, ids = self._parse_from_milvus_results(res)
        return nodes, similarities, ids

    # 【中文研读】方法职责：上游组合查询的异步版本，非 P3 采用范围
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；string_expr（转换后的数据库过滤表达式）；output_fields（数据库需返回的字段）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Tuple[List[BaseNode], List[float], List[str]]。结果的业务含义与失败分支见下面处理流程。
    async def _async_hybrid_search(
        self,
        query: VectorStoreQuery,
        string_expr: str,
        output_fields: List[str],
        **kwargs,
    ) -> Tuple[List[BaseNode], List[float], List[str]]:
        """
        Perform asynchronous hybrid search.
        """
        # 【中文研读】处理流程：准备请求和合并配置，await 数据库，再转为统一结果。
        assert self._async_milvusclient is not None, (
            "Async Client should be non-null to perform async operations. Pass `use_async_client = True` to the constructor to instantiate an async client"
        )
        if isinstance(self.sparse_embedding_function, BaseSparseEmbeddingFunction):
            sparse_emb = (
                await self.sparse_embedding_function.async_encode_queries(
                    [query.query_str]
                )
            )[0]
            query_data = [sparse_emb]
            sparse_metric_type = "IP"
        elif isinstance(self.sparse_embedding_function, BaseMilvusBuiltInFunction):
            query_data = [query.query_str]
            sparse_metric_type = "BM25"
        sparse_req = AnnSearchRequest(
            data=query_data,
            anns_field=self.sparse_embedding_field,
            param={"metric_type": sparse_metric_type},
            limit=query.similarity_top_k,
            expr=string_expr,  # Apply metadata filters to sparse search
        )
        dense_search_params = {
            "metric_type": self.similarity_metric,
            "params": self.search_config,
        }
        dense_emb = query.query_embedding
        dense_req = AnnSearchRequest(
            data=[dense_emb],
            anns_field=self.embedding_field,
            param=dense_search_params,
            limit=query.similarity_top_k,
            expr=string_expr,  # Apply metadata filters to dense search
        )
        if WeightedRanker is None or RRFRanker is None:
            logger.error("Hybrid retrieval is only supported in Milvus 2.4.0 or later.")
            raise ValueError(
                "Hybrid retrieval is only supported in Milvus 2.4.0 or later."
            )
        if self.hybrid_ranker == "WeightedRanker":
            if self.hybrid_ranker_params == {}:
                self.hybrid_ranker_params = {"weights": [1.0, 1.0]}
            ranker = WeightedRanker(*self.hybrid_ranker_params["weights"])
        elif self.hybrid_ranker == "RRFRanker":
            if self.hybrid_ranker_params == {}:
                self.hybrid_ranker_params = {"k": 60}
            ranker = RRFRanker(self.hybrid_ranker_params["k"])
        else:
            raise ValueError(f"Unsupported ranker: {self.hybrid_ranker}")
        if not hasattr(self.client, "hybrid_search"):
            raise ValueError(
                "Your pymilvus version does not support hybrid search. please update it by `pip install -U pymilvus`"
            )
        res = await self.aclient.hybrid_search(
            self.collection_name,
            [dense_req, sparse_req],
            ranker=ranker,
            limit=query.similarity_top_k,
            output_fields=output_fields,
            partition_names=kwargs.get("milvus_partition_names"),
        )
        logger.debug(
            f"Successfully searched embedding in collection: {self.collection_name}"
            f" Num Results: {len(res[0])}"
        )
        nodes, similarities, ids = self._parse_from_milvus_results(res)
        return nodes, similarities, ids

    # 【中文研读】方法职责：按索引管理策略决定是否建索引
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def _create_index_if_required(self) -> None:
        """
        Create the index based on the index management strategy.

        This method only create index for existing collection without index.
        """
        # 【中文研读】处理流程：NO_VALIDATION 跳过，已有任意索引则返回，否则准备配置并创建；不等于全面校验已有索引兼容性。
        if self.index_management == IndexManagement.NO_VALIDATION:
            return
        elif self.index_management == IndexManagement.CREATE_IF_NOT_EXISTS:
            if len(self.client.list_indexes(self.collection_name)) > 0:
                return
            else:
                index_params = self.client.prepare_index_params()
                if self.enable_dense:
                    index_params = self._add_dense_index_params(index_params)
                if self.enable_sparse:
                    index_params = self._add_sparse_index_params(index_params)
                self.client.create_index(self.collection_name, index_params)
                logger.debug(
                    f"Successfully created index for existing collection: {self.collection_name}"
                )
        else:
            logger.warning(
                f"Ignored unsupported index management strategy: {self.index_management}"
            )
            return

    # 【中文研读】方法职责：组装稠密向量索引配置
    # 【中文研读】输入参数：index_params（待补充的索引参数集合）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def _add_dense_index_params(self, index_params: IndexParams):
        """Add dense vector index to params."""
        # 【中文研读】处理流程：复制参数，取字段/名称/类型/度量，剩余参数装入 params，追加到 IndexParams。
        base_params: Dict[str, Any] = self.index_config.copy()
        field_name: str = base_params.pop("field_name", self.embedding_field)
        index_name: str = base_params.pop("index_name", self.embedding_field)
        index_type: str = base_params.pop("index_type", "FLAT")
        metric_type: str = base_params.pop("metric_type", self.similarity_metric)
        kwargs = {
            "field_name": field_name,
            "index_name": index_name,
            "index_type": index_type,
            "metric_type": metric_type,
        }
        if len(base_params) != 0:
            kwargs["params"] = base_params
        index_params.add_index(**kwargs)
        return index_params

    # 【中文研读】方法职责：上游非主线索引配置辅助
    # 【中文研读】输入参数：index_params（待补充的索引参数集合）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def _add_sparse_index_params(self, index_params: IndexParams):
        """Add sparse index params."""
        # 【中文研读】处理流程：复制并拆分配置，向索引参数集合追加条目；当前 P3 不启用此路线。
        base_params: Dict[str, Any] = self.sparse_index_config.copy()
        field_name: str = base_params.pop("field_name", self.sparse_embedding_field)
        index_name: str = base_params.pop("index_name", self.sparse_embedding_field)
        index_type: str = base_params.pop("index_type", "SPARSE_INVERTED_INDEX")
        metric_type: str = base_params.pop(
            "metric_type", _get_index_metric_type(self.sparse_embedding_function)
        )
        kwargs = {
            "field_name": field_name,
            "index_name": index_name,
            "index_type": index_type,
            "metric_type": metric_type,
        }
        if len(base_params) != 0:
            kwargs["params"] = base_params
        index_params.add_index(**kwargs)
        return index_params

    # 【中文研读】方法职责：组装 collection 字段
    # 【中文研读】输入参数：schema（单个输出或数据结构定义）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def _add_fields_to_schema(self, schema: CollectionSchema):
        # 【中文研读】处理流程：主键、文档引用、正文、可选标量字段、启用的向量字段；稠密字段必须给出 dim 与名称。
        if self.enable_sparse and isinstance(
            self.sparse_embedding_function, BM25BuiltInFunction
        ):
            bm25_text_fields = self.sparse_embedding_function.input_field_names
            if isinstance(bm25_text_fields, str):
                bm25_text_fields = [bm25_text_fields]
        else:
            bm25_text_fields = []

        # Add scalar fields
        schema.add_field(
            field_name=MILVUS_ID_FIELD,
            datatype=DataType.VARCHAR,
            max_length=65_535,
            is_primary=True,
        )
        schema.add_field(
            field_name=self.doc_id_field,
            datatype=DataType.VARCHAR,
            max_length=65_535,
        )
        if self.text_key in bm25_text_fields:
            schema.add_field(
                field_name=self.text_key,
                datatype=DataType.VARCHAR,
                max_length=65_535,
                **self.sparse_embedding_function.get_field_kwargs(),
            )
        else:
            schema.add_field(
                field_name=self.text_key, datatype=DataType.VARCHAR, max_length=65_535
            )
        if self.scalar_field_names is not None and self.scalar_field_types is not None:
            # 【中文研读】字段名与类型一一配对，数量不同则拒绝建 Schema，避免错位建表。
            if len(self.scalar_field_names) != len(self.scalar_field_types):
                raise ValueError(
                    "scalar_field_names and scalar_field_types must have same length."
                )
            for field_name, field_type in zip(
                self.scalar_field_names, self.scalar_field_types
            ):
                max_length = 65_535 if field_type == DataType.VARCHAR else None
                if field_name in bm25_text_fields:
                    schema.add_field(
                        field_name=field_name,
                        datatype=field_type,
                        max_length=max_length,
                        **self.sparse_embedding_function.get_field_kwargs(),
                    )
                else:
                    schema.add_field(
                        field_name=field_name,
                        datatype=field_type,
                        max_length=max_length,
                    )

        # Add embedding field(s)
        if self.enable_dense:  # dense field
            # 【中文研读】稠密向量字段需要维度与字段名；缺失时明确报配置错误。
            if self.dim is None or self.embedding_field is None:
                raise ValueError(
                    "Dim and embedding_field are required to add dense embedding field."
                )
            schema.add_field(
                field_name=self.embedding_field,
                datatype=DataType.FLOAT_VECTOR,
                dim=self.dim,
            )
        if self.enable_sparse:  # sparse field
            if (
                self.sparse_embedding_function is None
                or self.sparse_embedding_field is None
            ):
                raise ValueError(
                    "Sparse embedding function and sparse_embedding_field are required to add sparse field."
                )
            schema.add_field(
                field_name=self.sparse_embedding_field,
                datatype=DataType.SPARSE_FLOAT_VECTOR,
            )
        return schema

    # 【中文研读】方法职责：为启用了上游可选函数的集合补 Schema；P3 默认稠密路径不依赖该功能。
    # 【中文研读】输入参数：schema（单个输出或数据结构定义）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def _add_functions_to_schema(self, schema: CollectionSchema):
        # 【中文研读】处理流程：为启用了上游可选函数的集合补 Schema；P3 默认稠密路径不依赖该功能。
        if self.enable_sparse and isinstance(
            self.sparse_embedding_function, BaseMilvusBuiltInFunction
        ):
            milvus_function = self.sparse_embedding_function
            schema.add_function(milvus_function)
        return schema

    # 【中文研读】方法职责：将驱动返回实体恢复为候选节点
    # 【中文研读】输入参数：results（多路或多次检索结果）。
    # 【中文研读】返回约定：Tuple[List[BaseNode], List[float], List[str]]。结果的业务含义与失败分支见下面处理流程。
    def _parse_from_milvus_results(
        self, results: List
    ) -> Tuple[List[BaseNode], List[float], List[str]]:
        """
        Parses the results from Milvus and returns a list of nodes, similarities and ids.
        Only parse the first result since we are only searching for one query.
        """
        # 【中文研读】处理流程：只处理首组结果，恢复节点元信息及正文，按同一遍历顺序收集 distance 与 ID。
        if len(results) > 1:
            logger.warning(
                "More than one result found in Milvus search. Only parsing the first result."
            )
        nodes = []
        similarities = []
        ids = []
        # Parse the results
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

            # Set the text field if it exists
            if self.text_key in hit["entity"]:
                text = hit["entity"].get(self.text_key)
                node.text = text

            nodes.append(node)
            # 【中文研读】驱动字段叫 distance；具体数值方向由度量方式决定，不是自动统一的相关性概率。
            similarities.append(hit["distance"])
            ids.append(self._get_id_from_hit(hit))
        return nodes, similarities, ids

    # 【中文研读】方法职责：兼容驱动结果中的主键字段
    # 【中文研读】输入参数：hit（单条数据库命中）。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def _get_id_from_hit(self, hit: Dict) -> str:
        # 【中文研读】处理流程：有 id 则直接读取，否则使用首个字段的值；接入方必须核对实际返回结构。
        if "id" in hit:
            return hit["id"]
        else:
            return hit[next(iter(hit))]
