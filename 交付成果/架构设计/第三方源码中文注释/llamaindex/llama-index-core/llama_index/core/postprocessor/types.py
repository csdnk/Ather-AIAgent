# 【中文研读】阅读主线：对候选列表执行可替换的处理器。公共层统一查询参数，子类实现筛选、排序等具体逻辑；多个处理器的串联在 QueryEngine 中完成。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional

from llama_index.core.bridge.pydantic import Field, ConfigDict
from llama_index.core.callbacks import CallbackManager
from llama_index.core.instrumentation import DispatcherSpanMixin
from llama_index.core.prompts.mixin import PromptDictType, PromptMixinType
from llama_index.core.schema import BaseComponent, NodeWithScore, QueryBundle


# 【中文研读】类型职责：候选后处理抽象接口；输入和输出都是节点列表，便于按顺序串联。
class BaseNodePostprocessor(BaseComponent, DispatcherSpanMixin, ABC):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    callback_manager: CallbackManager = Field(
        default_factory=CallbackManager, exclude=True
    )

    # 【中文研读】方法职责：默认后处理器没有提示词，返回空映射。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：PromptDictType。结果的业务含义与失败分支见下面处理流程。
    def _get_prompts(self) -> PromptDictType:
        """Get prompts."""
        # set by default since most postprocessors don't require prompts
        # 【中文研读】处理流程：默认后处理器没有提示词，返回空映射。
        return {}

    # 【中文研读】方法职责：空的提示更新钩子；需要模型提示的子类可覆盖。
    # 【中文研读】输入参数：prompts。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def _update_prompts(self, prompts: PromptDictType) -> None:
        """Update prompts."""

    # 【中文研读】方法职责：默认不暴露嵌套提示组件。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：PromptMixinType。结果的业务含义与失败分支见下面处理流程。
    def _get_prompt_modules(self) -> PromptMixinType:
        """Get prompt modules."""
        # 【中文研读】处理流程：默认不暴露嵌套提示组件。
        return {}

    # implement class_name so users don't have to worry about it when extending
    # 【中文研读】方法职责：读取并返回 'BaseNodePostprocessor'；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def class_name(cls) -> str:
        # 【中文研读】处理流程：读取并返回 'BaseNodePostprocessor'；此入口不发起检索或存储写入。
        return "BaseNodePostprocessor"

    # 【中文研读】方法职责：同步统一入口
    # 【中文研读】输入参数：nodes（候选节点列表）；query_bundle（包装后的查询文本及可选向量）；query_str（查询文本）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    def postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
        query_str: Optional[str] = None,
    ) -> List[NodeWithScore]:
        """Postprocess nodes."""
        # 【中文研读】处理流程：query_str 与 query_bundle 互斥，必要时包装文本，最后交给子类 _postprocess_nodes。
        # 【中文研读】拒绝同时给出两份查询，避免处理器不知道该以哪个输入为准。
        if query_str is not None and query_bundle is not None:
            raise ValueError("Cannot specify both query_str and query_bundle")
        # 【中文研读】仅给字符串时补成统一对象；未给查询的处理器仍可只处理节点。
        elif query_str is not None:
            query_bundle = QueryBundle(query_str)
        else:
            pass
        # 【中文研读】此处进入子类业务逻辑；同一个返回类型让下一处理器可直接接上。
        return self._postprocess_nodes(nodes, query_bundle)

    # 【中文研读】方法职责：抽象业务扩展点；子类实现过滤或排序，基类没有自动授权、版本校验或预算处理。
    # 【中文研读】输入参数：nodes（候选节点列表）；query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    @abstractmethod
    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        """Postprocess nodes."""

    # 【中文研读】方法职责：异步统一入口
    # 【中文研读】输入参数：nodes（候选节点列表）；query_bundle（包装后的查询文本及可选向量）；query_str（查询文本）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    async def apostprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
        query_str: Optional[str] = None,
    ) -> List[NodeWithScore]:
        """Postprocess nodes (async)."""
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

    # 【中文研读】方法职责：默认将同步后处理放入线程执行
    # 【中文研读】输入参数：nodes（候选节点列表）；query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    async def _apostprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        """Postprocess nodes (async)."""
        # 【中文研读】处理流程：asyncio.to_thread 避免直接阻塞事件循环，但取消等待不等于强行停止线程工作。
        # 【中文研读】线程适配只提供执行方式，不提供持久化检查点、幂等或重试。
        return await asyncio.to_thread(self._postprocess_nodes, nodes, query_bundle)
