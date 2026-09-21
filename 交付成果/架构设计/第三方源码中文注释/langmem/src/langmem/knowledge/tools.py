# 【中文研读】阅读主线：工厂创建 StructuredTool → 模型提出工具参数 → 同步/异步函数校验操作 → Store 读写。此处有真实副作用，不能直接绕过 P3 的授权和事务。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
import functools
import logging
import typing
import uuid

from langchain_core.tools import StructuredTool
from langgraph.store.base import BaseStore
from langgraph.utils.config import get_store

from langmem import errors, utils

if typing.TYPE_CHECKING:
    from langchain_core.tools.base import ArgsSchema

try:
    from pydantic import ConfigDict
except ImportError:
    ConfigDict = None

logger = logging.getLogger(__name__)

# LangGraph Tools


# 【中文研读】方法职责：创建 Agent 可调用的记忆管理工具
# 【中文研读】输入参数：namespace（存储命名空间或其模板）；instructions（传给提取模型的业务指令）；schema（单个输出或数据结构定义）；actions_permitted（允许执行的操作集合）；store（底层存储实例）；name（工具或组件名称）。
# 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
def create_manage_memory_tool(
    namespace: tuple[str, ...] | str,
    *,
    instructions: str = "Proactively call this tool when you:\n\n"
    "1. Identify a new USER preference.\n"
    "2. Receive an explicit USER request to remember something or otherwise alter your behavior.\n"
    "3. Are working and want to record important context.\n"
    "4. Identify that an existing MEMORY is incorrect or outdated.\n",
    schema: typing.Type = str,
    actions_permitted: typing.Optional[
        tuple[typing.Literal["create", "update", "delete"], ...]
    ] = ("create", "update", "delete"),
    store: typing.Optional[BaseStore] = None,
    name: str = "manage_memory",
):
    """Create a tool for managing persistent memories in conversations.

    This function creates a tool that allows AI assistants to create, update, and delete
    persistent memories that carry over between conversations. The tool helps maintain
    context and user preferences across sessions.


    Args:
        instructions: Custom instructions for when to use the memory tool.
            Defaults to a predefined set of guidelines for proactive memory management.
        namespace: The namespace structure for organizing memories in LangGraph's BaseStore.
            Uses runtime configuration with placeholders like `{langgraph_user_id}`.
        store: The BaseStore to use for searching. If not provided, the tool will use the configured BaseStore in your graph or entrypoint.
            Only set if you intend on using these tools outside the LangGraph context.

    Returns:
        memory_tool (Tool): A decorated async function that can be used as a tool for memory management.
            The tool supports creating, updating, and deleting memories with proper validation.

    The resulting tool has a signature that looks like the following:
        ```python
        from typing import Literal


        def manage_memory(
            content: str | None = None,  # Content for new/updated memory
            id: str | None = None,  # ID of existing memory to update/delete
            action: Literal["create", "update", "delete"] = "create",
        ) -> str: ...
        ```
        _Note: the tool supports both sync and async usage._

    !!! note "Namespace Configuration"
        The namespace is configured at runtime through the `config` parameter:
        ```python
        # Example: Per-user memory storage
        config = {"configurable": {"langgraph_user_id": "user-123"}}
        # Results in namespace: ("memories", "user-123")

        # Example: Team-wide memory storage
        config = {"configurable": {"langgraph_user_id": "team-x"}}
        # Results in namespace: ("memories", "team-x")
        ```

    Tip:
        This tool connects with the LangGraph [BaseStore](https://langchain-ai.github.io/langgraph/reference/store/#langgraph.store.base.BaseStore) configured in your graph or entrypoint.
        It will not work if you do not provide a store.

    !!! example "Examples"
        ```python
        from langmem import create_manage_memory_tool
        from langgraph.func import entrypoint
        from langgraph.store.memory import InMemoryStore

        memory_tool = create_manage_memory_tool(
            # All memories saved to this tool will live within this namespace
            # The brackets will be populated at runtime by the configurable values
            namespace=("project_memories", "{langgraph_user_id}"),
        )

        store = InMemoryStore(
            index={
                "dims": 1536,
                "embed": "openai:text-embedding-3-small",
            }
        )


        @entrypoint(store=store)
        async def workflow(state: dict, *, previous=None):
            # Other work....
            result = await memory_tool.ainvoke(state)
            print(result)
            return entrypoint.final(value=result, save={})


        config = {
            "configurable": {
                # This value will be formatted into the namespace you configured above ("project_memories", "{langgraph_user_id}")
                "langgraph_user_id": "123e4567-e89b-12d3-a456-426614174000"
            }
        }
        # Create a new memory
        await workflow.ainvoke(
            {"content": "Team prefers to use Python for backend development"},
            config=config,
        )
        # Output: 'created memory 123e4567-e89b-12d3-a456-426614174000'

        # Update an existing memory
        result = await workflow.ainvoke(
            {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "content": "Team uses Python for backend and TypeScript for frontend",
                "action": "update",
            },
            config=config,
        )
        print(result)
        # Output: 'updated memory 123e4567-e89b-12d3-a456-426614174000'
        ```

        You can use in LangGraph's prebuilt `create_react_agent`:

        ```python
        from langgraph.prebuilt import create_react_agent
        from langgraph.config import get_config, get_store

        def prompt(state):
            config = get_config()
            memories = get_store().search(
                # Search within the same namespace as the one
                # we've configured for the agent
                ("memories", config["configurable"]["langgraph_user_id"]),
            )
            system_prompt = f"\"\"You are a helpful assistant.
        <memories>
        {memories}
        </memories>
        \"\"\"
            system_message = {"role": "system", "content": system_prompt}
            return [system_message, *state["messages"]]

        agent = create_react_agent(
            "anthropic:claude-3-5-sonnet-latest",
            tools=[
                create_manage_memory_tool(namespace=("memories", "{langgraph_user_id}")),
            ],
            store=store,
        )

        agent.invoke(
            {"messages": [{"role": "user", "content": "We've decided we like golang more than python for backend work"}]},
            config=config,
        )
        ```


        If you want to customize the expected schema for memories, you can do so by providing a `schema` argument.
        ```python
        from pydantic import BaseModel


        class UserProfile(BaseModel):
            name: str
            age: int | None = None
            recent_memories: list[str] = []
            preferences: dict | None = None


        memory_tool = create_manage_memory_tool(
            # All memories saved to this tool will live within this namespace
            # The brackets will be populated at runtime by the configurable values
            namespace=("memories", "{langgraph_user_id}", "user_profile"),
            schema=UserProfile,
            actions_permitted=["create", "update"],
            instructions="Update the existing user profile (or create a new one if it doesn't exist) based on the shared information.",
        )
        store = InMemoryStore(
            index={
                "dims": 1536,
                "embed": "openai:text-embedding-3-small",
            }
        )
        agent = create_react_agent(
            "anthropic:claude-3-5-sonnet-latest",
            prompt=prompt,
            tools=[
                memory_tool,
            ],
            store=store,
        )

        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "I'm 60 years old and have been programming for 5 days.",
                    }
                ]
            },
            config=config,
        )
        result["messages"][-1].pretty_print()
        # I've created a memory with your age of 60 and noted that you started programming 5 days ago...

        result = agent.invoke(
            {
                "messages": [
                    {"role": "user", "content": "Just had by 61'st birthday today!!"}
                ]
            },
            config=config,
        )
        result["messages"][-1].pretty_print()
        # Happy 61st birthday! 🎂 I've updated your profile to reflect your new age. Is there anything else I can help you with?
        print(
            store.search(
                ("memories", "123e4567-e89b-12d3-a456-426614174000", "user_profile")
            )
        )
        # [Item(
        #     namespace=['memories', '123e4567-e89b-12d3-a456-426614174000', 'user_profile'],
        #     key='1528553b-0900-4363-8dc2-c6b72844096e',
        #     value={
        # highlight-next-line
        #         'content': UserProfile(
        #             name='User',
        #             age=61,
        #             recent_memories=['Started programming 5 days ago'],
        #             preferences={'programming_experience': '5 days'}
        #         )
        #     },
        #     created_at='2025-02-07T01:12:14.383762+00:00',
        #     updated_at='2025-02-07T01:12:14.383763+00:00',
        #     score=None
        # )]
        ```

        If you want to limit the actions that can be taken by the tool, you can do so by providing a `actions_permitted` argument.

    """
    # 【中文研读】处理流程：配置 namespace 与允许操作，定义同步/异步执行函数，拼工具描述，封装为 StructuredTool。
    namespacer = utils.NamespaceTemplate(namespace)
    # 【中文研读】拒绝空操作集合，否则工具既无合法动作也无法选默认动作。
    if not actions_permitted:
        raise ValueError("actions_permitted cannot be empty")
    action_type = typing.Literal[actions_permitted]

    default_action = "create" if "create" in actions_permitted else actions_permitted[0]
    initial_store = store

    # 【中文研读】方法职责：异步执行创建、更新或删除
    # 【中文研读】输入参数：content（候选或工具提交的内容）；action（本次创建、更新或删除动作）；id（目标记忆标识）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    async def amanage_memory(
        content: typing.Optional[schema] = None,  # type: ignore
        action: action_type = default_action,  # type: ignore
        *,
        id: typing.Optional[uuid.UUID] = None,
    ):
        # 【中文研读】处理流程：取得 Store，校验操作及 ID，解析 namespace，删除直接调用 adelete，否则生成/复用 ID 后 aput。
        store = _get_store(initial_store)
        # 【中文研读】执行时再次核验动作，不能只依赖模型工具 Schema 限制。
        if action not in actions_permitted:
            raise ValueError(
                f"Invalid action {action}. Must be one of {actions_permitted}."
            )

        # 【中文研读】创建 ID 由工具生成，避免模型把一个已有 ID 当成新建目标覆盖。
        if action == "create" and id is not None:
            raise ValueError(
                "You cannot provide a MEMORY ID when creating a MEMORY. Please try again, omitting the id argument."
            )

        # 【中文研读】修改已有记忆必须有目标 ID；但该校验没有证明用户有权操作这个对象。
        if action in ("delete", "update") and not id:
            raise ValueError(
                "You must provide a MEMORY ID when deleting or updating a MEMORY."
            )
        namespace = namespacer()
        # 【中文研读】删除直接进入 Store；P3 还需当前资格屏障、清理待办和事务事件，因此不能原样开放此工具。
        if action == "delete":
            await store.adelete(namespace, key=str(id))
            return f"Deleted memory {id}"

        # 【中文研读】新建生成随机 UUID，更新复用原 ID；这不是请求幂等键，重复新建调用可能生成不同记录。
        id = id or uuid.uuid4()
        await store.aput(
            namespace,
            key=str(id),
            value={"content": _ensure_json_serializable(content)},
        )
        return f"{action}d memory {id}"

    # 【中文研读】方法职责：同步执行记忆管理
    # 【中文研读】输入参数：content（候选或工具提交的内容）；action（本次创建、更新或删除动作）；id（目标记忆标识）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def manage_memory(
        content: typing.Optional[schema] = None,  # type: ignore
        action: action_type = default_action,  # type: ignore
        *,
        id: typing.Optional[uuid.UUID] = None,
    ):
        # 【中文研读】处理流程：创建禁止调用方自填 ID，更新/删除要求 ID；合法后发起 Store 操作并返回说明文本。
        store = _get_store(initial_store)
        # 【中文研读】执行时再次核验动作，不能只依赖模型工具 Schema 限制。
        if action not in actions_permitted:
            raise ValueError(
                f"Invalid action {action}. Must be one of {actions_permitted}."
            )

        # 【中文研读】创建 ID 由工具生成，避免模型把一个已有 ID 当成新建目标覆盖。
        if action == "create" and id is not None:
            raise ValueError(
                "You cannot provide a MEMORY ID when creating a MEMORY. Please try again, omitting the id argument."
            )

        # 【中文研读】修改已有记忆必须有目标 ID；但该校验没有证明用户有权操作这个对象。
        if action in ("delete", "update") and not id:
            raise ValueError(
                "You must provide a MEMORY ID when deleting or updating a MEMORY."
            )
        namespace = namespacer()
        # 【中文研读】删除直接进入 Store；P3 还需当前资格屏障、清理待办和事务事件，因此不能原样开放此工具。
        if action == "delete":
            store.delete(namespace, key=str(id))
            return f"Deleted memory {id}"

        # 【中文研读】新建生成随机 UUID，更新复用原 ID；这不是请求幂等键，重复新建调用可能生成不同记录。
        id = id or uuid.uuid4()
        store.put(
            namespace,
            key=str(id),
            value={"content": _ensure_json_serializable(content)},
        )
        return f"{action}d memory {id}"

    if len(actions_permitted) == 1:
        verbs = f"{actions_permitted[0]} a memory"
    elif len(actions_permitted) == 2:
        verbs = (
            f"{actions_permitted[0].capitalize()} or {actions_permitted[1]} a memory"
        )
    else:
        prefix_names = ", ".join(
            (actions_permitted[0].capitalize(), *actions_permitted[1:-1])
        )
        verbs = f"{prefix_names}, or {actions_permitted[-1]} a memory"
    description = f"""{verbs} to persist across conversations.
Include the MEMORY ID when updating or deleting a MEMORY. Omit when creating a new MEMORY - it will be created for you.
{instructions}"""

    return _ToolWithRequired.from_function(
        manage_memory, amanage_memory, name=name, description=description
    )


_MEMORY_SEARCH_INSTRUCTIONS = ""


# 【中文研读】方法职责：创建记忆搜索工具
# 【中文研读】输入参数：namespace（存储命名空间或其模板）；instructions（传给提取模型的业务指令）；store（底层存储实例）；response_format（工具返回文本或文本与原始产物的方式）；name（工具或组件名称）。
# 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
def create_search_memory_tool(
    namespace: tuple[str, ...] | str,
    *,
    instructions: str = _MEMORY_SEARCH_INSTRUCTIONS,
    store: BaseStore | None = None,
    response_format: typing.Literal["content", "content_and_artifact"] = "content",
    name: str = "search_memory",
):
    """Create a tool for searching memories stored in a LangGraph BaseStore.

    This function creates a tool that allows AI assistants to search through previously stored
    memories using semantic or exact matching. The tool returns both the memory contents and
    the raw memory objects for advanced usage.

    Args:
        instructions: Custom instructions for when to use the search tool.
            Defaults to a predefined set of guidelines.
        namespace: The namespace structure for organizing memories in LangGraph's BaseStore.
            Uses runtime configuration with placeholders like `{langgraph_user_id}`.
            See [Memory Namespaces](../concepts/conceptual_guide.md#memory-namespaces).
        store: The BaseStore to use for searching. If not provided, the tool will use the configured BaseStore in your graph or entrypoint.
            Only set if you intend on using these tools outside the LangGraph context.

    Returns:
        search_tool (Tool): A decorated function that can be used as a tool for memory search.
            The tool returns both serialized memories and raw memory objects.

    The resulting tool has a signature that looks like the following:
        ```python
        def search_memory(
            query: str,  # Search query to match against memories
            limit: int = 10,  # Maximum number of results to return
            offset: int = 0,  # Number of results to skip
            filter: dict | None = None,  # Additional filter criteria
        ) -> tuple[list[dict], list]: ...  # Returns (serialized memories, raw memories)
        ```
    _Note: the tool supports both sync and async usage._


    Tip:
        This tool connects with the LangGraph [BaseStore](https://langchain-ai.github.io/langgraph/reference/store/#langgraph.store.base.BaseStore) configured in your graph or entrypoint.
        It will not work if you do not provide a store.

    !!! example "Examples"
        ```python
        from langmem import create_search_memory_tool
        from langgraph.func import entrypoint
        from langgraph.store.memory import InMemoryStore

        search_tool = create_search_memory_tool(
            namespace=("project_memories", "{langgraph_user_id}"),
        )

        store = InMemoryStore(
            index={
                "dims": 1536,
                "embed": "openai:text-embedding-3-small",
            }
        )


        @entrypoint(store=store)
        async def workflow(state: dict, *, previous=None):
            # Search for memories about Python
            memories, _ = await search_tool.ainvoke(
                {"query": "Python preferences", "limit": 5}
            )
            print(memories)
            return entrypoint.final(value=memories, save={})
        ```
    """
    # 【中文研读】处理流程：保存 namespace 模板和 Store，定义两种调用入口，按 response_format 决定是否携带原始检索产物。
    namespacer = utils.NamespaceTemplate(namespace)
    initial_store = store

    # 【中文研读】方法职责：异步按查询、过滤和分页搜索 Store
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；limit（最多返回的记录数）；offset（分页跳过的记录数）；filter（单组检索过滤条件）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    async def asearch_memory(
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
        filter: typing.Optional[dict] = None,
    ):
        # 【中文研读】处理流程：解析 namespace 后 await 搜索，结果可返回 JSON 文本或文本与原始记录二元组。
        store = _get_store(initial_store)
        namespace = namespacer()
        memories = await store.asearch(
            namespace,
            query=query,
            filter=filter,
            limit=limit,
            offset=offset,
        )
        # 【中文研读】同时返回模型可读文本和程序可用的原始记录，供搜索管道后续去重排序。
        if response_format == "content_and_artifact":
            return utils.dumps([m.dict() for m in memories]), memories
        return utils.dumps([m.dict() for m in memories])

    # 【中文研读】方法职责：同步搜索 Store 并格式化返回
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；limit（最多返回的记录数）；offset（分页跳过的记录数）；filter（单组检索过滤条件）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def search_memory(
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
        filter: typing.Optional[dict] = None,
    ):
        # 【中文研读】处理流程：查询得到 SearchItem，统一转 JSON；content_and_artifact 额外保留机器可用原记录。
        store = _get_store(initial_store)
        namespace = namespacer()
        memories = store.search(
            namespace,
            query=query,
            filter=filter,
            limit=limit,
            offset=offset,
        )
        # 【中文研读】同时返回模型可读文本和程序可用的原始记录，供搜索管道后续去重排序。
        if response_format == "content_and_artifact":
            return utils.dumps([m.dict() for m in memories]), memories
        return utils.dumps([m.dict() for m in memories])

    description = """Search your long-term memories for information relevant to your current context. {instructions}""".format(
        instructions=instructions
    )

    return StructuredTool.from_function(
        search_memory,
        asearch_memory,
        name=name,
        description=description,
        response_format=response_format,
    )


# 【中文研读】方法职责：解析显式注入或运行上下文中的 Store
# 【中文研读】输入参数：initial_store（显式注入的存储实例）。
# 【中文研读】返回约定：BaseStore。结果的业务含义与失败分支见下面处理流程。
def _get_store(initial_store: BaseStore | None = None) -> BaseStore:
    # 【中文研读】处理流程：找不到上下文 Store 时把 RuntimeError 转为配置错误，不吞掉并伪造结果。
    try:
        if initial_store is not None:
            store = initial_store
        else:
            store = get_store()
        return store
    except RuntimeError as e:
        raise errors.ConfigurationError("Could not get store") from e


# 【中文研读】方法职责：尽量将模型内容转换为可存数据
# 【中文研读】输入参数：content（候选或工具提交的内容）。
# 【中文研读】返回约定：typing.Any。结果的业务含义与失败分支见下面处理流程。
def _ensure_json_serializable(content: typing.Any) -> typing.Any:
    # Right now just support primitives and pydantic models
    # 【中文研读】处理流程：基础类型原样返回，Pydantic 用 model_dump，转换异常记日志并转字符串；这并非严格业务 Schema 验证。
    if isinstance(content, (str, int, float, bool, dict, list)):
        return content
    # 【中文研读】将结构化模型转换为可序列化值；失败退成字符串会丢结构语义，P3 候选提交需更严格校验。
    if hasattr(content, "model_dump"):
        try:
            return content.model_dump(mode="json")
        except Exception as e:
            logger.error(e)
            return str(content)
    return content


# 【中文研读】类型职责：StructuredTool 的兼容扩展，保证导出的工具 Schema 含 required 键；不是额外业务授权器。
class _ToolWithRequired(StructuredTool):
    # 【中文研读】方法职责：调整工具调用 Schema 的 required 键生成
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：'ArgsSchema'。结果的业务含义与失败分支见下面处理流程。
    @functools.cached_property
    def tool_call_schema(self) -> "ArgsSchema":
        # 【中文研读】处理流程：取父类 Schema，通过 Pydantic 配置注册补丁；兼容失败时保持原行为。
        tcs = super().tool_call_schema
        try:
            if tcs.model_config:
                tcs.model_config["json_schema_extra"] = _ensure_schema_contains_required
            elif ConfigDict is not None:
                tcs.model_config = ConfigDict(
                    json_schema_extra=_ensure_schema_contains_required
                )
        except Exception:
            pass
        return tcs


# 【中文研读】方法职责：补齐缺失的 required 空列表
# 【中文研读】输入参数：schema（单个输出或数据结构定义）。
# 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
def _ensure_schema_contains_required(schema: dict) -> None:
    # 【中文研读】处理流程：setdefault 只在键不存在时补值，不把所有字段变成必填。
    schema.setdefault("required", [])
