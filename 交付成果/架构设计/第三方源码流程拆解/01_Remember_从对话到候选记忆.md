# Remember：从一段对话走到候选记忆

[返回流程总入口](README.md)

本章以 LangMem Core 的异步路径为主。先跟随一个调用进入方法、展开被调用的帮助函数，再返回原方法继续。最后单列 Store 路径，说明什么时候开始出现数据库副作用。两条路径不会被拼成 P3 必须采用的一条链。

## 先看这一条执行路径

```text
启动：create_memory_manager → MemoryManager.__init__
请求：MemoryManager.ainvoke 前半段
  → _prepare_messages → 返回提示消息
  → _prepare_existing → 返回旧记忆三元组
  → 创建 trustcall extractor → 调模型
  → 按 ID 汇总本轮结果 → 判断是否继续下一轮
  → _filter_response → 返回 ExtractedMemory 列表
P3 边界：校验证据与授权 → 正式版本提交 → 任务/Outbox
旁支：MemoryStoreManager.ainvoke 会自己查询及写 Store
```

贯穿示例：用户说“项目预算最终批准为十五万元”，旧记忆是“预算二十万元”。这里只用它解释数据如何移动，不把示意候选写成真实模型实验结果。第一轮学习可显式关闭自动更新/删除；研究更新时，再提供经过授权的稳定旧 ID 和更新开关。

以下代码从已核对版本逐段摘录；省略导入、英文说明文档及英文整行注释，保留执行语句、字符串与中文研读注释。标为“片段”的代码保留原方法的局部上下文，不能当成独立函数运行。

## 按执行顺序展开

- [步骤 01：创建候选提取器](#s01)
- [步骤 02：保存模型与规则](#s02)
- [步骤 03：请求进入，读出本轮输入](#s03)
- [步骤 04：展开：把原始对话组织成模型提示](#s04)
- [步骤 05：展开：统一已有记忆形状](#s05)
- [步骤 06：回到请求：建立提取器与待提交模型的 payload](#s06)
- [步骤 07：调用模型：第一轮与后续轮次如何不同](#s07)
- [步骤 08：回到循环：给候选找 ID 并汇总](#s08)
- [步骤 09：决定退出，或把本轮结果交给下一轮](#s09)
- [步骤 10：展开：区分新增、修改和删除标记](#s10)
- [步骤 11：返回候选，正式提交交回 P3](#s11)
- [步骤 12：存储旁支：StoreManager 怎样先读已有记忆](#s12)
- [步骤 13：存储旁支：整理命中并保留回写位置](#s13)
- [步骤 14：存储旁支：同一存储位置得到相同内部 ID](#s14)
- [步骤 15：存储旁支：模型处理完后究竟怎么写 Store](#s15)

<a id="s01"></a>

### 01　创建候选提取器

**当前执行位置：** `create_memory_manager`，完整方法或类型摘录。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 624—781 行。

**收到什么：** 模型对象或名称、候选 Schema、提取指令和允许操作。

**这一段怎么处理：** 工厂只把配置交给 MemoryManager；本次还没有输入对话。

```python
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

    # 【中文研读】处理流程：把模型、Schema、提示及操作开关原样交给构造器，不创建 Store。
    return MemoryManager(
        model,
        schemas=schemas,
        instructions=instructions,
        enable_inserts=enable_inserts,
        enable_updates=enable_updates,
        enable_deletes=enable_deletes,
    )
```

**执行后得到什么：** 一个可调用的 Manager 对象。

**接下来到哪里：** 进入步骤 02 的构造器；构造结束后，由业务任务调用步骤 03。

**失败与 P3 责任：** 默认 enable_updates=True，不适合不经判断就照抄；学习纯提取需显式关闭更新与删除。

<a id="s02"></a>

### 02　保存模型与规则

**当前执行位置：** `MemoryManager.__init__`，完整方法或类型摘录。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 260—278 行。

**收到什么：** 来自工厂的配置。

**这一段怎么处理：** 已有模型对象直接使用；字符串名称通过模型初始化接口解析，其余配置保存到实例。

```python
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
```

**执行后得到什么：** self.model、schemas、instructions 和操作开关。

**接下来到哪里：** 回到调用方；有对话后进入 ainvoke。

**失败与 P3 责任：** 构造成功只代表对象装配完成，不表示外部模型可用或事实已经写入。

<a id="s03"></a>

### 03　请求进入，读出本轮输入

**当前执行位置：** `MemoryManager.ainvoke`，方法内部片段。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 289—294 行。

**收到什么：** input 中的 messages、existing、max_steps。

**这一段怎么处理：** 缺省轮数设为 1，取出新消息和已有记忆。下面还没有调用模型。

```python
# 【中文研读】处理流程：准备输入 → 创建提取器 → 逐轮调用模型并合并 ID → 判断 Done → 必要时追加工具反馈 → 过滤最终候选。
max_steps = input.get("max_steps")
if max_steps is None:
    max_steps = 1
messages = input["messages"]
existing = input.get("existing")
```

**执行后得到什么：** 本次局部变量。

**接下来到哪里：** 步骤 04 展开随后调用的 _prepare_messages。

**失败与 P3 责任：** max_steps 是一次调用内的模型轮数，不是公共任务重试次数。

<a id="s04"></a>

### 04　展开：把原始对话组织成模型提示

**当前执行位置：** `MemoryManager._prepare_messages`，完整方法或类型摘录。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 531—553 行。

**收到什么：** 用户消息、轮数及实例上的 instructions。

**这一段怎么处理：** 给对话生成临时 session 标记；多轮时加轮数说明；返回系统/用户消息列表。

```python
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
```

**执行后得到什么：** prepared_messages，供后面 extractor 读取。

**接下来到哪里：** 返回 ainvoke，然后执行步骤 05。

**失败与 P3 责任：** 临时 UUID 是提示标签，不是可信 session 或租户身份；英文提示字符串也是行为的一部分，摘录保留原值。

<a id="s05"></a>

### 05　展开：统一已有记忆形状

**当前执行位置：** `MemoryManager._prepare_existing`，完整方法或类型摘录。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 558—587 行。

**收到什么：** None、字符串列表，或带 ID 的已有记忆。

**这一段怎么处理：** None 返回空；字符串生成临时 ID；三元组保留；二元组补充类型名。

```python
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
            id_, value = e[0], e[1]
            kind = (
                value.__repr_name__() if isinstance(value, BaseModel) else "__any__"
            )
            result.append((id_, kind, value))
    return result
```

**执行后得到什么：** (id, kind, value) 的列表。

**接下来到哪里：** 返回 ainvoke，步骤 06 用这批 ID 建立外部旧对象集合。

**失败与 P3 责任：** 字符串路径没有稳定业务身份；P3 更新必须提供已核验范围和版本的稳定引用。

<a id="s06"></a>

### 06　回到请求：建立提取器与待提交模型的 payload

**当前执行位置：** `MemoryManager.ainvoke`，方法内部片段。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 298—313 行。

**收到什么：** prepared_messages 与 prepared_existing。

**这一段怎么处理：** 记录调用前已有的 ID；按 Schema 和操作开关创建 extractor；把消息和旧记忆放入 payload。

```python
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
results: dict[str, BaseModel] = {}
```

**执行后得到什么：** extractor、payload，以及空的 results 字典。

**接下来到哪里：** 进入步骤 07 的有限轮次循环。

**失败与 P3 责任：** results 仅在内存中；到此没有业务事务或持久检查点。

<a id="s07"></a>

### 07　调用模型：第一轮与后续轮次如何不同

**当前执行位置：** `MemoryManager.ainvoke`，方法内部片段。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 314—327 行。

**收到什么：** 本轮 payload、Schema、操作开关。

**这一段怎么处理：** 第二轮开始加入 Done 工具，允许模型声明结束；然后 await extractor.ainvoke。

```python
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
```

**执行后得到什么：** response：结构化 responses、response_metadata，以及模型消息。

**接下来到哪里：** 模型调用返回后，继续步骤 08。

**失败与 P3 责任：** 这一层等待 trustcall；模型失败直接向外传播。结构化返回成功不代表原文证据正确。

<a id="s08"></a>

### 08　回到循环：给候选找 ID 并汇总

**当前执行位置：** `MemoryManager.ainvoke`，方法内部片段。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 328—347 行。

**收到什么：** 模型结构化结果与元信息。

**这一段怎么处理：** 识别 Done；删除标记用目标 ID，更新优先使用元信息 ID，新建缺少 ID 时生成 UUID；同 ID 覆盖，未改动旧项补回。

```python
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
```

**执行后得到什么：** 本轮累计 results。

**接下来到哪里：** 步骤 09 判断结束还是继续。

**失败与 P3 责任：** 返回集合可含未变化的旧记忆，不全是新建候选；随机 UUID 也不是请求幂等键。

<a id="s09"></a>

### 09　决定退出，或把本轮结果交给下一轮

**当前执行位置：** `MemoryManager.ainvoke`，方法内部片段。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 348—391 行。

**收到什么：** results、最后的 AI 消息和剩余轮数。

**这一段怎么处理：** Done 或无工具调用时退出；否则构造工具反馈，并过滤删除项作为下轮 existing。

```python
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
    payload = {
        "messages": prepared_messages,
        "existing": self._filter_response(
            list(results.items()), external_ids, exclude_removals=True
        ),
    }
```

**执行后得到什么：** 下轮 payload，或进入最终返回。

**接下来到哪里：** 内部与最终都调用 _filter_response，步骤 10 展开它。

**失败与 P3 责任：** 多轮链只是局部计算；不要把工具反馈中的“inserted”文本当作数据库已提交证据。

<a id="s10"></a>

### 10　展开：区分新增、修改和删除标记

**当前执行位置：** `MemoryManager._filter_response`，完整方法或类型摘录。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 592—618 行。

**收到什么：** 候选二元组、调用前外部旧 ID、是否排除删除标记。

**这一段怎么处理：** 供下一轮使用时排除所有删除项；最终返回时仅保留针对外部旧对象的删除，舍弃本轮临时对象的创建后删除。

```python
@staticmethod
def _filter_response(
    memories: list[ExtractedMemory],
    external_ids: set[str],
    exclude_removals: bool = False,
) -> list[ExtractedMemory]:
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
            if is_removal and (rid not in external_ids):
                continue
        results.append(ExtractedMemory(id=rid, content=value))
    return results
```

**执行后得到什么：** ExtractedMemory(id, content) 列表。

**接下来到哪里：** 返回调用它的位置；若是最终调用，步骤 11 离开 Core。

**失败与 P3 责任：** 这里的过滤只处理提取语义，没有检查业务租户、当前版本或授权。

<a id="s11"></a>

### 11　返回候选，正式提交交回 P3

**当前执行位置：** `MemoryManager.ainvoke`，方法内部片段。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 392—394 行。

**收到什么：** 最终 results。

**这一段怎么处理：** 以最终模式调用刚才的过滤器，并将结果返回。

```python
return self._filter_response(
    list(results.items()), external_ids, exclude_removals=False
)
```

**执行后得到什么：** 候选列表；不是已提交 Memory。

**接下来到哪里：** P3 Remember 校验证据、版本和幂等后，进入自己的事务。

**失败与 P3 责任：** 进程在此后、P3 提交前退出时，不能以日志里已有候选推断事实存在。

<a id="s12"></a>

### 12　存储旁支：StoreManager 怎样先读已有记忆

**当前执行位置：** `MemoryStoreManager.ainvoke`，方法内部片段。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 1149—1183 行。

**收到什么：** messages、查询配置、namespace 和 Store。

**这一段怎么处理：** 查看独立存储编排的入口：先确定 Store/namespace，走查询生成或对话窗口检索，再汇总旧记录。

```python
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
        queries = utils.get_dialated_windows(
            input["messages"], self.query_limit // 4
        )
        search_results_lists = await asyncio.gather(
            *[store.asearch(namespace, query=query) for query in queries]
        )
```

**执行后得到什么：** store_map，随后转成 Core 所需的旧记忆。

**接下来到哪里：** 步骤 13 展开搜索结果整理；这不是 Core 返回后的自动下一步。

**失败与 P3 责任：** 本旁支是参考对照，不能当作 P3 事实提交层。

<a id="s13"></a>

### 13　存储旁支：整理命中并保留回写位置

**当前执行位置：** `MemoryStoreManager._sort_results`，完整方法或类型摘录。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 1130—1144 行。

**收到什么：** 多路 Store 搜索结果、query_limit。

**这一段怎么处理：** 按存储位置去重并按分数限量；用稳定内部 ID 建回查映射。

```python
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
```

**执行后得到什么：** 内部 ID 到 Store 记录的映射。

**接下来到哪里：** _stable_id 的算法见下一步；后续 StoreManager 再调用 Core。

**失败与 P3 责任：** 检索分数不是来源可信度；映射位置也不能证明授权。

<a id="s14"></a>

### 14　存储旁支：同一存储位置得到相同内部 ID

**当前执行位置：** `MemoryStoreManager._stable_id`，完整方法或类型摘录。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 1060—1063 行。

**收到什么：** namespace 与 key。

**这一段怎么处理：** 使用确定性 UUID 算法把位置转为内部 ID。

```python
@staticmethod
def _stable_id(item: SearchItem) -> str:
    # 【中文研读】处理流程：相同存储位置得到相同内部 ID，便于对齐提取结果与原记录；它不是 P3 版本号。
    return uuid.uuid5(uuid.NAMESPACE_DNS, str((*item.namespace, item.key))).hex
```

**执行后得到什么：** 供本轮 Core 与 Store 记录对应的稳定 ID。

**接下来到哪里：** 回到 _sort_results；模型加工结束后进入步骤 15 的写回片段。

**失败与 P3 责任：** 这不是 P3 的记忆版本或 CAS 条件。

<a id="s15"></a>

### 15　存储旁支：模型处理完后究竟怎么写 Store

**当前执行位置：** `MemoryStoreManager.ainvoke`，方法内部片段。[出处](../第三方源码中文注释/langmem/src/langmem/knowledge/extraction.py)，注释版第 1250—1289 行。

**收到什么：** 主提取和附加阶段合并后的 store_based、ephemeral、removed_ids。

**这一段怎么处理：** 比較新旧内容，构建 final_puts 与删除操作；通过 gather 并行提交底层 Store 操作。

```python
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
```

**执行后得到什么：** 返回待写入项信息；底层操作可能已经产生副作用。

**接下来到哪里：** 回到 StoreManager 的调用方；P3 需另行设计原子提交，不能直接套用。

**失败与 P3 责任：** 多个 aput/adelete 不是业务事实、Task、Outbox 的一个原子事务；其中一项失败不保证其他项回滚。

## 本章出现的外部调用与停止展开的位置

| 调用或能力 | 在这条链中的作用 | 为什么在这里画边界 |
|---|---|---|
| init_chat_model / BaseChatModel | 加载或接收聊天模型适配器 | 下游模型调用接口；不在此展开各厂商 SDK |
| utils.get_conversation | 把消息组织成可读对话文本 | 文本格式化辅助边界；不把它当来源真实性校验 |
| trustcall.create_extractor / extractor.ainvoke | 结构化工具调用、模型输出与修订 | 本章明确停在模型编排依赖接口；没有摘录 trustcall 或模型推理内核 |
| Store.asearch / aput / adelete | 搜索和修改底层 Store | 实际存储由注入实现决定；接口调用不能证明事务语义 |
| model_dump / Pydantic | 结构化对象到普通数据的转换 | 序列化与字段校验；不是事实或权限校验 |

## 对照 P3 应怎样使用

P3 应把 Core 的返回当作候选，交回 Remember 的规则层。候选中的 ID、文本、RemoveDoc 都不能直接成为权威事实。正式 ID/版本、来源证据、删除屏障和 Memory/Task/Outbox 原子提交由 Remember 与公共事务层完成。

若模型返回后进程退出，Core 的 results 字典随进程消失；恢复可能重调模型。需要业务幂等保证正式效果不重复，不能把本地多轮循环称为持久恢复。

对照顺序：先掌握步骤 01—11 的 Core；步骤 12—15 是存储旁支的对比研读，专门解释为什么不能把现成 StoreManager 写回直接当作 P3 提交协议。
