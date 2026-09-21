# 【中文研读】阅读主线：retrieve/aretrieve 统一查询与记录回调 → 子类检索 → 展开 IndexNode 引用 → 按节点 ID 去重。这里没有 P3 授权、版本或持久恢复。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
"""Base retriever."""

from abc import abstractmethod
from typing import Any, Dict, List, Optional

from llama_index.core.base.base_query_engine import BaseQueryEngine
from llama_index.core.callbacks.base import CallbackManager
from llama_index.core.callbacks.schema import CBEventType, EventPayload
from llama_index.core.prompts.mixin import (
    PromptDictType,
    PromptMixin,
    PromptMixinType,
)
from llama_index.core.schema import (
    BaseNode,
    IndexNode,
    NodeWithScore,
    QueryBundle,
    QueryType,
    TextNode,
)
from llama_index.core.settings import Settings
from llama_index.core.utils import print_text
from llama_index.core.instrumentation import DispatcherSpanMixin
from llama_index.core.instrumentation.events.retrieval import (
    RetrievalEndEvent,
    RetrievalStartEvent,
)
import llama_index.core.instrumentation as instrument

dispatcher = instrument.get_dispatcher(__name__)


# 【中文研读】类型职责：所有检索器的共同入口；子类负责取得候选，基类处理输入包装、递归节点和观测回调。
class BaseRetriever(PromptMixin, DispatcherSpanMixin):
    """Base retriever."""

    # 【中文研读】方法职责：装配回调、索引对象映射和调试开关
    # 【中文研读】输入参数：callback_manager（运行事件与回调管理器）；object_map（索引 ID 到可检索对象的映射）；objects（可供递归展开的索引节点）；verbose（是否输出调试信息）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def __init__(
        self,
        callback_manager: Optional[CallbackManager] = None,
        object_map: Optional[Dict] = None,
        objects: Optional[List[IndexNode]] = None,
        verbose: bool = False,
    ) -> None:
        # 【中文研读】处理流程：objects 若已给出，转换为 index_id 到实际对象的字典，供递归检索定位对象。
        self.callback_manager = callback_manager or CallbackManager()

        if objects is not None:
            object_map = {obj.index_id: obj.obj for obj in objects}

        self.object_map = object_map or {}
        self._verbose = verbose

    # 【中文研读】方法职责：兼容没有调用父类构造器的子类
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def _check_callback_manager(self) -> None:
        """Check callback manager."""
        # 【中文研读】处理流程：仅在属性缺失时补用全局回调管理器，不覆盖已有配置。
        if not hasattr(self, "callback_manager"):
            self.callback_manager = Settings.callback_manager

    # 【中文研读】方法职责：基类本身没有提示词，返回空映射；具体检索器可覆盖此方法。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：PromptDictType。结果的业务含义与失败分支见下面处理流程。
    def _get_prompts(self) -> PromptDictType:
        """Get prompts."""
        # 【中文研读】处理流程：基类本身没有提示词，返回空映射；具体检索器可覆盖此方法。
        return {}

    # 【中文研读】方法职责：基类没有嵌套提示组件，返回空映射。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：PromptMixinType。结果的业务含义与失败分支见下面处理流程。
    def _get_prompt_modules(self) -> PromptMixinType:
        """Get prompt modules."""
        # 【中文研读】处理流程：基类没有嵌套提示组件，返回空映射。
        return {}

    # 【中文研读】方法职责：预留提示词更新钩子；此基类没有具体更新逻辑。
    # 【中文研读】输入参数：prompts。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def _update_prompts(self, prompts: PromptDictType) -> None:
        """Update prompts."""

    # 【中文研读】方法职责：把不同类型的被引用对象统一转换为带分数节点
    # 【中文研读】输入参数：obj（节点引用的对象）；query_bundle（包装后的查询文本及可选向量）；score（候选分数）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    def _retrieve_from_object(
        self,
        obj: Any,
        query_bundle: QueryBundle,
        score: float,
    ) -> List[NodeWithScore]:
        """Retrieve nodes from object."""
        # 【中文研读】处理流程：已有带分节点直接返回，裸节点补分数，查询引擎生成文本节点，子检索器继续 retrieve；不支持的类型抛错。
        if self._verbose:
            print_text(
                f"Retrieving from object {obj.__class__.__name__} with query {query_bundle.query_str}\n",
                color="llama_pink",
            )
        # 【中文研读】类型分派从最具体的包装节点开始；不同对象最终统一为 List[NodeWithScore]。
        if isinstance(obj, NodeWithScore):
            return [obj]
        elif isinstance(obj, BaseNode):
            return [NodeWithScore(node=obj, score=score)]
        elif isinstance(obj, BaseQueryEngine):
            # 【中文研读】这里可能触发模型生成回答；返回内容不一定是原始存储正文，P3 精确来源链不能直接照搬。
            response = obj.query(query_bundle)
            return [
                NodeWithScore(
                    node=TextNode(text=str(response), metadata=response.metadata or {}),
                    score=score,
                )
            ]
        elif isinstance(obj, BaseRetriever):
            return obj.retrieve(query_bundle)
        else:
            raise ValueError(f"Object {obj} is not retrievable.")

    # 【中文研读】方法职责：异步展开被引用对象
    # 【中文研读】输入参数：obj（节点引用的对象）；query_bundle（包装后的查询文本及可选向量）；score（候选分数）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    async def _aretrieve_from_object(
        self,
        obj: Any,
        query_bundle: QueryBundle,
        score: float,
    ) -> List[NodeWithScore]:
        """Retrieve nodes from object."""
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

    # 【中文研读】方法职责：展开指向其他检索器或查询引擎的 IndexNode
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）；nodes（候选节点列表）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    def _handle_recursive_retrieval(
        self, query_bundle: QueryBundle, nodes: List[NodeWithScore]
    ) -> List[NodeWithScore]:
        # 【中文研读】处理流程：逐个定位对象并递归取回节点，未找到对象则保留引用；最终按 node_id 保留首次出现的结果。
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
                        self._retrieve_from_object(
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

    # 【中文研读】方法职责：异步展开 IndexNode 后去重
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）；nodes（候选节点列表）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
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
                    # TODO: Add concurrent execution via `run_jobs()` ?
                    retrieved_nodes.extend(
                        await self._aretrieve_from_object(
                            obj, query_bundle=query_bundle, score=score
                        )
                    )
                else:
                    retrieved_nodes.append(n)
            else:
                retrieved_nodes.append(n)

        # remove any duplicates based on node_id
        # 【中文研读】集合记录已交付的节点 ID；列表推导式用短路逻辑完成首次保留，后续重复 ID 被排除。
        seen = set()
        return [
            n
            for n in retrieved_nodes
            if not (
                n.node.node_id in seen or seen.add(n.node.node_id)  # type: ignore[func-returns-value]
            )
        ]

    # 【中文研读】方法职责：同步公共检索入口
    # 【中文研读】输入参数：str_or_query_bundle（字符串或统一查询对象）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    @dispatcher.span
    def retrieve(self, str_or_query_bundle: QueryType) -> List[NodeWithScore]:
        """
        Retrieve nodes given query.

        Args:
            str_or_query_bundle (QueryType): Either a query string or
                a QueryBundle object.

        """
        # 【中文研读】处理流程：补回调 → 把字符串转成 QueryBundle → 发出开始事件 → 调用子类检索 → 展开引用 → 发出结束事件并返回候选。
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
                # 【中文研读】真正的候选发现发生在子类；外层公共入口不决定具体数据库。
                nodes = self._retrieve(query_bundle)
                nodes = self._handle_recursive_retrieval(query_bundle, nodes)
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

    # 【中文研读】方法职责：异步公共检索入口
    # 【中文研读】输入参数：str_or_query_bundle（字符串或统一查询对象）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
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

    # 【中文研读】方法职责：抽象检索契约；子类必须提供候选获取实现，基类不执行向量查询。
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    @abstractmethod
    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        """
        Retrieve nodes given query.

        Implemented by the user.

        """

    # TODO: make this abstract
    # @abstractmethod
    # 【中文研读】方法职责：默认异步兼容入口
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    async def _aretrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        """
        Asynchronously retrieve nodes given query.

        Implemented by the user.

        """
        # 【中文研读】处理流程：直接调用同步 _retrieve，因此方法名带 async 也不保证不阻塞事件循环，真正异步需要子类覆盖。
        return self._retrieve(query_bundle)
