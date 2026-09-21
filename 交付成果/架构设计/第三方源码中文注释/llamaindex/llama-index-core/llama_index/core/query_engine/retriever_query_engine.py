# 【中文研读】阅读主线：检索器 → 顺序后处理 → 回答合成器。可以单独调用 retrieve 只取候选；P3 参考组合结构，最终交付使用自己的 ContextPack 契约。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
from typing import Any, List, Optional, Sequence, Type

from llama_index.core.base.base_query_engine import BaseQueryEngine
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.base.response.schema import RESPONSE_TYPE
from llama_index.core.bridge.pydantic import BaseModel
from llama_index.core.callbacks.base import CallbackManager
from llama_index.core.callbacks.schema import CBEventType, EventPayload
from llama_index.core.llms.llm import LLM
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.prompts import BasePromptTemplate
from llama_index.core.prompts.mixin import PromptMixinType
from llama_index.core.response_synthesizers import (
    BaseSynthesizer,
    ResponseMode,
    get_response_synthesizer,
)
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.core.settings import Settings
import llama_index.core.instrumentation as instrument

dispatcher = instrument.get_dispatcher(__name__)


# 【中文研读】类型职责：用依赖注入组合检索器、处理器列表与合成器；不要求每个模块了解其他模块内部实现。
class RetrieverQueryEngine(BaseQueryEngine):
    """
    Retriever query engine.

    Args:
        retriever (BaseRetriever): A retriever object.
        response_synthesizer (Optional[BaseSynthesizer]): A BaseSynthesizer
            object.
        callback_manager (Optional[CallbackManager]): A callback manager.

    """

    # 【中文研读】方法职责：装配三个可替换部分并统一回调
    # 【中文研读】输入参数：retriever（被装配的检索器）；response_synthesizer（回答合成组件）；node_postprocessors（顺序执行的候选处理器）；callback_manager（运行事件与回调管理器）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def __init__(
        self,
        retriever: BaseRetriever,
        response_synthesizer: Optional[BaseSynthesizer] = None,
        node_postprocessors: Optional[List[BaseNodePostprocessor]] = None,
        callback_manager: Optional[CallbackManager] = None,
    ) -> None:
        # 【中文研读】处理流程：保留传入检索器，未指定合成器时基于全局 LLM 构建默认合成器，为后处理器共享回调。
        self._retriever = retriever
        # 【中文研读】默认装配可能解析模型依赖；只想学习候选流程时，不能忽略构造阶段的配置要求。
        self._response_synthesizer = response_synthesizer or get_response_synthesizer(
            llm=Settings.llm,
            callback_manager=callback_manager or Settings.callback_manager,
        )

        self._node_postprocessors = node_postprocessors or []
        callback_manager = (
            callback_manager or self._response_synthesizer.callback_manager
        )
        # 【中文研读】处理器按用户提供的列表顺序执行或配置；列表顺序就是管道顺序。
        for node_postprocessor in self._node_postprocessors:
            node_postprocessor.callback_manager = callback_manager
        super().__init__(callback_manager=callback_manager)

    # 【中文研读】方法职责：暴露合成器为提示子模块，方便上层访问和调整回答模板。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：PromptMixinType。结果的业务含义与失败分支见下面处理流程。
    def _get_prompt_modules(self) -> PromptMixinType:
        """Get prompt sub-modules."""
        # 【中文研读】处理流程：暴露合成器为提示子模块，方便上层访问和调整回答模板。
        return {"response_synthesizer": self._response_synthesizer}

    # 【中文研读】方法职责：将大量便捷参数转换为真正组件后调用构造器
    # 【中文研读】输入参数：retriever（被装配的检索器）；llm（回答或查询生成模型）；response_synthesizer（回答合成组件）；node_postprocessors（顺序执行的候选处理器）；callback_manager（运行事件与回调管理器）；response_mode（回答合成模式）；text_qa_template（初次问答提示模板）；refine_template（追加材料时的回答修订模板）；summary_template（摘要提示模板）；simple_template（简单回答提示模板）；chat_content_qa_template（多模态问答模板）；chat_content_refine_template（多模态修订模板）；output_cls（结构化回答的模型类型）；use_async（是否选择异步执行路径）；streaming（是否流式返回回答）；verbose（是否输出调试信息）；multimodal（是否按多模态内容块合成）；**kwargs（透传选项）。
    # 【中文研读】返回约定：'RetrieverQueryEngine'。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def from_args(
        cls,
        retriever: BaseRetriever,
        llm: Optional[LLM] = None,
        response_synthesizer: Optional[BaseSynthesizer] = None,
        node_postprocessors: Optional[List[BaseNodePostprocessor]] = None,
        callback_manager: Optional[CallbackManager] = None,
        # response synthesizer args
        response_mode: ResponseMode = ResponseMode.COMPACT,
        text_qa_template: Optional[BasePromptTemplate] = None,
        refine_template: Optional[BasePromptTemplate] = None,
        summary_template: Optional[BasePromptTemplate] = None,
        simple_template: Optional[BasePromptTemplate] = None,
        chat_content_qa_template: Optional[BasePromptTemplate] = None,
        chat_content_refine_template: Optional[BasePromptTemplate] = None,
        output_cls: Optional[Type[BaseModel]] = None,
        use_async: bool = False,
        streaming: bool = False,
        verbose: bool = False,
        multimodal: bool = False,
        **kwargs: Any,
    ) -> "RetrieverQueryEngine":
        """
        Initialize a RetrieverQueryEngine object.".

        Args:
            retriever (BaseRetriever): A retriever object.
            llm (Optional[LLM]): An instance of an LLM.
            response_synthesizer (Optional[BaseSynthesizer]): An instance of a response
                synthesizer.
            node_postprocessors (Optional[List[BaseNodePostprocessor]]): A list of
                node postprocessors.
            callback_manager (Optional[CallbackManager]): A callback manager.
            response_mode (ResponseMode): A ResponseMode object.
            text_qa_template (Optional[BasePromptTemplate]): A BasePromptTemplate
                object.
            refine_template (Optional[BasePromptTemplate]): A BasePromptTemplate object.
            summary_template (Optional[BasePromptTemplate]): A BasePromptTemplate object.
            simple_template (Optional[BasePromptTemplate]): A BasePromptTemplate object.
            chat_content_qa_template (Optional[BasePromptTemplate]): Multimodal QA
                prompt used when ``multimodal=True``.
            chat_content_refine_template (Optional[BasePromptTemplate]): Multimodal
                refine prompt used when ``multimodal=True``.
            output_cls (Optional[Type[BaseModel]]): The pydantic model to pass to the
                response synthesizer.
            use_async (bool): Whether to use async.
            streaming (bool): Whether to use streaming.
            verbose (bool): Whether to print verbose output.
            multimodal (bool): If True, configure the synthesizer to consume
                multimodal content blocks from retrieved nodes.

        """
        # 【中文研读】处理流程：解析模型，构建合成器，选回调，最终统一装配引擎。
        llm = llm or Settings.llm

        response_synthesizer = response_synthesizer or get_response_synthesizer(
            llm=llm,
            text_qa_template=text_qa_template,
            refine_template=refine_template,
            summary_template=summary_template,
            simple_template=simple_template,
            chat_content_qa_template=chat_content_qa_template,
            chat_content_refine_template=chat_content_refine_template,
            response_mode=response_mode,
            output_cls=output_cls,
            use_async=use_async,
            streaming=streaming,
            verbose=verbose,
            multimodal=multimodal,
        )

        callback_manager = callback_manager or Settings.callback_manager

        return cls(
            retriever=retriever,
            response_synthesizer=response_synthesizer,
            callback_manager=callback_manager,
            node_postprocessors=node_postprocessors,
        )

    # 【中文研读】方法职责：顺序执行后处理器
    # 【中文研读】输入参数：nodes（候选节点列表）；query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    def _apply_node_postprocessors(
        self, nodes: List[NodeWithScore], query_bundle: QueryBundle
    ) -> List[NodeWithScore]:
        # 【中文研读】处理流程：每个处理器接收上一处理器的结果，顺序会改变筛选与排序效果。
        # 【中文研读】处理器按用户提供的列表顺序执行或配置；列表顺序就是管道顺序。
        for node_postprocessor in self._node_postprocessors:
            nodes = node_postprocessor.postprocess_nodes(
                nodes, query_bundle=query_bundle
            )
        return nodes

    # 【中文研读】方法职责：按列表顺序 await 后处理器
    # 【中文研读】输入参数：nodes（候选节点列表）；query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
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

    # 【中文研读】方法职责：只执行检索与后处理
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    def retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        # 【中文研读】处理流程：先调用底层检索器，再串联处理器，返回节点，不调用答案合成。
        # 【中文研读】检索器可以是单路向量检索器，也可以是融合多个业务来源的组合检索器。
        nodes = self._retriever.retrieve(query_bundle)
        return self._apply_node_postprocessors(nodes, query_bundle=query_bundle)

    # 【中文研读】方法职责：异步检索与后处理入口
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    async def aretrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        # 【中文研读】处理流程：等待检索完成后再按序处理，返回候选列表。
        # 【中文研读】统一异步接口隐藏具体来源，但是否真正非阻塞仍由具体实现决定。
        nodes = await self._retriever.aretrieve(query_bundle)
        return await self._async_apply_node_postprocessors(
            nodes, query_bundle=query_bundle
        )

    # 【中文研读】方法职责：构造一个换了检索器的新引擎
    # 【中文研读】输入参数：retriever（被装配的检索器）。
    # 【中文研读】返回约定：'RetrieverQueryEngine'。结果的业务含义与失败分支见下面处理流程。
    def with_retriever(self, retriever: BaseRetriever) -> "RetrieverQueryEngine":
        # 【中文研读】处理流程：合成器、回调及处理器引用复用，不是深拷贝所有组件。
        return RetrieverQueryEngine(
            retriever=retriever,
            response_synthesizer=self._response_synthesizer,
            callback_manager=self.callback_manager,
            node_postprocessors=self._node_postprocessors,
        )

    # 【中文研读】方法职责：使用已有候选生成答案
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）；nodes（候选节点列表）；additional_source_nodes（附加的来源节点）。
    # 【中文研读】返回约定：RESPONSE_TYPE。结果的业务含义与失败分支见下面处理流程。
    def synthesize(
        self,
        query_bundle: QueryBundle,
        nodes: List[NodeWithScore],
        additional_source_nodes: Optional[Sequence[NodeWithScore]] = None,
    ) -> RESPONSE_TYPE:
        # 【中文研读】处理流程：查询、节点和可选附加来源转交合成器，此处不重新检索。
        return self._response_synthesizer.synthesize(
            query=query_bundle,
            nodes=nodes,
            additional_source_nodes=additional_source_nodes,
        )

    # 【中文研读】方法职责：异步使用已有候选生成答案
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）；nodes（候选节点列表）；additional_source_nodes（附加的来源节点）。
    # 【中文研读】返回约定：RESPONSE_TYPE。结果的业务含义与失败分支见下面处理流程。
    async def asynthesize(
        self,
        query_bundle: QueryBundle,
        nodes: List[NodeWithScore],
        additional_source_nodes: Optional[Sequence[NodeWithScore]] = None,
    ) -> RESPONSE_TYPE:
        # 【中文研读】处理流程：等待合成器完成，返回其响应类型。
        return await self._response_synthesizer.asynthesize(
            query=query_bundle,
            nodes=nodes,
            additional_source_nodes=additional_source_nodes,
        )

    # 【中文研读】方法职责：完整同步查询
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：RESPONSE_TYPE。结果的业务含义与失败分支见下面处理流程。
    @dispatcher.span
    def _query(self, query_bundle: QueryBundle) -> RESPONSE_TYPE:
        """Answer a query."""
        # 【中文研读】处理流程：打开查询事件，执行检索和后处理，把节点交给合成器，再记录最终回答。
        with self.callback_manager.event(
            CBEventType.QUERY, payload={EventPayload.QUERY_STR: query_bundle.query_str}
        ) as query_event:
            nodes = self.retrieve(query_bundle)
            # 【中文研读】到这里才生成回答；P3 的 ContextPack 应在自己的最终核验与预算流程后交付，不以回答成功代替业务验收。
            response = self._response_synthesizer.synthesize(
                query=query_bundle,
                nodes=nodes,
            )
            query_event.on_end(payload={EventPayload.RESPONSE: response})

        return response

    # 【中文研读】方法职责：完整异步查询
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：RESPONSE_TYPE。结果的业务含义与失败分支见下面处理流程。
    @dispatcher.span
    async def _aquery(self, query_bundle: QueryBundle) -> RESPONSE_TYPE:
        """Answer a query."""
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

    # 【中文研读】方法职责：读取并返回 self._retriever；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：BaseRetriever。结果的业务含义与失败分支见下面处理流程。
    @property
    def retriever(self) -> BaseRetriever:
        """Get the retriever object."""
        # 【中文研读】处理流程：读取并返回 self._retriever；此入口不发起检索或存储写入。
        return self._retriever
