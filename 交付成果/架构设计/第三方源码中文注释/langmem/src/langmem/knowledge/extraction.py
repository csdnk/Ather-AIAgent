# 【中文研读】阅读主线：create_memory_manager → MemoryManager.ainvoke → 准备消息和已有记忆 → trustcall 模型提取 → 过滤候选。MemoryStoreManager 另行加入真实 Store 读写，P3 不把该读写当自身事务。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
import asyncio
import datetime
import typing
import uuid

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from langchain_core.runnables.config import get_executor_for_config
from langgraph.store.base import (
    NOT_PROVIDED,
    BaseStore,
    NotProvided,
)
from langgraph.store.base import (
    Item as BaseItem,
)
from langgraph.store.base import (
    SearchItem as BaseSearchItem,
)
from langgraph.utils.config import ensure_config, get_store
from pydantic import BaseModel, Field
from trustcall import create_extractor
from typing_extensions import TypedDict

from langmem import utils
from langmem.knowledge.tools import create_search_memory_tool

## LangGraph Tools

# ```python setup
# from langmem import create_memory_store_manager
# from langgraph.store.memory import InMemoryStore
# manager = create_memory_store_manager(
#     "anthropic:claude-3-5-sonnet-latest",
#     namespace=("chat",),
#     store=InMemoryStore(),
# )
# ```  # end-setup


# 【中文研读】类型职责：存储记录包装，允许 value 是 Pydantic 模型或普通字典；导出时把模型转成 JSON 可表示的数据。
class Item(BaseItem):
    value: BaseModel | dict[str, typing.Any]

    # 【中文研读】方法职责：把记录导出为普通字典
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：dict。结果的业务含义与失败分支见下面处理流程。
    def dict(self) -> dict:
        # 【中文研读】处理流程：先使用父类结果，仅当 value 为 Pydantic 模型时转为 JSON 兼容数据，其余记录信息保留。
        result = super().dict()
        if isinstance(self.value, BaseModel):
            result["value"] = self.value.model_dump(mode="json")
        return result


# 【中文研读】类型职责：带检索属性的存储记录包装；保留 namespace、key、时间和检索分数。
class SearchItem(BaseSearchItem):
    value: BaseModel | dict[str, typing.Any]

    # 【中文研读】方法职责：把记录导出为普通字典
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：dict。结果的业务含义与失败分支见下面处理流程。
    def dict(self) -> dict:
        # 【中文研读】处理流程：先使用父类结果，仅当 value 为 Pydantic 模型时转为 JSON 兼容数据，其余记录信息保留。
        result = super().dict()
        if isinstance(self.value, BaseModel):
            result["value"] = self.value.model_dump(mode="json")
        return result


# 【中文研读】类型职责：线程提取输入，仅声明 messages 字段，不执行持久化。
class MessagesState(TypedDict):
    messages: list[AnyMessage]


# 【中文研读】类型职责：记忆提取状态：消息、可选已有记忆及本次最大迭代次数。
class MemoryState(MessagesState):
    # 【中文研读】可选旧记忆输入，供模型判断更新对象；ID 来自调用方，P3 必须先验证所属范围。
    existing: typing.NotRequired[list[tuple[str, BaseModel]]]
    # 【中文研读】本次调用允许的最大模型迭代次数；运行代码缺省使用 1，不是持久任务重试次数。
    max_steps: int  # Default of 1


# 【中文研读】类型职责：默认会话摘要结构，包含标题和摘要，供模型结构化输出使用。
class SummarizeThread(BaseModel):
    title: str
    summary: str


# 【中文研读】类型职责：提取结果二元组：记忆 ID 与模型内容；并不自动代表 P3 正式提交的事实。
class ExtractedMemory(typing.NamedTuple):
    # 【中文研读】提取器使用的 ID；正式业务 ID 和版本由 P3 提交规则决定。
    id: str
    # 【中文研读】结构化候选或删除标记，调用方必须解释类型，不能一律当正文保存。
    content: BaseModel


S = typing.TypeVar("S", bound=type)


# 【中文研读】类型职责：默认候选 Schema，只有 content；其英文说明也会参与模型工具描述，不能为翻译而修改。
class Memory(BaseModel):
    """Call this tool once for each new memory you want to record. Use multi-tool calling to record multiple new memories."""

    content: str = Field(
        description="The memory as a well-written, standalone episode/fact/note/preference/etc."
        " Refer to the user's instructions for more information the preferred memory organization."
    )


# 【中文研读】方法职责：创建结构化会话摘要管道
# 【中文研读】输入参数：model（模型实例或模型标识）；schema（单个输出或数据结构定义）；instructions（传给提取模型的业务指令）。
# 【中文研读】返回约定：Runnable[MessagesState, SummarizeThread]。结果的业务含义与失败分支见下面处理流程。
@typing.overload
def create_thread_extractor(
    model: str,
    /,
    schema: None = None,
    instructions: str = "You are tasked with summarizing the following conversation.",
                                               # 【中文研读】处理流程：选默认或自定义 Schema，构建工具提取器与提示模板，串联消息拼接、模型调用和第一个结果提取；overload 版本仅描述类型。
) -> Runnable[MessagesState, SummarizeThread]: ...


# 【中文研读】方法职责：创建结构化会话摘要管道
# 【中文研读】输入参数：model（模型实例或模型标识）；schema（单个输出或数据结构定义）；instructions（传给提取模型的业务指令）。
# 【中文研读】返回约定：Runnable[MessagesState, S]。结果的业务含义与失败分支见下面处理流程。
@typing.overload
def create_thread_extractor(
    model: str,
    /,
    schema: S,
    instructions: str = "You are tasked with summarizing the following conversation.",
                                 # 【中文研读】处理流程：选默认或自定义 Schema，构建工具提取器与提示模板，串联消息拼接、模型调用和第一个结果提取；overload 版本仅描述类型。
) -> Runnable[MessagesState, S]: ...


# 【中文研读】方法职责：创建结构化会话摘要管道
# 【中文研读】输入参数：model（模型实例或模型标识）；schema（单个输出或数据结构定义）；instructions（传给提取模型的业务指令）。
# 【中文研读】返回约定：Runnable[MessagesState, BaseModel]。结果的业务含义与失败分支见下面处理流程。
def create_thread_extractor(
    model: str,
    /,
    schema: typing.Union[None, BaseModel, type] = None,
    instructions: str = "You are tasked with summarizing the following conversation.",
) -> Runnable[MessagesState, BaseModel]:
    """Creates a conversation thread summarizer using schema-based extraction.

    This function creates an asynchronous callable that takes conversation messages and produces
    a structured summary based on the provided schema. If no schema is provided, it uses a default
    schema with title and summary fields.

    Args:
        model (str): The chat model to use for summarization (name or instance)
        schema (Optional[Union[BaseModel, type]], optional): Pydantic model for structured output.
            Defaults to a simple summary schema with title and summary fields.
        instructions (str, optional): System prompt template for the summarization task.
            Defaults to a basic summarization instruction.

    Returns:
        extractor (Callable[[list], typing.Awaitable[typing.Any]]): Async callable that takes a list of messages and returns a structured summary

    ???+ example "Examples"
        ```python
        from langmem import create_thread_extractor

        summarizer = create_thread_extractor("gpt-4")

        messages = [
            {"role": "user", "content": "Hi, I'm having trouble with my account"},
            {
                "role": "assistant",
                "content": "I'd be happy to help. What seems to be the issue?",
            },
            {"role": "user", "content": "I can't reset my password"},
        ]

        summary = await summarizer.ainvoke({"messages": messages})
        print(summary.title)
        # Output: "Password Reset Assistance"
        print(summary.summary)
        # Output: "User reported issues with password reset process..."
        ```

    """
    # 【中文研读】处理流程：选默认或自定义 Schema，构建工具提取器与提示模板，串联消息拼接、模型调用和第一个结果提取；overload 版本仅描述类型。
    if schema is None:
        schema = SummarizeThread

    extractor = create_extractor(model, tools=[schema], tool_choice="any")

    template = ChatPromptTemplate.from_messages(
        [
            ("system", instructions),
            (
                "user",
                "Call the provided tool based on the conversation below:\n\n<conversation>{conversation}</conversation>",
            ),
        ]
    )

    # 【中文研读】方法职责：把消息列表拼成对话文本并保留其他输入字段
    # 【中文研读】输入参数：input（本次输入状态，具体字段见输入类型）。
    # 【中文研读】返回约定：dict。结果的业务含义与失败分支见下面处理流程。
    def merge_messages(input: dict) -> dict:
        # 【中文研读】处理流程：生成 conversation，移除原 messages 键，供提示模板填充。
        conversation = utils.get_conversation(input["messages"])

        return {"conversation": conversation} | {
            k: v for k, v in input.items() if k != "messages"
        }

    return (
        merge_messages | template | extractor | (lambda out: out["responses"][0])
    ).with_config({"run_name": "thread_extractor"})  # type: ignore


_MEMORY_INSTRUCTIONS = """You are a long-term memory manager maintaining a core store of semantic, procedural, and episodic memory. These memories power a life-long learning agent's core predictive model.

What should the agent learn from this interaction about the user, itself, or how it should act? Reflect on the input trajectory and current memories (if any).

1. **Extract & Contextualize**  
   - Identify essential facts, relationships, preferences, reasoning procedures, and context
   - Caveat uncertain or suppositional information with confidence levels (p(x)) and reasoning
   - Quote supporting information when necessary

2. **Compare & Update**  
   - Attend to novel information that deviates from existing memories and expectations.
   - Consolidate and compress redundant memories to maintain information-density; strengthen based on reliability and recency; maximize SNR by avoiding idle words.
   - Remove incorrect or redundant memories while maintaining internal consistency

3. **Synthesize & Reason**  
   - What can you conclude about the user, agent ("I"), or environment using deduction, induction, and abduction?
   - What patterns, relationships, and principles emerge about optimal responses?
   - What generalizations can you make?
   - Qualify conclusions with probabilistic confidence and justification

As the agent, record memory content exactly as you'd want to recall it when predicting how to act or respond. 
Prioritize retention of surprising (pattern deviation) and persistent (frequently reinforced) information, ensuring nothing worth remembering is forgotten and nothing false is remembered. Prefer dense, complete memories over overlapping ones."""


# 【中文研读】类型职责：多轮提取的完成信号工具；模型调用它表示本轮不必继续修订，不是存储事务完成。
class Done(BaseModel):
    """Only call this tool once you are done forming & consolidating memories.
    Before that, continue to refine existing memories by patching and removing them
    or create new ones."""

    pass


# 【中文研读】类型职责：Core 候选提取管理器；持有模型和 Schema，支持插入/更新/删除候选，不持有事实数据库。
class MemoryManager(Runnable[MemoryState, list[ExtractedMemory]]):
    # 【中文研读】方法职责：装配候选提取模型与规则
    # 【中文研读】输入参数：model（模型实例或模型标识）；schemas（允许输出的结构化类型集合）；instructions（传给提取模型的业务指令）；enable_inserts（是否允许新增候选）；enable_updates（是否允许更新候选）；enable_deletes（是否允许删除候选）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def __init__(
        self,
        model: str | BaseChatModel,
        *,
        schemas: typing.Sequence[typing.Union[BaseModel, type]] = (Memory,),
        instructions: str = _MEMORY_INSTRUCTIONS,
        enable_inserts: bool = True,
        enable_updates: bool = True,
        enable_deletes: bool = False,
    ):
        # 【中文研读】处理流程：字符串模型名交给 init_chat_model，保存 Schema、提示及操作开关；初始化不会把候选写入数据库。
        self.model = (
            model if isinstance(model, BaseChatModel) else init_chat_model(model)
        )
        self.schemas = schemas or (Memory,)
        self.instructions = instructions
        self.enable_inserts = enable_inserts
        self.enable_updates = enable_updates
        self.enable_deletes = enable_deletes

    # 【中文研读】方法职责：异步执行有限轮次的候选提取
    # 【中文研读】输入参数：input（本次输入状态，具体字段见输入类型）；config（本次运行配置）；**kwargs（透传选项）。
    # 【中文研读】返回约定：list[ExtractedMemory]。结果的业务含义与失败分支见下面处理流程。
    async def ainvoke(
        self,
        input: MemoryState,
        config: typing.Optional[RunnableConfig] = None,
        **kwargs: typing.Any,
    ) -> list[ExtractedMemory]:
        # 【中文研读】处理流程：准备输入 → 创建提取器 → 逐轮调用模型并合并 ID → 判断 Done → 必要时追加工具反馈 → 过滤最终候选。
        max_steps = input.get("max_steps")
        if max_steps is None:
            max_steps = 1
        messages = input["messages"]
        existing = input.get("existing")
        prepared_messages = self._prepare_messages(messages, max_steps)
        prepared_existing = self._prepare_existing(existing)
        # Track external memory IDs (those passed in from outside)
        # 【中文研读】记录调用前已存在的 ID，最终过滤时用于区别真实旧记忆删除与本轮临时对象撤销。
        external_ids = {mem_id for mem_id, _, _ in prepared_existing}

        extractor = create_extractor(
            self.model,
            tools=list(self.schemas),
            enable_inserts=self.enable_inserts,
            enable_updates=self.enable_updates,
            enable_deletes=self.enable_deletes,
            existing_schema_policy=False,
        )
        # initial payload uses the full prepared_existing list
        payload = {"messages": prepared_messages, "existing": prepared_existing}
        # Use a dict to record the latest update for each memory id.
        results: dict[str, BaseModel] = {}

        # 【中文研读】本地迭代循环：每轮模型调用都可能失败，results 只在内存中，不支持进程重启后自动续跑。
        for i in range(max_steps):
            # 【中文研读】从第二轮起加入 Done 工具，让模型有明确结束信号；第一轮仍以记忆 Schema 为主。
            if i == 1:
                extractor = create_extractor(
                    self.model,
                    tools=list(self.schemas) + [Done],
                    enable_inserts=self.enable_inserts,
                    enable_updates=self.enable_updates,
                    enable_deletes=self.enable_deletes,
                    existing_schema_policy=False,
                )
            # 【中文研读】实际模型提取边界：由 trustcall 组织结构化工具响应；格式正确不等于事实已获业务验证。
            response = await extractor.ainvoke(payload, config=config)
            is_done = False
            step_results = {}
            for r, rmeta in zip(response["responses"], response["response_metadata"]):
                if hasattr(r, "__repr_name__") and r.__repr_name__() == "Done":
                    is_done = True
                    continue
                mem_id = (
                    r.json_doc_id
                    if hasattr(r, "__repr_name__") and r.__repr_name__() == "RemoveDoc"
                    else rmeta.get("json_doc_id", str(uuid.uuid4()))
                )
                # 【中文研读】按确定的 ID 保存本轮结果，同 ID 后写覆盖前写；更新通过 response_metadata 中的 ID 与已有对象关联。
                step_results[mem_id] = r
            # 【中文研读】合并本轮输出；随后补回未改变的旧记忆，因此返回集合并不只是新增内容。
            results.update(step_results)

            for mem_id, _, mem in prepared_existing:
                if mem_id not in results:
                    results[mem_id] = mem

            ai_msg = response["messages"][-1]
            # 【中文研读】模型声明完成或没有工具调用时停止；这代表提取终止，不代表业务事实已持久保存。
            if is_done or not ai_msg.tool_calls:
                break
            # 【中文研读】只有还允许下一轮时才构建工具反馈和下一轮 existing，避免无用地继续生成上下文。
            if i < max_steps - 1:
                actions = [
                    (
                        "updated"
                        if rmeta.get("json_doc_id")
                        else (
                            "deleted"
                            if hasattr(r, "__repr_name__")
                            and r.__repr_name__() == "RemoveDoc"
                            else "inserted"
                        )
                    )
                    for r, rmeta in zip(
                        response["responses"], response["response_metadata"]
                    )
                ]
                prepared_messages = (
                    prepared_messages
                    + [response["messages"][-1]]
                    + [
                        {
                            "role": "tool",
                            "content": f"Memory {rid} {action}.",
                            "tool_call_id": tc["id"],
                        }
                        for tc, ((rid, _), action) in zip(
                            ai_msg.tool_calls, zip(list(step_results.items()), actions)
                        )
                    ]
                )
                # For the next iteration payload, drop all removal objects.
                payload = {
                    "messages": prepared_messages,
                    "existing": self._filter_response(
                        list(results.items()), external_ids, exclude_removals=True
                    ),
                }

        # For the final response, include removals only if they refer to an external memory.
        return self._filter_response(
            list(results.items()), external_ids, exclude_removals=False
        )

    # 【中文研读】方法职责：同步执行同样的多轮候选提取
    # 【中文研读】输入参数：input（本次输入状态，具体字段见输入类型）；config（本次运行配置）；**kwargs（透传选项）。
    # 【中文研读】返回约定：list[ExtractedMemory]。结果的业务含义与失败分支见下面处理流程。
    def invoke(
        self,
        input: MemoryState,
        config: typing.Optional[RunnableConfig] = None,
        **kwargs: typing.Any,
    ) -> list[ExtractedMemory]:
        # 【中文研读】处理流程：保存本次 results 字典，每轮覆盖同 ID 内容，最后返回候选；这是一次调用内的工作状态，不是重启检查点。
        max_steps = input.get("max_steps")
        if max_steps is None:
            max_steps = 1
        messages = input["messages"]
        existing = input.get("existing")
        prepared_messages = self._prepare_messages(messages, max_steps)
        prepared_existing = self._prepare_existing(existing)
        # Track external memory IDs (those passed in from outside)
        # 【中文研读】记录调用前已存在的 ID，最终过滤时用于区别真实旧记忆删除与本轮临时对象撤销。
        external_ids = {mem_id for mem_id, _, _ in prepared_existing}

        extractor = create_extractor(
            self.model,
            tools=list(self.schemas),
            enable_inserts=self.enable_inserts,
            enable_updates=self.enable_updates,
            enable_deletes=self.enable_deletes,
            existing_schema_policy=False,
        )
        payload = {"messages": prepared_messages, "existing": prepared_existing}
        # Use a dict to record the latest update for each memory id.
        results: dict[str, BaseModel] = {}

        # 【中文研读】本地迭代循环：每轮模型调用都可能失败，results 只在内存中，不支持进程重启后自动续跑。
        for i in range(max_steps):
            # 【中文研读】从第二轮起加入 Done 工具，让模型有明确结束信号；第一轮仍以记忆 Schema 为主。
            if i == 1:
                extractor = create_extractor(
                    self.model,
                    tools=list(self.schemas) + [Done],
                    enable_inserts=self.enable_inserts,
                    enable_updates=self.enable_updates,
                    enable_deletes=self.enable_deletes,
                    existing_schema_policy=False,
                )
            # 【中文研读】同步模型调用边界；成功只产生本轮候选，还没有 Store 事务。
            response = extractor.invoke(payload, config=config)
            is_done = False
            step_results: dict[str, BaseModel] = {}
            for r, rmeta in zip(response["responses"], response["response_metadata"]):
                if hasattr(r, "__repr_name__") and r.__repr_name__() == "Done":
                    is_done = True
                    continue
                mem_id = (
                    r.json_doc_id
                    if (
                        hasattr(r, "__repr_name__") and r.__repr_name__() == "RemoveDoc"
                    )
                    else rmeta.get("json_doc_id", str(uuid.uuid4()))
                )
                # 【中文研读】按确定的 ID 保存本轮结果，同 ID 后写覆盖前写；更新通过 response_metadata 中的 ID 与已有对象关联。
                step_results[mem_id] = r
            # 【中文研读】合并本轮输出；随后补回未改变的旧记忆，因此返回集合并不只是新增内容。
            results.update(step_results)

            # Ensure any memory from the initial payload that hasn't been updated is retained.
            for mem_id, _, mem in prepared_existing:
                if mem_id not in results:
                    results[mem_id] = mem

            ai_msg = response["messages"][-1]
            # 【中文研读】模型声明完成或没有工具调用时停止；这代表提取终止，不代表业务事实已持久保存。
            if is_done or not ai_msg.tool_calls:
                break
            # 【中文研读】只有还允许下一轮时才构建工具反馈和下一轮 existing，避免无用地继续生成上下文。
            if i < max_steps - 1:
                actions = [
                    (
                        "updated"
                        if rmeta.get("json_doc_id")
                        else (
                            "deleted"
                            if (
                                hasattr(r, "__repr_name__")
                                and r.__repr_name__() == "RemoveDoc"
                            )
                            else "inserted"
                        )
                    )
                    for r, rmeta in zip(
                        response["responses"], response["response_metadata"]
                    )
                ]
                prepared_messages = (
                    prepared_messages
                    + [response["messages"][-1]]
                    + [
                        {
                            "role": "tool",
                            "content": f"Memory {rid} {action}.",
                            "tool_call_id": tc["id"],
                        }
                        for tc, ((rid, _), action) in zip(
                            ai_msg.tool_calls, zip(list(step_results.items()), actions)
                        )
                    ]
                )
                payload = {
                    "messages": prepared_messages,
                    "existing": self._filter_response(
                        list(results.items()), external_ids, exclude_removals=True
                    ),
                }

        return self._filter_response(
            list(results.items()), external_ids, exclude_removals=False
        )

    # 【中文研读】方法职责：简便异步入口
    # 【中文研读】输入参数：messages（待处理对话消息）；existing（参与比较的已有记忆）。
    # 【中文研读】返回约定：list[ExtractedMemory]。结果的业务含义与失败分支见下面处理流程。
    async def __call__(
        self,
        messages: typing.Sequence[AnyMessage],
        existing: typing.Optional[typing.Sequence[ExtractedMemory]] = None,
    ) -> list[ExtractedMemory]:
        # 【中文研读】处理流程：把 messages 和可选 existing 包装成 MemoryState，然后调用 ainvoke。
        input: MemoryState = {"messages": messages}
        if existing is not None:
            input["existing"] = existing
        return await self.ainvoke(input)

    # 【中文研读】方法职责：构建供模型读取的提取提示
    # 【中文研读】输入参数：messages（待处理对话消息）；max_steps（本次模型迭代上限）。
    # 【中文研读】返回约定：list[dict]。结果的业务含义与失败分支见下面处理流程。
    def _prepare_messages(
        self, messages: list[AnyMessage], max_steps: int = 1
    ) -> list[dict]:
        # 【中文研读】处理流程：给对话包上随机会话标签，按最大轮次补充说明，再拼接系统和用户消息；标签不是业务授权证明。
        id_ = str(uuid.uuid4())
        session = (
            f"\n\n<session_{id_}>\n{utils.get_conversation(messages)}\n</session_{id_}>"
        )
        if max_steps > 1:
            session = f"{session}\n\nYou have a maximum of {max_steps - 1} attempts"
            " to form and consolidate memories from this session."
        return [
            {"role": "system", "content": "You are a memory subroutine for an AI."},
            {
                "role": "user",
                "content": (
                    f"{self.instructions}\n\nEnrich, prune, and organize memories based on any new information. "
                    f"If an existing memory is incorrect or outdated, update it based on the new information. "
                    f"All operations must be done in single parallel multi-tool call."
                    f" Avoid duplicate extractions. {session}"
                ),
            },
        ]

    # 【中文研读】方法职责：把不同形式的已有记忆转为统一三元组
    # 【中文研读】输入参数：existing（参与比较的已有记忆）。
    # 【中文研读】返回约定：list[tuple[str, str, typing.Any]]。结果的业务含义与失败分支见下面处理流程。
    def _prepare_existing(
        self,
        existing: typing.Optional[
            typing.Union[
                list[str], list[tuple[str, BaseModel]], list[tuple[str, str, dict]]
            ]
        ],
    ) -> list[tuple[str, str, typing.Any]]:
        # 【中文研读】处理流程：None 变空列表，字符串创建临时 ID 与默认模型，三元组保留，二元组补类型名。
        if existing is None:
            return []
        # 【中文研读】纯字符串旧记忆没有稳定业务 ID，因此这里临时生成 ID；P3 精确版本更新不能依赖这种隐式身份。
        if all(isinstance(ex, str) for ex in existing):
            MemoryModel = self.schemas[0]
            return [
                (str(uuid.uuid4()), "Memory", MemoryModel(content=ex))
                for ex in existing
            ]
        result = []
        for e in existing:
            if isinstance(e, (tuple, list)) and len(e) == 3:
                result.append(tuple(e))
            else:
                # Assume a two-element tuple: (id, value)
                id_, value = e[0], e[1]
                kind = (
                    value.__repr_name__() if isinstance(value, BaseModel) else "__any__"
                )
                result.append((id_, kind, value))
        return result

    # 【中文研读】方法职责：整理新增、更新及删除候选
    # 【中文研读】输入参数：memories（待整理的提取结果）；external_ids（调用前就存在的记忆 ID 集合）；exclude_removals（是否移除删除标记）。
    # 【中文研读】返回约定：list[ExtractedMemory]。结果的业务含义与失败分支见下面处理流程。
    @staticmethod
    def _filter_response(
        memories: list[ExtractedMemory],
        external_ids: set[str],
        exclude_removals: bool = False,
    ) -> list[ExtractedMemory]:
        """
        When exclude_removals is True (for the next iteration payload),
        drop any memory whose content is a RemoveDoc.
        When False (final response), drop removal objects only for internal memories.
        """
        # 【中文研读】处理流程：迭代中可排除所有删除项，最终只保留针对外部已有 ID 的删除，抑制本轮新建又删除的临时对象。
        results = []
        for rid, value in memories:
            is_removal = (
                hasattr(value, "__repr_name__") and value.__repr_name__() == "RemoveDoc"
            )
            # 【中文研读】迭代输入与最终输出对删除标记采用不同规则，避免将已删除对象再作为当前记忆送给模型。
            if exclude_removals:
                if is_removal:
                    continue
            else:
                # Final response: if this is a removal *and* its id is not external, skip it.
                if is_removal and (rid not in external_ids):
                    continue
            results.append(ExtractedMemory(id=rid, content=value))
        return results


# 【中文研读】方法职责：Core 工厂，返回 MemoryManager
# 【中文研读】输入参数：model（模型实例或模型标识）；schemas（允许输出的结构化类型集合）；instructions（传给提取模型的业务指令）；enable_inserts（是否允许新增候选）；enable_updates（是否允许更新候选）；enable_deletes（是否允许删除候选）。
# 【中文研读】返回约定：Runnable[MemoryState, list[ExtractedMemory]]。结果的业务含义与失败分支见下面处理流程。
def create_memory_manager(
    model: str | BaseChatModel,
    /,
    *,
    schemas: typing.Sequence[S] = (Memory,),
    instructions: str = _MEMORY_INSTRUCTIONS,
    enable_inserts: bool = True,
    enable_updates: bool = True,
    enable_deletes: bool = False,
) -> Runnable[MemoryState, list[ExtractedMemory]]:
    """Create a memory manager that processes conversation messages and generates structured memory entries.

    This function creates an async callable that analyzes conversation messages and existing memories
    to generate or update structured memory entries. It can identify implicit preferences,
    important context, and key information from conversations, organizing them into
    well-structured memories that can be used to improve future interactions.

    The manager supports both unstructured string-based memories and structured memories
    defined by Pydantic models, all automatically persisted to the configured storage.

    Args:
        model (Union[str, BaseChatModel]): The language model to use for memory enrichment.
            Can be a model name string or a BaseChatModel instance.
        schemas (Optional[list]): List of Pydantic models defining the structure of memory
            entries. Each model should define the fields and validation rules for a type
            of memory. If None, uses unstructured string-based memories. Defaults to None.
        instructions (str, optional): Custom instructions for memory generation and
            organization. These guide how the model extracts and structures information
            from conversations. Defaults to predefined memory instructions.
        enable_inserts (bool, optional): Whether to allow creating new memory entries.
            When False, the manager will only update existing memories. Defaults to True.
        enable_updates (bool, optional): Whether to allow updating existing memories
            that are outdated or contradicted by new information. Defaults to True.
        enable_deletes (bool, optional): Whether to allow deleting existing memories
            that are outdated or contradicted by new information. Defaults to False.

    Returns:
        manager: An runnable that processes conversations and returns `ExtractedMemory`'s. The function signature depends on whether schemas are provided

    ???+ example "Examples"
        Basic unstructured memory enrichment:
        ```python
        from langmem import create_memory_manager

        manager = create_memory_manager("anthropic:claude-3-5-sonnet-latest")

        conversation = [
            {"role": "user", "content": "I prefer dark mode in all my apps"},
            {"role": "assistant", "content": "I'll remember that preference"},
        ]

        # Extract memories from conversation
        memories = await manager(conversation)
        print(memories[0][1])  # First memory's content
        # Output: "User prefers dark mode for all applications"
        ```

        Structured memory enrichment with Pydantic models:
        ```python
        from pydantic import BaseModel
        from langmem import create_memory_manager

        class PreferenceMemory(BaseModel):
            \"\"\"Store the user's preference\"\"\"
            category: str
            preference: str
            context: str

        manager = create_memory_manager(
            "anthropic:claude-3-5-sonnet-latest",
            schemas=[PreferenceMemory]
        )

        # Same conversation, but with structured output
        conversation = [
            {"role": "user", "content": "I prefer dark mode in all my apps"},
            {"role": "assistant", "content": "I'll remember that preference"}
        ]
        memories = await manager(conversation)
        print(memories[0][1])
        # Output:
        # PreferenceMemory(
        #     category="ui",
        #     preference="dark_mode",
        #     context="User explicitly stated preference for dark mode in all applications"
        # )
        ```

        Working with existing memories:
        ```python
        conversation = [
            {
                "role": "user",
                "content": "Actually I changed my mind, dark mode hurts my eyes",
            },
            {"role": "assistant", "content": "I'll update your preference"},
        ]

        # The manager will upsert; working with the existing memory instead of always creating a new one
        updated_memories = await manager.ainvoke(
            {"messages": conversation, "existing": memories}
        )
        ```

        Insertion-only memories:
        ```python
        manager = create_memory_manager(
            "anthropic:claude-3-5-sonnet-latest",
            schemas=[PreferenceMemory],
            enable_updates=False,
            enable_deletes=False,
        )

        conversation = [
            {
                "role": "user",
                "content": "Actually I changed my mind, dark mode is the best mode",
            },
            {"role": "assistant", "content": "I'll update your preference"},
        ]

        # The manager will only create new memories
        updated_memories = await manager.ainvoke(
            {"messages": conversation, "existing": memories}
        )
        print(updated_memories)
        ```

        Providing multiple max steps for extraction and synthesis:
        ```python
        manager = create_memory_manager(
            "anthropic:claude-3-5-sonnet-latest",
            schemas=[PreferenceMemory],
        )

        conversation = [
            {"role": "user", "content": "I prefer dark mode in all my apps"},
            {"role": "assistant", "content": "I'll remember that preference"},
        ]

        # Set max steps for extraction and synthesis
        max_steps = 3
        memories = await manager.ainvoke(
            {"messages": conversation, "max_steps": max_steps}
        )
        print(memories)
        ```
    """

    # 【中文研读】处理流程：把模型、Schema、提示及操作开关原样交给构造器，不创建 Store。
    return MemoryManager(
        model,
        schemas=schemas,
        instructions=instructions,
        enable_inserts=enable_inserts,
        enable_updates=enable_updates,
        enable_deletes=enable_deletes,
    )


# 【中文研读】方法职责：创建模型主动生成搜索工具调用的管道
# 【中文研读】输入参数：model（模型实例或模型标识）；prompt（搜索提示模板）；namespace（存储命名空间或其模板）。
# 【中文研读】返回约定：Runnable[MessagesState, typing.Awaitable[list[SearchItem]]]。结果的业务含义与失败分支见下面处理流程。
def create_memory_searcher(
    model: str | BaseChatModel,
    prompt: str = "Search for distinct memories relevant to different aspects of the provided context.",
    *,
    namespace: tuple[str, ...] = ("memories", "{langgraph_user_id}"),
) -> Runnable[MessagesState, typing.Awaitable[list[SearchItem]]]:
    """Creates a memory search pipeline with automatic query generation.

    This function builds a pipeline that combines query generation, memory search,
    and result ranking into a single component. It uses the provided model to
    generate effective search queries based on conversation context.

    Args:
        model (Union[str, BaseChatModel]): The language model to use for search query generation.
            Can be a model name string or a BaseChatModel instance.
        prompt (str, optional): System prompt template for search assistant.
            Defaults to a basic search prompt.
        namespace: The namespace structure for organizing memories in LangGraph's BaseStore.
            Uses runtime configuration with placeholders like `{langgraph_user_id}`.
            See [Memory Namespaces](../concepts/conceptual_guide.md#memory-namespaces).
            Defaults to ("memories", "{langgraph_user_id}").

    ???+ note "Namespace Configuration"
        If the namespace has template variables "{variable_name}", they will be configured at
        runtime through the `config` parameter:
        ```python
        # Example: Search user's memories
        config = {"configurable": {"langgraph_user_id": "user-123"}}
        # Searches in namespace: ("memories", "user-123")

        # Example: Search team knowledge
        config = {"configurable": {"langgraph_user_id": "team-x"}}
        # Searches in namespace: ("memories", "team-x")
        ```

    Returns:
        searcher (Callable[[list], typing.Awaitable[typing.Any]]): A pipeline that takes conversation messages and returns sorted memory artifacts,
            ranked by relevance score.

    ???+ example "Examples"
        ```python
        from langmem import create_memory_searcher
        from langgraph.store.memory import InMemoryStore
        from langgraph.func import entrypoint

        store = InMemoryStore(
            index={
                "dims": 1536,
                "embed": "openai:text-embedding-3-small",
            }
        )
        user_id = "abcd1234"
        store.put(
            ("memories", user_id), key="preferences", value={"content": "I like sushi"}
        )
        searcher = create_memory_searcher(
            "openai:gpt-4o-mini", namespace=("memories", "{langgraph_user_id}")
        )


        @entrypoint(store=store)
        async def search_memories(messages: list):
            results = await searcher.ainvoke({"messages": messages})
            print(results[0].value["content"])
            # Output: "I like sushi"


        await search_memories.ainvoke(
            [{"role": "user", "content": "What do I like to eat?"}],
            config={"configurable": {"langgraph_user_id": user_id}},
        )
        ```

    """
    # 【中文研读】处理流程：模板 → 合并消息 → 模型生成调用 → 批量搜索 → 收集结果并排序。
    template = ChatPromptTemplate.from_messages(
        [
            ("system", prompt),
            ("placeholder", "{messages}"),
            ("user", "\n\nSearch for memories relevant to the above context."),
        ]
    )

    # Initialize model and search tool
    model_instance = (
        model if isinstance(model, BaseChatModel) else init_chat_model(model)
    )
    search_tool = create_search_memory_tool(
        namespace=namespace, response_format="content_and_artifact"
    )
    query_gen = model_instance.bind_tools([search_tool], tool_choice="search_memory")

    # 【中文研读】方法职责：合并各工具消息的检索产物
    # 【中文研读】输入参数：tool_messages（工具调用返回的消息列表）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def return_sorted(tool_messages: list):
        # 【中文研读】处理流程：按 namespace 与 key 去重，按 score 排序，缺失分数按 0 处理。
        artifacts = {
            (*item.namespace, item.key): item
            for msg in tool_messages
            for item in (msg.artifact or [])
        }
        return [
            v
            for v in sorted(
                artifacts.values(),
                key=lambda item: item.score if item.score is not None else 0,
                reverse=True,
            )
        ]

    # 【中文研读】方法职责：带分页和过滤搜索当前 namespace
    # 【中文研读】输入参数：msg（模型产生的工具调用消息）。
    # 【中文研读】返回约定：list[SearchItem]。结果的业务含义与失败分支见下面处理流程。
    def search(msg: AIMessage) -> list[SearchItem]:
        # 【中文研读】处理流程：调用 Store.search 并恢复 Schema；无结果且配置默认工厂时返回构造的默认项。
        return search_tool.batch(
            [tc for tc in msg.tool_calls if tc["name"] == "search_memory"]
        )

    # 【中文研读】方法职责：异步执行模型生成的搜索工具调用
    # 【中文研读】输入参数：msg（模型产生的工具调用消息）。
    # 【中文研读】返回约定：list[SearchItem]。结果的业务含义与失败分支见下面处理流程。
    async def search_async(msg: AIMessage) -> list[SearchItem]:
        # 【中文研读】处理流程：筛选工具名后调用 abatch，返回工具结果供后续整理。
        return await search_tool.abatch(
            [tc for tc in msg.tool_calls if tc["name"] == "search_memory"]
        )

    searcher = RunnableLambda(search, search_async)

    return (  # type: ignore
        template | utils.merge_message_runs | query_gen | searcher | return_sorted
    ).with_config({"run_name": "search_memory_pipeline"})


# 【中文研读】类型职责：附加提取阶段配置：指令、是否继续带原消息及允许的操作。
class MemoryPhase(TypedDict, total=False):
    instructions: str
    include_messages: bool
    enable_inserts: bool
    enable_deletes: bool


# 【中文研读】类型职责：Store 管理器的消息与步数输入；已有记忆由它自行查询取得。
class MemoryStoreManagerInput(TypedDict):
    """Input schema for MemoryStoreManager."""

    messages: list[AnyMessage]
    max_steps: int  # Default of 1


# 【中文研读】类型职责：在 Core 外组合旧记忆查询、可选默认记录、多阶段提取和 Store 写回；含外部副作用。
class MemoryStoreManager(Runnable[MemoryStoreManagerInput, list[dict]]):
    # 【中文研读】方法职责：装配有存储副作用的记忆管道
    # 【中文研读】输入参数：model（模型实例或模型标识）；schemas（允许输出的结构化类型集合）；default（无现有记录时的默认内容）；default_factory（按运行配置产生默认内容的函数）；instructions（传给提取模型的业务指令）；enable_inserts（是否允许新增候选）；enable_deletes（是否允许删除候选）；query_model（专用于生成搜索查询的模型）；query_limit（查询旧记忆的数量限制）；namespace（存储命名空间或其模板）；store（底层存储实例）；phases（附加加工阶段列表）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def __init__(
        self,
        model: str | BaseChatModel,
        /,
        *,
        schemas: list[S] | None = None,
        default: str | dict | S | None = None,
        default_factory: (
            typing.Callable[[RunnableConfig], str | dict | S] | None
        ) = None,
        instructions: str = _MEMORY_INSTRUCTIONS,
        enable_inserts: bool = True,
        enable_deletes: bool = True,
        query_model: str | BaseChatModel | None = None,
        query_limit: int = 5,
        namespace: tuple[str, ...] = ("memories", "{langgraph_user_id}"),
        store: BaseStore | None = None,
        phases: list[MemoryPhase] | None = None,
    ):
        # 【中文研读】处理流程：模型和 Schema → 默认值工厂互斥检查 → namespace/Store → Core Manager → 可选查询生成模型。
        self.model = (
            model if isinstance(model, BaseChatModel) else init_chat_model(model)
        )
        self.query_model = (
            None
            if query_model is None
            else (
                query_model
                if isinstance(query_model, BaseChatModel)
                else init_chat_model(query_model)
            )
        )
        self.schemas = schemas if schemas is not None else (Memory,)
        self.schema_name_map = {schema.__name__: schema for schema in self.schemas}
        # 【中文研读】默认值与默认工厂只能选一个；默认内容是配置产生，不能当作用户新提供的来源事实。
        self.default_factory = default_factory
        if default is not None:
            if self.default_factory is not None:
                raise ValueError("Cannot specify both default and default_factory")
            coerced = self._coerce_default(default, self.schemas)
            self.default_factory = lambda _: coerced
        self.instructions = instructions
        self.enable_inserts = enable_inserts
        self.enable_deletes = enable_deletes
        self.query_limit = query_limit
        self.phases = phases or []
        self.namespace = utils.NamespaceTemplate(namespace)
        self._store = store

        self.memory_manager = create_memory_manager(
            self.model,
            schemas=schemas,
            instructions=instructions,
            enable_inserts=enable_inserts,
            enable_deletes=enable_deletes,
        )
        self.search_tool = create_search_memory_tool(
            namespace=namespace,
            instructions="Queries should be formatted as hypothetical memories that would be relevant to the current conversation.",
        )
        self.query_gen = None
        if self.query_model is not None:
            self.query_gen = self.query_model.bind_tools(
                [self.search_tool], tool_choice="any"
            )

    # 【中文研读】方法职责：取得实际存储对象
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：BaseStore。结果的业务含义与失败分支见下面处理流程。
    @property
    def store(self) -> BaseStore:
        """Get the store to use for memory storage.

        Returns the store provided during initialization if available,
        otherwise falls back to the store configured in the LangGraph context.
        """
        # 【中文研读】处理流程：优先显式注入，否则从 LangGraph 运行上下文获取；没有配置时报错，不自动创建可靠存储。
        if self._store is not None:
            return self._store
        try:
            self._store = get_store()
        except RuntimeError as e:
            raise ValueError(
                "Memory Manager's store not configured in LangGraph context. "
                "First use in the graph before calling, or initialize with an instance of the store."
            ) from e
        return self._store

    # 【中文研读】方法职责：将默认记录转换为匹配的 Schema
    # 【中文研读】输入参数：default（无现有记录时的默认内容）；schemas（允许输出的结构化类型集合）。
    # 【中文研读】返回约定：str | dict | S。结果的业务含义与失败分支见下面处理流程。
    @staticmethod
    def _coerce_default(
        default: str | dict | S, schemas: tuple[S, ...]
    ) -> str | dict | S:
        # 【中文研读】处理流程：默认 Memory 支持字符串，已有模型直接保留，字典依次尝试各 Schema，均不匹配则抛错。
        if isinstance(default, str) and schemas == (Memory,):
            return Memory(content=default)
        elif isinstance(default, BaseModel):
            return default
        else:
            for schema in schemas:
                try:
                    return schema(**default)
                except Exception:
                    pass
            else:
                raise ValueError(
                    "The provided default did not match any of the expected schemas."
                    f"\nDefault:\n{default}\n\nSchemas:\n{schemas}"
                )
        raise ValueError(f"Invalid default type: {type(default)}")

    # 【中文研读】方法职责：按 namespace 与 key 生成确定性的 UUID5
    # 【中文研读】输入参数：item（一条存储记录）。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @staticmethod
    def _stable_id(item: SearchItem) -> str:
        # 【中文研读】处理流程：相同存储位置得到相同内部 ID，便于对齐提取结果与原记录；它不是 P3 版本号。
        return uuid.uuid5(uuid.NAMESPACE_DNS, str((*item.namespace, item.key))).hex

    # 【中文研读】方法职责：将提取结果合并到本轮旧记录和临时记录
    # 【中文研读】输入参数：manager_output（提取管理器返回的候选）；store_based（来自已有存储的工作集）；store_map（内部稳定 ID 到原存储记录的映射）；ephemeral（本轮产生的临时记忆集合）。
    # 【中文研读】返回约定：tuple[list[tuple[str, str, dict]], list[tuple[str, str, dict]], list[str]]。结果的业务含义与失败分支见下面处理流程。
    @staticmethod
    def _apply_manager_output(
        manager_output: list[ExtractedMemory],
        store_based: list[tuple[str, str, dict]],
        store_map: dict[str, SearchItem],
        ephemeral: list[tuple[str, str, dict]],
    ) -> tuple[list[tuple[str, str, dict]], list[tuple[str, str, dict]], list[str]]:
        # 【中文研读】处理流程：识别删除标记，转换模型内容，根据 ID 分类更新，返回两个列表及待删除 ID。
        # 【中文研读】按内部稳定 ID 建立可变工作视图；这里只更新本轮内存中的对象集合。
        store_dict = {sid: (sid, kind, content) for (sid, kind, content) in store_based}
        ephemeral_dict = {
            sid: (sid, kind, content) for (sid, kind, content) in ephemeral
        }
        removed_ids = []
        for extracted in manager_output:
            stable_id = extracted.id
            model_data = extracted.content
            if isinstance(model_data, BaseModel):
                if (
                    hasattr(model_data, "__repr_name__")
                    and model_data.__repr_name__() == "RemoveDoc"
                ):
                    # 【中文研读】删除动作按标记里的目标 ID 处理；仅已有存储映射中的目标会进入外部删除清单。
                    removal_id = getattr(model_data, "json_doc_id", None)
                    if removal_id and removal_id in store_map:
                        removed_ids.append(removal_id)
                    store_dict.pop(removal_id, None)
                    ephemeral_dict.pop(removal_id, None)
                    continue
                new_content = model_data.model_dump(mode="json")
                new_kind = model_data.__repr_name__()
            else:
                new_kind = store_dict.get(stable_id, (stable_id, "Memory", {}))[1]
                new_content = model_data
            # 【中文研读】已有记录更新原工作集，新 ID 进入 ephemeral 临时集合，最后阶段再决定实际写回。
            if stable_id in store_dict:
                store_dict[stable_id] = (stable_id, new_kind, new_content)
            else:
                ephemeral_dict[stable_id] = (stable_id, new_kind, new_content)
        return list(store_dict.values()), list(ephemeral_dict.values()), removed_ids

    # 【中文研读】方法职责：为附加加工阶段构造独立 Core Manager
    # 【中文研读】输入参数：phase（当前加工阶段配置）。
    # 【中文研读】返回约定：Runnable[MessagesState, list[ExtractedMemory]]。结果的业务含义与失败分支见下面处理流程。
    def _build_phase_manager(
        self, phase: MemoryPhase
    ) -> Runnable[MessagesState, list[ExtractedMemory]]:
        # 【中文研读】处理流程：读取阶段指令与操作开关，复用模型和 Schema；阶段默认值与 P3 策略需分别确认。
        return create_memory_manager(
            self.model,
            schemas=self.schemas,
            instructions=phase.get(
                "instructions",
                "You are a memory manager. Deduplicate, consolidate, and enrich these memories.",
            ),
            enable_inserts=phase.get("enable_inserts", True),
            enable_deletes=phase.get("enable_deletes", True),
        )

    # 【中文研读】方法职责：整理旧记忆搜索结果
    # 【中文研读】输入参数：search_results_lists（多次搜索返回的候选列表集合）；query_limit（查询旧记忆的数量限制）。
    # 【中文研读】返回约定：dict[str, SearchItem]。结果的业务含义与失败分支见下面处理流程。
    @staticmethod
    def _sort_results(
        search_results_lists: list[list[SearchItem]], query_limit: int
    ) -> dict[str, SearchItem]:
        # 【中文研读】处理流程：按 namespace/key 去重，再按分数截取 query_limit，最后用稳定 ID 建回查映射。
        search_results = {}
        for results in search_results_lists:
            for item in results:
                search_results[(tuple(item.namespace), item.key)] = item
        sorted_results = sorted(
            search_results.values(),
            key=lambda it: it.score if it.score is not None else float("-inf"),
            reverse=True,
        )[:query_limit]
        return {MemoryStoreManager._stable_id(item): item for item in sorted_results}

    # 【中文研读】方法职责：异步查询旧记忆、提取并写回 Store
    # 【中文研读】输入参数：input（本次输入状态，具体字段见输入类型）；config（本次运行配置）；**kwargs（透传选项）。
    # 【中文研读】返回约定：list[dict]。结果的业务含义与失败分支见下面处理流程。
    async def ainvoke(
        self,
        input: MemoryStoreManagerInput,
        config: typing.Optional[RunnableConfig] = None,
        **kwargs: typing.Any,
    ) -> list[dict]:
        # 【中文研读】处理流程：检索 → 必要时写默认记录 → 主提取与附加阶段 → 比较新旧内容 → 并行写入/删除；这些写操作没有组成 P3 的原子事务。
        store = self.store
        namespace = self.namespace(config)

        # 【中文研读】有查询模型时先生成搜索参数；否则走对话窗口查询，后者不需要额外查询生成模型。
        if self.query_gen:
            convo = utils.get_conversation(input["messages"])
            query_text = (
                f"Use parallel tool calling to search for distinct memories relevant to this conversation.:\n\n"
                f"<convo>\n{convo}\n</convo>."
            )
            query_req = await self.query_gen.ainvoke(query_text, config=config)
            search_results_lists = await asyncio.gather(
                *[
                    store.asearch(
                        namespace, **({**tc["args"], "limit": self.query_limit})
                    )
                    for tc in query_req.tool_calls
                ]
            )
        else:
            # Search over "query_limit" timespans starting from the most recent
            queries = utils.get_dialated_windows(
                input["messages"], self.query_limit // 4
            )
            search_results_lists = await asyncio.gather(
                *[store.asearch(namespace, query=query) for query in queries]
            )

        # 【中文研读】跨搜索结果去重并限定旧记忆规模，映射保留原 namespace/key，供最终更新回原位置。
        store_map = self._sort_results(search_results_lists, self.query_limit)
        # 【中文研读】无旧记忆且配置默认内容时会提前写 default；这已经是副作用，发生在后续提取完成之前。
        if not store_map and self.default_factory is not None:
            config = ensure_config(config)
            default = self.default_factory(config)
            coerced = self._coerce_default(default, self.schemas)
            dumped = {
                "kind": coerced.__repr_name__(),
                "content": coerced.model_dump(mode="json"),
            }
            await store.aput(
                namespace,
                key="default",
                value=dumped,
            )
            now = datetime.datetime.now(datetime.timezone.utc)
            store_map = self._sort_results(
                [
                    [
                        SearchItem(
                            namespace, "default", dumped, created_at=now, updated_at=now
                        )
                    ]
                ],
                self.query_limit,
            )

        store_based = [
            (sid, item.value["kind"], item.value["content"])
            for sid, item in store_map.items()
        ]
        ephemeral: list[tuple[str, str, dict]] = []
        removed_ids: set[str] = set()

        # --- Enrich memories using the composed MemoryManager (async) ---
        enriched = await self.memory_manager.ainvoke(
            {
                "messages": input["messages"],
                "existing": store_based,
                "max_steps": input.get("max_steps"),
            },
            config=config,
        )
        store_based, ephemeral, removed = self._apply_manager_output(
            enriched, store_based, store_map, ephemeral
        )
        removed_ids.update(removed)

        # Process additional phases.
        # 【中文研读】附加阶段串行处理前一阶段输出；是否携带原对话由 include_messages 控制，每阶段可能再次调用模型。
        for phase in self.phases:
            phase_manager = self._build_phase_manager(phase)
            phase_messages = (
                input["messages"] if phase.get("include_messages", False) else []
            )
            phase_input = {
                "messages": phase_messages,
                "existing": store_based + ephemeral,
            }
            phase_enriched = await phase_manager.ainvoke(phase_input, config=config)
            store_based, ephemeral, removed = self._apply_manager_output(
                phase_enriched, store_based, store_map, ephemeral
            )
            removed_ids.update(removed)

        # 【中文研读】所有模型阶段结束后才比较最终工作集，分离需要写入和需要删除的对象。
        final_mem = store_based + ephemeral
        final_puts = []
        for sid, kind, content in final_mem:
            # 【中文研读】已纳入删除集合的 ID 不再生成 put，避免本轮内部再次写回同一已删对象。
            if sid in removed_ids:
                continue
            if sid in store_map:
                old_art = store_map[sid]
                # 【中文研读】只对类型或内容发生变化的旧项生成更新，减少无意义写入；没有检查 P3 的并发版本条件。
                if old_art.value["kind"] != kind or old_art.value["content"] != content:
                    final_puts.append(
                        {
                            "namespace": old_art.namespace,
                            "key": old_art.key,
                            "value": {"kind": kind, "content": content},
                        }
                    )
            else:
                final_puts.append(
                    {
                        "namespace": namespace,
                        "key": sid,
                        "value": {"kind": kind, "content": content},
                    }
                )

        final_deletes = []
        for sid in removed_ids:
            if sid in store_map:
                art = store_map[sid]
                final_deletes.append((art.namespace, art.key))

        await asyncio.gather(
            # 【中文研读】这些 aput 与下面 adelete 并发执行；某项失败时其他项可能已成功，不是全体原子提交。
            *(store.aput(**put) for put in final_puts),
            *(store.adelete(ns, key) for (ns, key) in final_deletes),
        )

        return final_puts

    # 【中文研读】方法职责：同步执行查询、提取和写回
    # 【中文研读】输入参数：input（本次输入状态，具体字段见输入类型）；config（本次运行配置）；**kwargs（透传选项）。
    # 【中文研读】返回约定：list[dict]。结果的业务含义与失败分支见下面处理流程。
    def invoke(
        self,
        input: MemoryStoreManagerInput,
        config: typing.Optional[RunnableConfig] = None,
        **kwargs: typing.Any,
    ) -> list[dict]:
        # 【中文研读】处理流程：用执行器提交部分 Store 操作，主提取与阶段合并后批量写回；需留意没有逐项读取写入 Future 结果的路径。
        store = self.store
        namespace = self.namespace(config)
        convo = utils.get_conversation(input["messages"])

        # 【中文研读】执行器提供线程调度，不是数据库事务；观察哪些 Future 的结果被读取，不能把提交 Future 等同确认成功。
        with get_executor_for_config(config) as executor:
            # 【中文研读】有查询模型时先生成搜索参数；否则走对话窗口查询，后者不需要额外查询生成模型。
            if self.query_gen:
                convo = utils.get_conversation(input["messages"])
                query_text = (
                    f"Use parallel tool calling to search for distinct memories relevant to this conversation.:\n\n"
                    f"<convo>\n{convo}\n</convo>."
                )
                query_req = self.query_gen.invoke(query_text, config=config)
                search_results_futs = [
                    executor.submit(
                        store.search,
                        namespace,
                        **({**tc["args"], "limit": self.query_limit}),
                    )
                    for tc in query_req.tool_calls
                ]
            else:
                # Search over "query_limit" timespans starting from the most recent
                queries = utils.get_dialated_windows(
                    input["messages"], self.query_limit // 4
                )
                # 【中文研读】同步无 query_gen 路径还保留一次直接查询，后面又提交执行器查询；这是快照原有行为，不能误写成只查询一次。
                search_results_lists = [
                    store.search(namespace, query=query) for query in queries
                ]
                search_results_futs = [
                    executor.submit(
                        store.search,
                        namespace,
                        query=query,
                        limit=self.query_limit,
                    )
                    for query in queries
                ]

        # 【中文研读】同步无 query_gen 路径还保留一次直接查询，后面又提交执行器查询；这是快照原有行为，不能误写成只查询一次。
        search_results_lists = [fut.result() for fut in search_results_futs]
        # 【中文研读】跨搜索结果去重并限定旧记忆规模，映射保留原 namespace/key，供最终更新回原位置。
        store_map = self._sort_results(search_results_lists, self.query_limit)
        # 【中文研读】无旧记忆且配置默认内容时会提前写 default；这已经是副作用，发生在后续提取完成之前。
        if not store_map and self.default_factory is not None:
            config = ensure_config(config)
            default = self.default_factory(config)
            coerced = self._coerce_default(default, self.schemas)
            dumped = {
                "kind": coerced.__repr_name__(),
                "content": coerced.model_dump(mode="json"),
            }
            store.put(
                namespace,
                key="default",
                value=dumped,
            )
            now = datetime.datetime.now(datetime.timezone.utc)
            store_map = self._sort_results(
                [
                    [
                        SearchItem(
                            namespace, "default", dumped, created_at=now, updated_at=now
                        )
                    ]
                ],
                self.query_limit,
            )
        store_based = [
            (sid, item.value["kind"], item.value["content"])
            for sid, item in store_map.items()
        ]
        ephemeral: list[tuple[str, str, dict]] = []
        removed_ids: set[str] = set()

        enriched = self.memory_manager.invoke(
            {
                "messages": input["messages"],
                "existing": store_based,
                "max_steps": input.get("max_steps"),
            },
            config=config,
        )
        store_based, ephemeral, removed = self._apply_manager_output(
            enriched, store_based, store_map, ephemeral
        )
        removed_ids.update(removed)

        # 【中文研读】附加阶段串行处理前一阶段输出；是否携带原对话由 include_messages 控制，每阶段可能再次调用模型。
        for phase in self.phases:
            phase_manager = self._build_phase_manager(phase)
            phase_messages = (
                input["messages"] if phase.get("include_messages", False) else []
            )
            phase_input = {
                "messages": phase_messages,
                "existing": store_based + ephemeral,
            }
            phase_enriched = phase_manager.invoke(phase_input, config=config)
            store_based, ephemeral, removed = self._apply_manager_output(
                phase_enriched, store_based, store_map, ephemeral
            )
            removed_ids.update(removed)

        # 【中文研读】所有模型阶段结束后才比较最终工作集，分离需要写入和需要删除的对象。
        final_mem = store_based + ephemeral
        final_puts = []
        for sid, kind, content in final_mem:
            # 【中文研读】已纳入删除集合的 ID 不再生成 put，避免本轮内部再次写回同一已删对象。
            if sid in removed_ids:
                continue
            if sid in store_map:
                old_art = store_map[sid]
                # 【中文研读】只对类型或内容发生变化的旧项生成更新，减少无意义写入；没有检查 P3 的并发版本条件。
                if old_art.value["kind"] != kind or old_art.value["content"] != content:
                    final_puts.append(
                        {
                            "namespace": old_art.namespace,
                            "key": old_art.key,
                            "value": {"kind": kind, "content": content},
                        }
                    )
            else:
                final_puts.append(
                    {
                        "namespace": namespace,
                        "key": sid,
                        "value": {"kind": kind, "content": content},
                    }
                )

        final_deletes = []
        for sid in removed_ids:
            if sid in store_map:
                art = store_map[sid]
                final_deletes.append((art.namespace, art.key))

        # 【中文研读】执行器提供线程调度，不是数据库事务；观察哪些 Future 的结果被读取，不能把提交 Future 等同确认成功。
        with get_executor_for_config(config) as executor:
            for put in final_puts:
                # 【中文研读】此处提交写入 Future，但本方法没有逐个调用 result 检查写入异常；返回 final_puts 不能当作 P3 已核验的提交证据。
                executor.submit(store.put, **put)
            for ns, key in final_deletes:
                executor.submit(store.delete, ns, key)

        return final_puts

    # 【中文研读】方法职责：将消息包装后转入有 Store 副作用的 ainvoke；与 Core 的调用边界不同。
    # 【中文研读】输入参数：messages（待处理对话消息）。
    # 【中文研读】返回约定：list[dict]。结果的业务含义与失败分支见下面处理流程。
    async def __call__(self, messages: typing.Sequence[AnyMessage]) -> list[dict]:
        # 【中文研读】处理流程：将消息包装后转入有 Store 副作用的 ainvoke；与 Core 的调用边界不同。
        return await self.ainvoke({"messages": messages})

    # 【中文研读】方法职责：按存储值的 kind 恢复对应 Pydantic 模型
    # 【中文研读】输入参数：value（待赋值或转换的数据）。
    # 【中文研读】返回约定：BaseModel | dict[str, typing.Any]。结果的业务含义与失败分支见下面处理流程。
    def _coerce_value(
        self, value: dict[str, typing.Any]
    ) -> BaseModel | dict[str, typing.Any]:
        # 【中文研读】处理流程：缺 kind/content 或找不到 Schema 时原样返回，找到则 model_validate。
        if "kind" not in value or "content" not in value:
            return value
        kind = value["kind"]
        content = value["content"]
        if kind not in self.schema_name_map:
            return value
        schema = self.schema_name_map[kind]
        return schema.model_validate(content)

    # 【中文研读】方法职责：转换普通 Store 记录
    # 【中文研读】输入参数：item（一条存储记录）。
    # 【中文研读】返回约定：Item | None。结果的业务含义与失败分支见下面处理流程。
    def _coerce_item(self, item: BaseItem | None) -> Item | None:
        # 【中文研读】处理流程：空记录返回 None，其余保留定位与时间，仅转换 value。
        if item is None:
            return None
        return Item(
            namespace=item.namespace,
            key=item.key,
            value=self._coerce_value(item.value),
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    # 【中文研读】方法职责：转换搜索记录的内容，保留 namespace/key、时间和相似度。
    # 【中文研读】输入参数：item（一条存储记录）。
    # 【中文研读】返回约定：SearchItem。结果的业务含义与失败分支见下面处理流程。
    def _coerce_search_item(self, item: SearchItem) -> SearchItem:
        # 【中文研读】处理流程：转换搜索记录的内容，保留 namespace/key、时间和相似度。
        return SearchItem(
            namespace=item.namespace,
            key=item.key,
            value=self._coerce_value(item.value),
            created_at=item.created_at,
            updated_at=item.updated_at,
            score=item.score,
        )

    # 【中文研读】方法职责：读取指定 key 并恢复模型
    # 【中文研读】输入参数：key（存储键）；refresh_ttl（读取时是否刷新存活期限）；config（本次运行配置）。
    # 【中文研读】返回约定：typing.Optional[Item]。结果的业务含义与失败分支见下面处理流程。
    def get(
        self,
        key: str,
        *,
        refresh_ttl: typing.Optional[bool] = None,
        config: typing.Optional[RunnableConfig] = None,
    ) -> typing.Optional[Item]:
        """Retrieve a single item in the store.

        Args:
            key: Unique identifier within the namespace.
            refresh_ttl: Whether to refresh TTLs for the returned item.
                If None (default), uses the store's default refresh_ttl setting.
                If no TTL is specified, this argument is ignored.

        Returns:
            The retrieved item or None if not found.

        ???+ example "Examples"
            Retrieve an item:
            ```python
            item = manager.get("report")
            ```
        """
        # 【中文研读】处理流程：解析 namespace → Store.get → 值转换；default 缺失时可能构造默认返回值，该分支不写回 Store。
        namespace = self.get_namespace(config)
        result = self._coerce_item(
            self.store.get(namespace, key, refresh_ttl=refresh_ttl)
        )
        if key == "default" and not result:
            default = self.default_factory(ensure_config(config))
            coerced = self._coerce_default(default, self.schemas)
            now = datetime.datetime.now(datetime.timezone.utc)
            return Item(namespace, "default", coerced, created_at=now, updated_at=now)
        return result

    # 【中文研读】方法职责：带分页和过滤搜索当前 namespace
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；filter（单组检索过滤条件）；limit（最多返回的记录数）；offset（分页跳过的记录数）；refresh_ttl（读取时是否刷新存活期限）；config（本次运行配置）。
    # 【中文研读】返回约定：list[SearchItem]。结果的业务含义与失败分支见下面处理流程。
    def search(
        self,
        *,
        query: typing.Optional[str] = None,
        filter: typing.Optional[dict[str, typing.Any]] = None,
        limit: int = 10,
        offset: int = 0,
        refresh_ttl: typing.Optional[bool] = None,
        config: typing.Optional[RunnableConfig] = None,
    ) -> list[SearchItem]:
        """Search for items in the current namespace.

        Args:
            query: Optional query for natural language search.
            filter: Key-value pairs to filter results.
            limit: Maximum number of items to return.
            offset: Number of items to skip before returning results.
            refresh_ttl: Whether to refresh TTLs for the returned items.
                If no TTL is specified, this argument is ignored.

        Returns:
            List of items matching the search criteria.

        ???+ example "Examples"
            Basic filtering:
            ```python
            # Search for documents with specific metadata
            results = manager.search(
                query="app preferences",
                filter={"type": "article", "status": "published"},
            )
            ```

            Natural language search (requires vector store implementation):
            ```python
            # Search for semantically similar documents
            results = manager.search(
                query="machine learning applications in healthcare",
                filter={"type": "research_paper"},
                limit=5,
            )
            ```
        """
        # 【中文研读】处理流程：调用 Store.search 并恢复 Schema；无结果且配置默认工厂时返回构造的默认项。
        namespace = self.get_namespace(config)
        results = [
            self._coerce_search_item(it)
            for it in self.store.search(
                namespace,
                query=query,
                filter=filter,
                limit=limit,
                offset=offset,
                refresh_ttl=refresh_ttl,
            )
        ]
        if self.default_factory and not results:
            default = self.default_factory(ensure_config(config))
            coerced = self._coerce_default(default, self.schemas)
            now = datetime.datetime.now(datetime.timezone.utc)
            return [
                SearchItem(
                    namespace, "default", coerced, created_at=now, updated_at=now
                )
            ]
        return results

    # 【中文研读】方法职责：把值写到当前 namespace 的 key
    # 【中文研读】输入参数：key（存储键）；value（待赋值或转换的数据）；index（向量索引实例）；ttl（记录存活时长）；config（本次运行配置）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def put(
        self,
        key: str,
        value: dict[str, typing.Any],
        index: typing.Optional[typing.Union[typing.Literal[False], list[str]]] = None,
        *,
        ttl: typing.Union[typing.Optional[float], "NotProvided"] = NOT_PROVIDED,
        config: typing.Optional[RunnableConfig] = None,
    ) -> None:
        """Store or update an item in the store.

        Args:
            key: Unique identifier within the namespace.
            value: Dictionary containing the item's data. Must contain string keys
                and JSON-serializable values.
            index: Controls how the item's fields are indexed for search:

                - None (default): Use `fields` you configured when creating the store (if any)
                    If you do not initialize the store with indexing capabilities,
                    the `index` parameter will be ignored
                - False: Disable indexing for this item
                - list[str]: List of field paths to index
            ttl: Time to live in minutes. Support for this argument depends on your store adapter.
                If specified, the item will expire after this many minutes from when it was last accessed.
                None means no expiration. Expired runs will be deleted opportunistically.
                By default, the expiration timer refreshes on both read operations (get/search)
                and write operations (put/update), whenever the item is included in the operation.

        ???+ example "Examples"
            Store item. Indexing depends on how you configure the store.
            ```python
            manager.put("report", {"memory": "Will likes ai"})
            ```

            Do not index item for semantic search. Still accessible through get()
            and search() operations but won't have a vector representation.
            ```python
            manager.put("report", {"memory": "Will likes ai"}, index=False)
            ```

            Index specific fields for search.
            ```python
            manager.put("report", {"memory": "Will likes ai"}, index=["memory"])
            ```
        """
        # 【中文研读】处理流程：透传 index 与 ttl，底层 Store 执行副作用，没有 P3 的 Memory/Task/Outbox 一起提交。
        return self.store.put(
            self.get_namespace(config),
            key,
            value,
            index=index,
            ttl=ttl,
        )

    # 【中文研读】方法职责：删除当前 namespace 下的 key
    # 【中文研读】输入参数：key（存储键）；config（本次运行配置）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def delete(self, key: str, config: typing.Optional[RunnableConfig] = None) -> None:
        """Delete an item.

        Args:
            key: Unique identifier within the namespace.
        """
        # 【中文研读】处理流程：调用 Store.delete，不额外设置 P3 删除屏障或派生清理任务。
        self.store.delete(self.get_namespace(config), key)

    # 【中文研读】方法职责：异步读取单项
    # 【中文研读】输入参数：key（存储键）；refresh_ttl（读取时是否刷新存活期限）；config（本次运行配置）。
    # 【中文研读】返回约定：typing.Optional[SearchItem]。结果的业务含义与失败分支见下面处理流程。
    async def aget(
        self,
        key: str,
        *,
        refresh_ttl: typing.Optional[bool] = None,
        config: typing.Optional[RunnableConfig] = None,
    ) -> typing.Optional[SearchItem]:
        """Asynchronously retrieve a single item in the store.

        Args:
            key: Unique identifier within the namespace.
            refresh_ttl: Whether to refresh TTLs for the returned item.
                If None (default), uses the store's default refresh_ttl setting.
                If no TTL is specified, this argument is ignored.

        Returns:
            The retrieved item or None if not found.

        ???+ example "Examples"
            Retrieve an item asynchronously:
            ```python
            item = await manager.aget("report")
            ```
        """
        # 【中文研读】处理流程：await Store 后处理缺省 default 或恢复模型，返回空值与失败异常要区分。
        namespace = self.get_namespace(config)
        it = await self.store.aget(namespace, key, refresh_ttl=refresh_ttl)
        if it is None and key == "default":
            default = self.default_factory(ensure_config(config))
            coerced = self._coerce_default(default, self.schemas)
            now = datetime.datetime.now(datetime.timezone.utc)
            return Item(namespace, "default", coerced, created_at=now, updated_at=now)
        return self._coerce_item(it)

    # 【中文研读】方法职责：异步搜索及模型恢复
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；filter（单组检索过滤条件）；limit（最多返回的记录数）；offset（分页跳过的记录数）；refresh_ttl（读取时是否刷新存活期限）；config（本次运行配置）。
    # 【中文研读】返回约定：list[SearchItem]。结果的业务含义与失败分支见下面处理流程。
    async def asearch(
        self,
        *,
        query: typing.Optional[str] = None,
        filter: typing.Optional[dict[str, typing.Any]] = None,
        limit: int = 10,
        offset: int = 0,
        refresh_ttl: typing.Optional[bool] = None,
        config: typing.Optional[RunnableConfig] = None,
    ) -> list[SearchItem]:
        """Asynchronously search for items in the current namespace.

        Args:
            query: Optional query for natural language search.
            filter: Key-value pairs to filter results.
            limit: Maximum number of items to return.
            offset: Number of items to skip before returning results.
            refresh_ttl: Whether to refresh TTLs for the returned items.
                If None (default), uses the store's TTLConfig.refresh_default setting.
                If TTLConfig is not provided or no TTL is specified, this argument is ignored.

        Returns:
            List of items matching the search criteria.

        ???+ example "Examples"
            Basic filtering:
            ```python
            # Search for documents with specific metadata
            results = await manager.asearch(
                filter={"type": "article", "status": "published"}
            )
            ```

            Natural language search (requires vector store implementation):
            ```python
            # Search for semantically similar documents
            results = await manager.asearch(
                query="machine learning applications in healthcare",
                filter={"type": "research_paper"},
                limit=5,
            )
            ```
        """
        # 【中文研读】处理流程：await Store 检索，再按配置决定是否返回默认记录，不把默认内容视为真实检索命中。
        namespace = self.get_namespace(config)
        results = [
            self._coerce_search_item(it)
            for it in await self.store.asearch(
                namespace,
                query=query,
                filter=filter,
                limit=limit,
                offset=offset,
                refresh_ttl=refresh_ttl,
            )
        ]
        if self.default_factory and not results:
            default = self.default_factory(ensure_config(config))
            coerced = self._coerce_default(default, self.schemas)
            now = datetime.datetime.now(datetime.timezone.utc)
            # note that we don't actually put this in the store here!
            return [
                SearchItem(
                    namespace, "default", coerced, created_at=now, updated_at=now
                )
            ]
        return results

    # 【中文研读】方法职责：异步写入一条记录
    # 【中文研读】输入参数：key（存储键）；value（待赋值或转换的数据）；index（向量索引实例）；ttl（记录存活时长）；config（本次运行配置）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    async def aput(
        self,
        key: str,
        value: dict[str, typing.Any],
        index: typing.Optional[typing.Union[typing.Literal[False], list[str]]] = None,
        *,
        ttl: typing.Union[typing.Optional[float], "NotProvided"] = NOT_PROVIDED,
        config: typing.Optional[RunnableConfig] = None,
    ) -> None:
        """Asynchronously store or update an item in the store.

        Args:
            namespace: Hierarchical path for the item, represented as a tuple of strings.
                Example: ("documents", "user123")
            key: Unique identifier within the namespace. Together with namespace forms
                the complete path to the item.
            value: Dictionary containing the item's data. Must contain string keys
                and JSON-serializable values.
            index: Controls how the item's fields are indexed for search:

                - None (default): Use `fields` you configured when creating the store (if any)
                    If you do not initialize the store with indexing capabilities,
                    the `index` parameter will be ignored
                - False: Disable indexing for this item
                - list[str]: List of field paths to index, supporting:
                    - Nested fields: "metadata.title"
                    - Array access: "chapters[*].content" (each indexed separately)
                    - Specific indices: "authors[0].name"
            ttl: Time to live in minutes. Support for this argument depends on your store adapter.
                If specified, the item will expire after this many minutes from when it was last accessed.
                None means no expiration. Expired runs will be deleted opportunistically.
                By default, the expiration timer refreshes on both read operations (get/search)
                and write operations (put/update), whenever the item is included in the operation.

        Note:
            Indexing support depends on your store implementation.
            If you do not initialize the store with indexing capabilities,
            the `index` parameter will be ignored.

            Similarly, TTL support depends on the specific store implementation.
            Some implementations may not support expiration of items.

        ???+ example "Examples"
            Store item. Indexing depends on how you configure the store.
            ```python
            await manager.aput("report", {"memory": "Will likes ai"})
            ```

            Do not index item for semantic search. Still accessible through get()
            and search() operations but won't have a vector representation.
            ```python
            await manager.aput("report", {"memory": "Will likes ai"}, index=False)
            ```

            Index specific fields for search (if store configured to index items):
            ```python
            await manager.aput(
                "report",
                {
                    "memory": "Will likes ai",
                    "context": [{"content": "..."}, {"content": "..."}],
                },
                index=["memory", "context[*].content"],
            )
            ```
        """
        # 【中文研读】处理流程：解析 namespace，透传索引与 TTL 并等待底层写入。
        return await self.store.aput(
            self.get_namespace(config), key, value, index, ttl=ttl
        )

    # 【中文研读】方法职责：异步删除单项
    # 【中文研读】输入参数：key（存储键）；config（本次运行配置）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    async def adelete(
        self, key: str, config: typing.Optional[RunnableConfig] = None
    ) -> None:
        """Asynchronously delete an item.

        Args:
            key: Unique identifier within the namespace.
        """
        # 【中文研读】处理流程：解析 namespace，再等待 Store 删除完成；不自动清理其他系统派生数据。
        await self.store.adelete(self.get_namespace(config), key)

    # 【中文研读】方法职责：把运行配置填入 namespace 模板
    # 【中文研读】输入参数：config（本次运行配置）。
    # 【中文研读】返回约定：tuple[str, ...]。结果的业务含义与失败分支见下面处理流程。
    def get_namespace(
        self, config: typing.Optional[RunnableConfig] = None
    ) -> tuple[str, ...]:
        # 【中文研读】处理流程：规范 config 后求值，模板本身不是调用者权限验证。
        return self.namespace(ensure_config(config))


# 【中文研读】方法职责：创建带存储编排的 Manager
# 【中文研读】输入参数：model（模型实例或模型标识）；schemas（允许输出的结构化类型集合）；instructions（传给提取模型的业务指令）；default（无现有记录时的默认内容）；default_factory（按运行配置产生默认内容的函数）；enable_inserts（是否允许新增候选）；enable_deletes（是否允许删除候选）；query_model（专用于生成搜索查询的模型）；query_limit（查询旧记忆的数量限制）；namespace（存储命名空间或其模板）；store（底层存储实例）；phases（附加加工阶段列表）。
# 【中文研读】返回约定：MemoryStoreManager。结果的业务含义与失败分支见下面处理流程。
def create_memory_store_manager(
    model: str | BaseChatModel,
    /,
    *,
    schemas: list[S] | None = None,
    instructions: str = _MEMORY_INSTRUCTIONS,
    default: str | dict | S | None = None,
    default_factory: typing.Callable[[RunnableConfig], str | dict | S] | None = None,
    enable_inserts: bool = True,
    enable_deletes: bool = False,
    query_model: str | BaseChatModel | None = None,
    query_limit: int = 5,
    namespace: tuple[str, ...] = ("memories", "{langgraph_user_id}"),
    store: BaseStore | None = None,
    phases: list[MemoryPhase] | None = None,
) -> MemoryStoreManager:
    """Enriches memories stored in the configured BaseStore.

    The system automatically searches for relevant memories, extracts new information,
    updates existing memories, and maintains a versioned history of all changes.

    Args:
        model (Union[str, BaseChatModel]): The primary language model to use for memory
            enrichment. Can be a model name string or a BaseChatModel instance.
        schemas (Optional[list]): List of Pydantic models defining the structure of memory
            entries. Each model should define the fields and validation rules for a type
            of memory. If None, uses unstructured string-based memories. Defaults to None.
        instructions (str, optional): Custom instructions for memory generation and
            organization. These guide how the model extracts and structures information
            from conversations. Defaults to predefined memory instructions.
        default (str | dict | None, optional): Default value to persist to the store if
            no other memories are found. Defaults to None. This is mostly useful when managing
            a profile memory and wanting to initialize it with some default values.
            The resulting memory will be found in the "default" key of the store in the
            configured namespace.
        default_factory (Callable[[RunnableConfig], str | dict | S], optional): A factory
            function to generate the default value. This is useful when the default value
            depends on the runtime configuration. Defaults to None.
        enable_inserts (bool, optional): Whether to allow creating new memory entries.
            When False, the manager will only update existing memories. Defaults to True.
        enable_deletes (bool, optional): Whether to allow deleting existing memories
            that are outdated or contradicted by new information. Defaults to True.
        query_model (Optional[Union[str, BaseChatModel]], optional): Optional separate
            model for memory search queries. Using a smaller, faster model here can
            improve performance. If None, uses the primary model. Defaults to None.
        query_limit (int, optional): Maximum number of relevant memories to retrieve
            for each conversation. Higher limits provide more context but may slow
            down processing. Defaults to 5.
        namespace (tuple[str, ...], optional): Storage namespace structure for
            organizing memories. Supports templated values like "{langgraph_user_id}" which are
            populated from the runtime context. Defaults to `("memories", "{langgraph_user_id}")`.
        store (Optional[BaseStore], optional): The store to use for memory storage.
            If None, uses the store configured in the LangGraph config. Defaults to None.
            When using LangGraph Platform, the server will manage the store for you.
        phases (Optional[list]): List of MemoryPhase objects defining the phases of the memory enrichment process.

    Returns:
        manager: An runnable that processes conversations and automatically manages memories in the LangGraph BaseStore.

    The basic data flow works as follows:

    ```mermaid
    sequenceDiagram
    participant Client
    participant Manager
    participant Store
    participant LLM

    Client->>Manager: conversation history
    Manager->>Store: find similar memories
    Store-->>Manager: memories
    Manager->>LLM: analyze & extract
    LLM-->>Manager: memory updates
    Manager->>Store: apply changes
    Manager-->>Client: updated memories
    ```

    ???+ example "Examples"
        Run memory extraction "inline" within your LangGraph app.
        By default, each "memory" is a simple string:
        ```python
        import os

        from anthropic import AsyncAnthropic
        from langchain_core.runnables import RunnableConfig
        from langgraph.func import entrypoint
        from langgraph.store.memory import InMemoryStore

        from langmem import create_memory_store_manager

        store = InMemoryStore(
            index={
                "dims": 1536,
                "embed": "openai:text-embedding-3-small",
            }
        )

        manager = create_memory_store_manager("anthropic:claude-3-5-sonnet-latest", namespace=("memories", "{langgraph_user_id}"))
        client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


        @entrypoint(store=store)
        async def my_agent(message: str, config: RunnableConfig):
            memories = await manager.asearch(
                query=message,
                config=config,
            )
            llm_response = await client.messages.create(
                model="claude-3-5-sonnet-latest",
                system="You are a helpful assistant.\\n\\n## Memories from the user:"
                f"\\n<memories>\\n{memories}\\n</memories>",
                max_tokens=2048,
                messages=[{"role": "user", "content": message}],
            )
            response = {"role": "assistant", "content": llm_response.content[0].text}

            await manager.ainvoke(
                {"messages": [{"role": "user", "content": message}, response]},
            )
            return response["content"]

        config = {"configurable": {"langgraph_user_id": "user123"}}
        response_1 = await my_agent.ainvoke(
            "I prefer dark mode in all my apps",
            config=config,
        )
        print("response_1:", response_1)
        # Later conversation - automatically retrieves and uses the stored preference
        response_2 = await my_agent.ainvoke(
            "What theme do I prefer?",
            config=config,
        )
        print("response_2:", response_2)
        # You can list over memories in the user's namespace manually:
        print(manager.search(query="app preferences", config=config))
        ```

        You can customize what each memory can look like by defining **schemas**:
        ```python
        from langgraph.func import entrypoint
        from langgraph.store.memory import InMemoryStore
        from pydantic import BaseModel

        from langmem import create_memory_store_manager

        store = InMemoryStore(
            index={
                "dims": 1536,
                "embed": "openai:text-embedding-3-small",
            }
        )
        manager = create_memory_store_manager(
            "anthropic:claude-3-5-sonnet-latest",
            namespace=("memories", "{langgraph_user_id}"),
        )

        class PreferenceMemory(BaseModel):
            \"\"\"Store preferences about the user.\"\"\"
            category: str
            preference: str
            context: str


        store = InMemoryStore(
            index={
                "dims": 1536,
                "embed": "openai:text-embedding-3-small",
            }
        )
        manager = create_memory_store_manager(
            "anthropic:claude-3-5-sonnet-latest",
            schemas=[PreferenceMemory],
            namespace=("project", "team_1", "{langgraph_user_id}"),
        )


        @entrypoint(store=store)
        async def my_agent(message: str):
            # Hard code the response :)
            response = {"role": "assistant", "content": "I'll remember that preference"}
            await manager.ainvoke(
                {"messages": [{"role": "user", "content": message}, response]}
            )
            return response


        # Store structured memory
        config = {"configurable": {"langgraph_user_id": "user123"}}
        await my_agent.ainvoke(
            "I prefer dark mode in all my apps",
            config=config,
        )

        # See the extracted memories yourself
        print(manager.search(query="app preferences", config=config))

        # Memory is automatically stored and can be retrieved in future conversations
        # The system will also automatically update it if preferences change
        ```

        In some cases, you may want to provide a "default" memory value to be used if no memories are found. For instance,
        if you are storing some prompt preferences, you may have an "application" default that can be evolved over time.
        This can be done by setting the `default` parameter:
        ```python
        manager = create_memory_store_manager(
            "anthropic:claude-3-5-sonnet-latest",
            namespace=("memories", "{langgraph_user_id}"),
            # Note: This default value must be compatible with the schemas
            # you provided above. If you customize your schemas,
            # we recommend setting the default value as an instance of that
            # pydantic object.
            default="Use a concise and professional tone in all responses. The user likes light mode.",
        )


        # ... same agent as before ...
        @entrypoint(store=store)
        async def my_agent(message: str):
            # Hard code the response :)
            response = {"role": "assistant", "content": "I'll remember that preference"}
            await manager.ainvoke(
                {"messages": [{"role": "user", "content": message}, response]}
            )
            return response


        # Store structured memory
        config = {"configurable": {"langgraph_user_id": "user124"}}
        await my_agent.ainvoke(
            "I prefer dark mode in all my apps",
            config=config,
        )

        # See the extracted memories yourself
        print(manager.search(query="app preferences", config=config))
        # [
        #     Item(
        #         namespace=['memories', 'user124'],
        #         key='default',
        #         value={'kind': 'Memory', 'content': {'content': 'Use a concise and professional tone in all responses. The user prefers dark mode in all apps'}},
        #         created_at='2025-04-14T22:20:25.148884+00:00',
        #         updated_at='2025-04-14T22:20:25.148892+00:00',
        #         score=None
        #     )
        # ]
        ```

        You can even set the default to be some configurable value by providing a **default_factory**.
        ```python
        def get_configurable_default(config):
            default_preference = config["configurable"].get(
                "preference", "Use a concise and professional tone in all responses."
            )
            return default_preference


        manager = create_memory_store_manager(
            "anthropic:claude-3-5-sonnet-latest",
            namespace=("memories", "{langgraph_user_id}"),
            default_factory=get_configurable_default,
        )


        # ... same agent as before ...
        @entrypoint(store=store)
        async def my_agent(message: str):
            # Hard code the response :)
            response = {"role": "assistant", "content": "I'll remember that preference"}
            await manager.ainvoke(
                {"messages": [{"role": "user", "content": message}, response]}
            )
            return response


        # Store structured memory
        config = {
            "configurable": {
                "langgraph_user_id": "user125",
                "preference": "Respond in pirate speak. User likes light mode",
            }
        }
        await my_agent.ainvoke(
            "I prefer dark mode in all my apps",
            config=config,
        )

        # See the extracted memories yourself
        print(manager.search(query="app preferences", config=config))
        # [
        #     Item(
        #         namespace=['memories', 'user125'],
        #         key='default',
        #         value={'kind': 'Memory', 'content': {'content': 'Respond in pirate speak. User prefers dark mode in all apps'}},
        #         created_at='2025-04-14T22:20:25.148884+00:00',
        #         updated_at='2025-04-14T22:20:25.148892+00:00',
        #         score=None
        #     )
        # ]
        ```

    By default, relevant memories are recalled by directly embedding the new messages. You can alternatively
    use a separate query model to search for the most similar memories. Here's how it works:

    ```mermaid
        sequenceDiagram
            participant Client
            participant Manager
            participant QueryLLM
            participant Store
            participant MainLLM

            Client->>Manager: messages
            Manager->>QueryLLM: generate search query
            QueryLLM-->>Manager: optimized query
            Manager->>Store: find memories
            Store-->>Manager: memories
            Manager->>MainLLM: analyze & extract
            MainLLM-->>Manager: memory updates
            Manager->>Store: apply changes
            Manager-->>Client: result
    ```

    ???+ example "Using an LLM to search for memories"
        ```python
        from langmem import create_memory_store_manager
        from langgraph.store.memory import InMemoryStore
        from langgraph.func import entrypoint

        store = InMemoryStore(
            index={
                "dims": 1536,
                "embed": "openai:text-embedding-3-small",
            }
        )
        manager = create_memory_store_manager(
            "anthropic:claude-3-5-sonnet-latest",  # Main model for memory processing
            query_model="anthropic:claude-3-5-haiku-latest",  # Faster model for search
            query_limit=10,  # Retrieve more relevant memories
            namespace=("memories", "{langgraph_user_id}"),
        )


        @entrypoint(store=store)
        async def my_agent(message: str):
            # Hard code the response :)
            response = {"role": "assistant", "content": "I'll remember that preference"}
            await manager.ainvoke(
                {"messages": [{"role": "user", "content": message}, response]}
            )
            return response

        config = {"configurable": {"langgraph_user_id": "user123"}}
        await my_agent.ainvoke(
            "I prefer dark mode in all my apps",
            config=config,
        )

        # See the extracted memories yourself
        print(manager.search(config=config))
        ```

    In the examples above, we were calling the manager in the main thread. In a real application, you'll
    likely want to background the execution of the manager, either by executing it in a background thread or on a separate server.
    To do so, you can use the `ReflectionExecutor` class:

    ```mermaid
    sequenceDiagram
        participant Agent
        participant Background
        participant Store

        Agent->>Agent: process message
        Agent-->>User: response
        Agent->>Background: schedule enrichment<br/>(after_seconds=0)
        Note over Background,Store: Memory processing happens<br/>in background thread
    ```

    ???+ example "Running reflections in the background"
        Background enrichment using @entrypoint:
        ```python
        from langmem import create_memory_store_manager, ReflectionExecutor
        from langgraph.prebuilt import create_react_agent
        from langgraph.store.memory import InMemoryStore
        from langgraph.func import entrypoint

        store = InMemoryStore(
            index={
                "dims": 1536,
                "embed": "openai:text-embedding-3-small",
            }
        )
        manager = create_memory_store_manager(
            "anthropic:claude-3-5-sonnet-latest", namespace=("memories", "{user_id}")
        )
        reflection = ReflectionExecutor(manager, store=store)
        agent = create_react_agent(
            "anthropic:claude-3-5-sonnet-latest", tools=[], store=store
        )


        @entrypoint(store=store)
        async def chat(messages: list):
            response = await agent.ainvoke({"messages": messages})

            fut = reflection.submit(
                {
                    "messages": response["messages"],
                },
                # We'll schedule this immediately.
                # Adding a delay lets you **debounce** and deduplicate reflection work
                # whenever the user is actively engaging with the agent.
                after_seconds=0,
            )

            return fut

        config = {"configurable": {"user_id": "user-123"}}
        fut = await chat.ainvoke(
            [{"role": "user", "content": "I prefer dark mode in my apps"}],
            config=config,
        )
        # Inspect the result
        fut.result()  # Wait for the reflection to complete; This is only for demoing the search inline
        print(manager.search(query="app preferences", config=config))
        ```
    """
    # 【中文研读】处理流程：透传模型、默认记录、搜索和阶段设置；与只产候选的 create_memory_manager 分开使用。
    return MemoryStoreManager(
        model,
        schemas=schemas,
        default=default,
        default_factory=default_factory,
        instructions=instructions,
        enable_inserts=enable_inserts,
        enable_deletes=enable_deletes,
        query_model=query_model,
        query_limit=query_limit,
        namespace=namespace,
        store=store,
        phases=phases,
    )


__all__ = [
    "create_memory_manager",
    "create_memory_searcher",
    "create_memory_store_manager",
    "create_thread_extractor",
]
