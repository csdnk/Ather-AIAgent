# 【中文研读】阅读主线：VectorStoreQuery 表达请求，VectorStoreQueryResult 表达候选，MetadataFilter(s) 表达过滤，VectorStore/BasePydanticVectorStore 声明适配器接口。仅有类型定义不会执行数据库查询。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
"""Vector store index types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Sequence,
    Union,
    runtime_checkable,
)

import fsspec
from deprecated import deprecated
from llama_index.core.bridge.pydantic import (
    BaseModel,
    ConfigDict,
    StrictFloat,
    StrictInt,
    StrictStr,
)
from llama_index.core.schema import BaseComponent, BaseNode, TextNode

DEFAULT_PERSIST_DIR = "./storage"
DEFAULT_PERSIST_FNAME = "vector_store.json"


# legacy: kept for backward compatibility
NodeWithEmbedding = TextNode


# 【中文研读】类型职责：查询结果封套：可有节点、对应分数和节点 IDs；具体适配器决定返回哪些字段，调用方负责补正文与核验。
@dataclass
class VectorStoreQueryResult:
    """Vector store query result."""

    # 【中文研读】数据库返回的候选节点；可以缺失，此时上层需要按 IDs 补正文。
    nodes: Optional[Sequence[BaseNode]] = None
    # 【中文研读】与节点顺序对应的分数；调用方必须保证配对，不能乱序拼接。
    similarities: Optional[List[float]] = None
    # 【中文研读】命中的引用 ID，可能是索引内部 ID，需检查到业务记忆的映射。
    ids: Optional[List[str]] = None


# 【中文研读】类型职责：上游支持的查询模式目录；P3 使用默认稠密路径，其他枚举保留原始定义不表示项目采用。
class VectorStoreQueryMode(str, Enum):
    """Vector store query mode."""

    DEFAULT = "default"
    SPARSE = "sparse"
    HYBRID = "hybrid"
    TEXT_SEARCH = "text_search"
    SEMANTIC_HYBRID = "semantic_hybrid"

    # fit learners
    SVM = "svm"
    LOGISTIC_REGRESSION = "logistic_regression"
    LINEAR_REGRESSION = "linear_regression"

    # maximum marginal relevance
    MMR = "mmr"


# 【中文研读】类型职责：标量字段比较运算符，如等于、范围、集合包含；适配器须转换为数据库支持的表达式。
class FilterOperator(str, Enum):
    """Vector store filter operator."""

    # TODO add more operators
    EQ = "=="  # default operator (string, int, float)
    GT = ">"  # greater than (int, float)
    LT = "<"  # less than (int, float)
    NE = "!="  # not equal to (string, int, float)
    GTE = ">="  # greater than or equal to (int, float)
    LTE = "<="  # less than or equal to (int, float)
    IN = "in"  # In array (string or number)
    NIN = "nin"  # Not in array (string or number)
    ANY = "any"  # Contains any (array of strings)
    ALL = "all"  # Contains all (array of strings)
    TEXT_MATCH = "text_match"  # full text match (allows you to search for a specific substring, token or phrase within the text field)
    TEXT_MATCH_INSENSITIVE = (
        "text_match_insensitive"  # full text match (case insensitive)
    )
    CONTAINS = "contains"  # metadata array contains value (string or number)
    IS_EMPTY = "is_empty"  # the field is not exist or empty (null or empty array)


# 【中文研读】类型职责：多条过滤条件的逻辑组合：AND、OR 或 NOT。
class FilterCondition(str, Enum):
    """Vector store filter conditions to combine different filters."""

    # TODO add more conditions
    AND = "and"
    OR = "or"
    NOT = "not"  # negates the filter condition


# 【中文研读】类型职责：单条字段约束，包含键、严格类型值和比较运算符；不自动把客户端字段当可信授权。
class MetadataFilter(BaseModel):
    r"""
    Comprehensive metadata filter for vector stores to support more operators.

    Value uses Strict types, as int, float and str are compatible types and were all
    converted to string before.

    See: https://docs.pydantic.dev/latest/usage/types/#strict-types
    """

    # 【中文研读】待过滤字段名，例如由服务端约定的范围字段。
    key: str
    # 【中文研读】比较值使用严格类型，避免数字自动当字符串等隐式转换。
    value: Optional[
        Union[
            StrictInt,
            StrictFloat,
            StrictStr,
            List[StrictStr],
            List[StrictFloat],
            List[StrictInt],
        ]
    ]
    operator: FilterOperator = FilterOperator.EQ

    # 【中文研读】方法职责：按单条过滤 Schema 校验字典
    # 【中文研读】输入参数：filter_dict（单个过滤字典）。
    # 【中文研读】返回约定：'MetadataFilter'。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def from_dict(
        cls,
        filter_dict: Dict,
    ) -> "MetadataFilter":
        """
        Create MetadataFilter from dictionary.

        Args:
            filter_dict: Dict with key, value and operator.

        """
        # 【中文研读】处理流程：model_validate 拒绝不满足类型的输入，不进行业务权限判定。
        return MetadataFilter.model_validate(filter_dict)


# # TODO: Deprecate ExactMatchFilter and use MetadataFilter instead
# # Keep class for now so that AutoRetriever can still work with old vector stores
# class ExactMatchFilter(BaseModel):
#     key: str
#     value: Union[StrictInt, StrictFloat, StrictStr]

# set ExactMatchFilter to MetadataFilter
ExactMatchFilter = MetadataFilter


# 【中文研读】类型职责：可嵌套过滤树，通过 condition 组合叶子条件和子条件组。
class MetadataFilters(BaseModel):
    """Metadata filters for vector stores."""

    # Exact match filters and Advanced filters with operators like >, <, >=, <=, !=, etc.
    # 【中文研读】子过滤集合，可包含叶子或嵌套组，表达括号与逻辑结构。
    filters: List[Union[MetadataFilter, ExactMatchFilter, "MetadataFilters"]]
    # and/or such conditions for combining different filters
    condition: Optional[FilterCondition] = FilterCondition.AND

    # 【中文研读】方法职责：旧式字典转换入口
    # 【中文研读】输入参数：filter_dict（单个过滤字典）。
    # 【中文研读】返回约定：'MetadataFilters'。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    @deprecated(
        "`from_dict()` is deprecated. "
        "Please use `MetadataFilters(filters=.., condition='and')` directly instead."
    )
    def from_dict(cls, filter_dict: Dict) -> "MetadataFilters":
        """Create MetadataFilters from json."""
        # 【中文研读】处理流程：每个键值生成 EQ 条件，再组为默认 AND；复杂嵌套不通过该旧入口表达。
        filters = []
        for k, v in filter_dict.items():
            filter = MetadataFilter(key=k, value=v, operator=FilterOperator.EQ)
            filters.append(filter)
        return cls(filters=filters)

    # 【中文研读】方法职责：把多条条件字典转换为过滤集合
    # 【中文研读】输入参数：filter_dicts（多条过滤条件字典）；condition（条件间的逻辑关系）。
    # 【中文研读】返回约定：'MetadataFilters'。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def from_dicts(
        cls,
        filter_dicts: List[Dict],
        condition: Optional[FilterCondition] = FilterCondition.AND,
    ) -> "MetadataFilters":
        """
        Create MetadataFilters from dicts.

        This takes in a list of individual MetadataFilter objects, along
        with the condition.

        Args:
            filter_dicts: List of dicts, each dict is a MetadataFilter.
            condition: FilterCondition to combine different filters.

        """
        # 【中文研读】处理流程：逐条校验 MetadataFilter，再保存指定逻辑关系。
        return cls(
            filters=[
                MetadataFilter.from_dict(filter_dict) for filter_dict in filter_dicts
            ],
            condition=condition,
        )

    # 【中文研读】方法职责：降为旧适配器支持的精确匹配列表
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：List[ExactMatchFilter]。结果的业务含义与失败分支见下面处理流程。
    def legacy_filters(self) -> List[ExactMatchFilter]:
        """Convert MetadataFilters to legacy ExactMatchFilters."""
        # 【中文研读】处理流程：遇到嵌套组或非 EQ 运算明确报错，其余复制键值。
        filters = []
        # 【中文研读】旧接口只能接受扁平精确匹配；循环检查每条条件是否能无损转换。
        for filter in self.filters:
            if (
                isinstance(filter, MetadataFilters)
                or filter.operator != FilterOperator.EQ
            ):
                raise ValueError(
                    "Vector Store only supports exact match filters. "
                    "Please use ExactMatchFilter or FilterOperator.EQ instead."
                )
            filters.append(ExactMatchFilter(key=filter.key, value=filter.value))
        return filters


# 【中文研读】类型职责：查询文本、过滤条件与 TopK 的组合描述，供上层构造检索请求。
class VectorStoreQuerySpec(BaseModel):
    """
    Schema for a structured request for vector store
    (i.e. to be converted to a VectorStoreQuery).

    Currently only used by VectorIndexAutoRetriever.
    """

    query: str
    filters: List[MetadataFilter]
    top_k: Optional[int] = None


# 【中文研读】类型职责：描述可过滤元信息字段的名称、类型和含义，供自动检索等组件使用。
class MetadataInfo(BaseModel):
    """
    Information about a metadata filter supported by a vector store.

    Currently only used by VectorIndexAutoRetriever.
    """

    name: str
    type: str
    description: str


# 【中文研读】类型职责：向量库内容概况与元信息字段说明，不是库中具体记录。
class VectorStoreInfo(BaseModel):
    """
    Information about a vector store (content and supported metadata filters).

    Currently only used by VectorIndexAutoRetriever.
    """

    metadata_info: List[MetadataInfo]
    content_info: str


# 【中文研读】类型职责：向量数据库查询参数；P3 主要读取向量、TopK、ID 范围、过滤和返回字段。
@dataclass
class VectorStoreQuery:
    """Vector store query."""

    # 【中文研读】预先生成的查询向量；向量库接口本身不会证明它与库存空间兼容。
    query_embedding: Optional[List[float]] = None
    # 【中文研读】候选上限，不等同 ContextPack 最终条数或 token 预算。
    similarity_top_k: int = 1
    # 【中文研读】可选来源文档范围，限制候选来源，不等同当前权限自动校验。
    doc_ids: Optional[List[str]] = None
    # 【中文研读】可选节点集合限制，配合业务范围控制候选发现。
    node_ids: Optional[List[str]] = None
    query_str: Optional[str] = None
    output_fields: Optional[List[str]] = None
    embedding_field: Optional[str] = None

    mode: VectorStoreQueryMode = VectorStoreQueryMode.DEFAULT

    # NOTE: only for hybrid search (0 for bm25, 1 for vector search)
    alpha: Optional[float] = None

    # metadata filters
    # 【中文研读】标量条件；P3 的授权过滤应由服务端构造，不能原样信任用户输入。
    filters: Optional[MetadataFilters] = None

    # only for mmr
    mmr_threshold: Optional[float] = None

    # NOTE: currently only used by postgres hybrid search
    sparse_top_k: Optional[int] = None
    # NOTE: return top k results from hybrid search. similarity_top_k is used for dense search top k
    hybrid_top_k: Optional[int] = None


# 【中文研读】类型职责：Protocol 形式的向量适配器接口，声明增删查与能力；接口存在不代表实现已经提供。
@runtime_checkable
class VectorStore(Protocol):
    """Abstract vector store protocol."""

    stores_text: bool
    is_embedding_query: bool = True

    # 【中文研读】方法职责：暴露底层数据库客户端的接口契约；由具体适配器返回实例，基类不负责建立连接。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Any。结果的业务含义与失败分支见下面处理流程。
    @property
    def client(self) -> Any:
        """Get client."""
        # 【中文研读】处理流程：暴露底层数据库客户端的接口契约；由具体适配器返回实例，基类不负责建立连接。
        ...

    # 【中文研读】方法职责：插入节点并返回 ID 列表的抽象契约；数据格式转换、模型是否预先生成向量由具体适配器约定。
    # 【中文研读】输入参数：nodes（候选节点列表）；**add_kwargs（透传选项）。
    # 【中文研读】返回约定：List[str]。结果的业务含义与失败分支见下面处理流程。
    def add(
        self,
        nodes: List[BaseNode],
        **add_kwargs: Any,
    ) -> List[str]:
        """Add nodes with embedding to vector store."""
        # 【中文研读】处理流程：插入节点并返回 ID 列表的抽象契约；数据格式转换、模型是否预先生成向量由具体适配器约定。
        ...

    # 【中文研读】方法职责：默认异步兼容包装
    # 【中文研读】输入参数：nodes（候选节点列表）；**kwargs（透传选项）。
    # 【中文研读】返回约定：List[str]。结果的业务含义与失败分支见下面处理流程。
    async def async_add(
        self,
        nodes: List[BaseNode],
        **kwargs: Any,
    ) -> List[str]:
        """
        Asynchronously add nodes with embedding to vector store.
        NOTE: this is not implemented for all vector stores. If not implemented,
        it will just call add synchronously.
        """
        # 【中文研读】处理流程：直接调用同步 add，并非自动使用异步驱动，具体适配器应按需覆盖。
        # 【中文研读】异步包装继续执行同步 add；网络与模型耗时是否阻塞由子类实现决定。
        return self.add(nodes)

    # 【中文研读】方法职责：按来源文档引用删除向量的抽象契约；不能据此推断 P3 正文或删除屏障已同步完成。
    # 【中文研读】输入参数：ref_doc_id（来源文档 ID）；**delete_kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def delete(self, ref_doc_id: str, **delete_kwargs: Any) -> None:
        """
        Delete nodes using with ref_doc_id."""
        # 【中文研读】处理流程：按来源文档引用删除向量的抽象契约；不能据此推断 P3 正文或删除屏障已同步完成。
        ...

    # 【中文研读】方法职责：异步接口的默认同步转发
    # 【中文研读】输入参数：ref_doc_id（来源文档 ID）；**delete_kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    async def adelete(self, ref_doc_id: str, **delete_kwargs: Any) -> None:
        """
        Delete nodes using with ref_doc_id.
        NOTE: this is not implemented for all vector stores. If not implemented,
        it will just call delete synchronously.
        """
        # 【中文研读】处理流程：调用 delete，没有额外并发、重试或持久恢复机制。
        self.delete(ref_doc_id, **delete_kwargs)

    # 【中文研读】方法职责：向量查询抽象契约；输入 VectorStoreQuery，输出结果封套，具体检索由子类实现。
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；**kwargs（透传选项）。
    # 【中文研读】返回约定：VectorStoreQueryResult。结果的业务含义与失败分支见下面处理流程。
    def query(self, query: VectorStoreQuery, **kwargs: Any) -> VectorStoreQueryResult:
        """Query vector store."""
        # 【中文研读】处理流程：向量查询抽象契约；输入 VectorStoreQuery，输出结果封套，具体检索由子类实现。
        ...

    # 【中文研读】方法职责：默认直接调用同步 query
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；**kwargs（透传选项）。
    # 【中文研读】返回约定：VectorStoreQueryResult。结果的业务含义与失败分支见下面处理流程。
    async def aquery(
        self, query: VectorStoreQuery, **kwargs: Any
    ) -> VectorStoreQueryResult:
        """
        Asynchronously query vector store.
        NOTE: this is not implemented for all vector stores. If not implemented,
        it will just call query synchronously.
        """
        # 【中文研读】处理流程：提供兼容接口但可能阻塞事件循环，真正异步适配器需覆盖。
        # 【中文研读】注意这里没有线程转交或 await 驱动调用；async 定义本身不能保证非阻塞。
        return self.query(query, **kwargs)

    # 【中文研读】方法职责：默认持久化钩子直接返回；本基类不会自动把任何向量保存到磁盘。
    # 【中文研读】输入参数：persist_path（持久化目标路径）；fs（可选文件系统适配器）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def persist(
        self, persist_path: str, fs: Optional[fsspec.AbstractFileSystem] = None
    ) -> None:
        # 【中文研读】处理流程：默认持久化钩子直接返回；本基类不会自动把任何向量保存到磁盘。
        return None


# TODO: Temp copy of VectorStore for pydantic, can't mix with runtime_checkable
# 【中文研读】类型职责：Pydantic 形式的向量适配基类，含抽象接口与默认兼容方法，具体数据库适配器继承实现。
class BasePydanticVectorStore(BaseComponent, ABC):
    """Abstract vector store protocol."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    stores_text: bool
    is_embedding_query: bool = True

    # 【中文研读】方法职责：暴露底层数据库客户端的接口契约；由具体适配器返回实例，基类不负责建立连接。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Any。结果的业务含义与失败分支见下面处理流程。
    @property
    @abstractmethod
    def client(self) -> Any:
        """Get client."""

    # 【中文研读】方法职责：按 ID 或过滤器读取节点的可选接口；默认 NotImplementedError，调用前应确认适配器能力。
    # 【中文研读】输入参数：node_ids（限制或读取的节点 ID 集合）；filters（结构化过滤条件集合）。
    # 【中文研读】返回约定：List[BaseNode]。结果的业务含义与失败分支见下面处理流程。
    def get_nodes(
        self,
        node_ids: Optional[List[str]] = None,
        filters: Optional[MetadataFilters] = None,
    ) -> List[BaseNode]:
        """Get nodes from vector store."""
        # 【中文研读】处理流程：按 ID 或过滤器读取节点的可选接口；默认 NotImplementedError，调用前应确认适配器能力。
        raise NotImplementedError("get_nodes not implemented")

    # 【中文研读】方法职责：异步节点读取的默认同步包装
    # 【中文研读】输入参数：node_ids（限制或读取的节点 ID 集合）；filters（结构化过滤条件集合）。
    # 【中文研读】返回约定：List[BaseNode]。结果的业务含义与失败分支见下面处理流程。
    async def aget_nodes(
        self,
        node_ids: Optional[List[str]] = None,
        filters: Optional[MetadataFilters] = None,
    ) -> List[BaseNode]:
        """Asynchronously get nodes from vector store."""
        # 【中文研读】处理流程：将 ID 与过滤条件交给 get_nodes。
        return self.get_nodes(node_ids, filters)

    # 【中文研读】方法职责：插入节点并返回 ID 列表的抽象契约；数据格式转换、模型是否预先生成向量由具体适配器约定。
    # 【中文研读】输入参数：nodes（候选节点列表）；**kwargs（透传选项）。
    # 【中文研读】返回约定：List[str]。结果的业务含义与失败分支见下面处理流程。
    @abstractmethod
    def add(
        self,
        nodes: Sequence[BaseNode],
        **kwargs: Any,
    ) -> List[str]:
        """Add nodes to vector store."""

    # 【中文研读】方法职责：默认异步兼容包装
    # 【中文研读】输入参数：nodes（候选节点列表）；**kwargs（透传选项）。
    # 【中文研读】返回约定：List[str]。结果的业务含义与失败分支见下面处理流程。
    async def async_add(
        self,
        nodes: Sequence[BaseNode],
        **kwargs: Any,
    ) -> List[str]:
        """
        Asynchronously add nodes to vector store.
        NOTE: this is not implemented for all vector stores. If not implemented,
        it will just call add synchronously.
        """
        # 【中文研读】处理流程：直接调用同步 add，并非自动使用异步驱动，具体适配器应按需覆盖。
        # 【中文研读】异步包装继续执行同步 add；网络与模型耗时是否阻塞由子类实现决定。
        return self.add(nodes, **kwargs)

    # 【中文研读】方法职责：按来源文档引用删除向量的抽象契约；不能据此推断 P3 正文或删除屏障已同步完成。
    # 【中文研读】输入参数：ref_doc_id（来源文档 ID）；**delete_kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @abstractmethod
    def delete(self, ref_doc_id: str, **delete_kwargs: Any) -> None:
        """
        Delete nodes using with ref_doc_id."""

    # 【中文研读】方法职责：异步接口的默认同步转发
    # 【中文研读】输入参数：ref_doc_id（来源文档 ID）；**delete_kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    async def adelete(self, ref_doc_id: str, **delete_kwargs: Any) -> None:
        """
        Delete nodes using with ref_doc_id.
        NOTE: this is not implemented for all vector stores. If not implemented,
        it will just call delete synchronously.
        """
        # 【中文研读】处理流程：调用 delete，没有额外并发、重试或持久恢复机制。
        self.delete(ref_doc_id, **delete_kwargs)

    # 【中文研读】方法职责：按节点 ID 或过滤条件删除的可选接口；基类明确未实现，不能当作已支持清理功能。
    # 【中文研读】输入参数：node_ids（限制或读取的节点 ID 集合）；filters（结构化过滤条件集合）；**delete_kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def delete_nodes(
        self,
        node_ids: Optional[List[str]] = None,
        filters: Optional[MetadataFilters] = None,
        **delete_kwargs: Any,
    ) -> None:
        """Delete nodes from vector store."""
        # 【中文研读】处理流程：按节点 ID 或过滤条件删除的可选接口；基类明确未实现，不能当作已支持清理功能。
        raise NotImplementedError("delete_nodes not implemented")

    # 【中文研读】方法职责：异步删除节点的默认同步包装
    # 【中文研读】输入参数：node_ids（限制或读取的节点 ID 集合）；filters（结构化过滤条件集合）；**delete_kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    async def adelete_nodes(
        self,
        node_ids: Optional[List[str]] = None,
        filters: Optional[MetadataFilters] = None,
        **delete_kwargs: Any,
    ) -> None:
        """Asynchronously delete nodes from vector store."""
        # 【中文研读】处理流程：透传节点与过滤条件给 delete_nodes，具体能力由子类提供。
        self.delete_nodes(node_ids, filters)

    # 【中文研读】方法职责：清空向量库的可选接口；默认未实现，实际适配器可能产生全库删除副作用。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def clear(self) -> None:
        """Clear all nodes from configured vector store."""
        # 【中文研读】处理流程：清空向量库的可选接口；默认未实现，实际适配器可能产生全库删除副作用。
        raise NotImplementedError("clear not implemented")

    # 【中文研读】方法职责：异步清空的默认同步包装
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    async def aclear(self) -> None:
        """Asynchronously clear all nodes from configured vector store."""
        # 【中文研读】处理流程：调用 clear，不能自动获得事务或撤销能力。
        self.clear()

    # 【中文研读】方法职责：向量查询抽象契约；输入 VectorStoreQuery，输出结果封套，具体检索由子类实现。
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；**kwargs（透传选项）。
    # 【中文研读】返回约定：VectorStoreQueryResult。结果的业务含义与失败分支见下面处理流程。
    @abstractmethod
    def query(self, query: VectorStoreQuery, **kwargs: Any) -> VectorStoreQueryResult:
        """Query vector store."""

    # 【中文研读】方法职责：默认直接调用同步 query
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；**kwargs（透传选项）。
    # 【中文研读】返回约定：VectorStoreQueryResult。结果的业务含义与失败分支见下面处理流程。
    async def aquery(
        self, query: VectorStoreQuery, **kwargs: Any
    ) -> VectorStoreQueryResult:
        """
        Asynchronously query vector store.
        NOTE: this is not implemented for all vector stores. If not implemented,
        it will just call query synchronously.
        """
        # 【中文研读】处理流程：提供兼容接口但可能阻塞事件循环，真正异步适配器需覆盖。
        # 【中文研读】注意这里没有线程转交或 await 驱动调用；async 定义本身不能保证非阻塞。
        return self.query(query, **kwargs)

    # 【中文研读】方法职责：默认持久化钩子直接返回；本基类不会自动把任何向量保存到磁盘。
    # 【中文研读】输入参数：persist_path（持久化目标路径）；fs（可选文件系统适配器）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def persist(
        self, persist_path: str, fs: Optional[fsspec.AbstractFileSystem] = None
    ) -> None:
        # 【中文研读】处理流程：默认持久化钩子直接返回；本基类不会自动把任何向量保存到磁盘。
        return None
