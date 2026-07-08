from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from mock_services import (
    DemoEnv,
    episodic_detail_rows,
    memory_table_rows,
    normalize_score,
    truncate,
)
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.reactive import reactive
from textual.widgets import Footer, Header, RichLog, Static

from aether_agent_memory import (
    ContextRequest,
    Memory,
    MemorySignal,
    MemoryType,
    SignalType,
    SourceType,
)

AGENT_ID = "agent-duty-01"
USER_ID = "user-duty-chief"
SESSION_1 = "sess-duty-001"
SESSION_2 = "sess-duty-002"
MANUAL_SOURCE_ID = "doc-flood-manual-v3"


def _make_table(headers: list[str], rows: list[list[str]], *, title: str | None = None) -> Table:
    table = Table(title=title, show_lines=False, expand=True, pad_edge=False, box=None)
    for header in headers:
        table.add_column(header, overflow="fold", no_wrap=False)
    for row in rows:
        table.add_row(*[str(cell) for cell in row])
    return table


class P3DataflowApp(App[None]):
    TITLE = "P3DataflowApp"
    SUB_TITLE = "P3 数据流框架演示"

    CSS = """
    Screen {
        layout: vertical;
    }
    #main-area {
        height: 1fr;
        layout: horizontal;
    }
    #user-pane {
        width: 5fr;
        border: round $primary;
        padding: 0 1;
        background: $surface;
        overflow: hidden hidden;
    }
    #system-pane {
        width: 7fr;
        border: round $accent;
        padding: 0 1;
        background: $surface;
        overflow: hidden hidden;
    }
    #user-pane RichLog, #system-pane RichLog {
        height: 1fr;
        scrollbar-size: 1 1;
        overflow-x: hidden;
        overflow-y: auto;
    }
    #status-bar {
        height: 3;
        dock: bottom;
        background: $panel;
        border-top: solid $primary;
        padding: 0 2;
        content-align: center middle;
    }
    """

    stage: reactive[str] = reactive("准备中")

    def __init__(self, step_delay: float = 1.0) -> None:
        super().__init__()
        self.env = DemoEnv()
        self._advance: asyncio.Event = asyncio.Event()
        self.step_delay = step_delay

    async def _step(self) -> None:
        if self.step_delay > 0:
            await asyncio.sleep(self.step_delay)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main-area"):
            with Vertical(id="user-pane") as user_pane:
                user_pane.border_title = "用户视角 · Agent 对话"
                yield RichLog(id="user-log", wrap=True, markup=True)
            with Vertical(id="system-pane") as system_pane:
                system_pane.border_title = "P3 数据流框架 · B1 → B2 → B3"
                yield RichLog(id="system-log", wrap=True, markup=True)
        yield Static("准备中", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.set_stage("场景 1 · 上传手册 → B1 语义化 → B2 记忆化 → B3 保温")
        self.call_after_refresh(self._start_demo)

    def _start_demo(self) -> None:
        self.run_worker(self.run_scenario, exclusive=True)

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        bar = self.query_one("#status-bar", Static)
        bar.update(
            f"[bold cyan]{stage}[/bold cyan]    [dim]按 Enter 进入下一步 · Ctrl+C 退出[/dim]"
        )

    def user_log(self) -> RichLog:
        return self.query_one("#user-log", RichLog)

    def system_log(self) -> RichLog:
        return self.query_one("#system-log", RichLog)

    def _chat_panel_width(self) -> int:
        pane = self.query_one("#user-pane", Vertical)
        log = self.user_log()

        pane_width = pane.content_region.width or pane.size.width
        log_width = log.content_region.width or log.size.width
        fallback_width = max(44, int(self.size.width * 0.32))

        usable_width = max(pane_width, log_width, fallback_width)
        bubble_width = min(usable_width - 2, 84)
        return max(44, bubble_width)

    def user_write(self, text: str, *, who: str = "user") -> None:
        label = "用户" if who == "user" else "Agent"
        color = "bold cyan" if who == "user" else "bold green"
        body = Text(text, no_wrap=False, overflow="fold")
        panel = Panel(
            body,
            title=f"[{color}]{label}[/{color}]",
            title_align="left",
            border_style="cyan" if who == "user" else "green",
            padding=(0, 1),
            width=self._chat_panel_width(),
            expand=False,
        )
        self.user_log().write(panel)

    def system_write(self, content: Any) -> None:
        self.system_log().write(content)

    def system_section(self, title: str) -> None:
        self.system_write(Rule(title, style="bold cyan"))

    def module_log(self, module: str, message: str) -> None:
        styles = {"B1": "bold yellow", "B2": "bold green", "B3": "bold magenta"}
        style = styles.get(module, "bold white")
        self.system_write(f"[{style}]{module}[/{style}] {message}")

    async def wait_for_enter(self, hint: str) -> None:
        bar = self.query_one("#status-bar", Static)
        bar.update(f"[bold cyan]{self.stage}[/bold cyan]    [yellow]{hint}[/yellow]")
        self._advance.clear()
        await self._advance.wait()
        bar.update(
            f"[bold cyan]{self.stage}[/bold cyan]    [dim]按 Enter 进入下一步 · Ctrl+C 退出[/dim]"
        )
        await asyncio.sleep(0.1)

    def on_key(self, event: Key) -> None:
        if event.key in ("enter", "space"):
            self._advance.set()

    async def run_scenario(self) -> None:
        try:
            await self.scenario_1()
            await self.wait_for_enter("按 Enter 进入“任务执行 → Working/Episodic 流转”")
            self.system_log().clear()
            await self.scenario_2()
            await self.wait_for_enter("按 Enter 进入“新会话召回 → B3 插入调度”")
            self.system_log().clear()
            await self.scenario_3()
            await self.wait_for_enter("按 Enter 查看“三个场景总结”")
            self.system_log().clear()
            self.user_log().clear()
            await self.summary()
            await self.wait_for_enter("演示结束，按 Enter 退出")
            self.exit()
        except Exception as exc:
            self.system_write(f"[red]演示异常：{exc}[/red]")
            raise

    async def scenario_1(self) -> None:
        self.set_stage("场景 1 · 上传手册 → B1 语义化 → B2 记忆化 → B3 保温")
        self.system_section("场景 1 · 文档入库到长期知识")

        self.user_write("我刚上传了《汛期值班手册》，后面问到暴雨响应请按这份手册回答。", who="user")
        await self._step()
        self.user_write("已接收手册，我先解析条款并纳入长期知识。", who="agent")
        await self._step()

        chunks = [
            "一小时雨量达到 50mm 时，建议进入三级响应。",
            "连续两小时雨量持续升高时，需要提前通知西城和北河片区值守。",
            "用户显式指定的值班手册属于权威依据，后续回答应优先引用。",
        ]

        self.module_log("B1", f"接收 source_id={MANUAL_SOURCE_ID}，识别为需语义化的权威文档")
        await self._step()

        chunk_rows: list[list[str]] = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_id = f"chunk-manual-{index:03d}"
            vector = await self.env.embedder.embed_one(chunk)
            ref = await self.env.storage.put(f"manual/{chunk_id}", chunk.encode("utf-8"))
            chunk_rows.append([chunk_id, truncate(chunk, 24), len(vector), ref.object_key])
            self.module_log(
                "B1",
                f"切分 {chunk_id}，绑定 source_id/object_key，生成 embedding dim={len(vector)}，写入 P2-E1 / {ref.object_key}",
            )
            await self._step()

        self.system_write(
            _make_table(["chunk_id", "内容", "dim", "P2 object_key"], chunk_rows)
        )
        await self._step()

        semantic_items: list[Memory] = []
        for chunk in chunks[:2]:
            mem = Memory(
                type=MemoryType.SEMANTIC,
                session_id=SESSION_1,
                agent_id=AGENT_ID,
                user_id=USER_ID,
                content=chunk,
                source=SourceType.DISTILLED,
                tags=["manual", "rule"],
                metadata={"source_id": MANUAL_SOURCE_ID},
            )
            await self.env.semantic.write(mem)
            semantic_items.append(mem)
            self.module_log("B2", f"接收 B1 返回的 chunk/source/embedding_ref，沉淀 Semantic Memory id={mem.id[:8]}")
            await self._step()

        upload_event = Memory(
            type=MemoryType.EPISODIC,
            session_id=SESSION_1,
            agent_id=AGENT_ID,
            user_id=USER_ID,
            content="[事件] 用户上传《汛期值班手册》并声明其为后续回答依据",
            source=SourceType.USER,
            tags=["manual", "upload"],
            metadata={"source_id": MANUAL_SOURCE_ID},
        )
        await self.env.episodic.write(upload_event)
        self.module_log("B2", f"写入 Episodic Memory id={upload_event.id[:8]}，保留上传事件轨迹与来源追溯")
        await self._step()

        self.module_log("B2", "输出 memory signal：authoritative=true / user_defined=true / source_type=document")
        await self._step()
        self.module_log("B3", "读取 B2 的 memory signal，并结合对象状态，判断该手册属于高价值长期知识")
        await self._step()
        self.module_log("B3", "输出 Pin / Keep 建议：手册规则保留在 L1，避免后续被冷降级")
        await self._step()
        self.module_log("B2", "接收调度结果并落账：后续召回该手册规则时优先使用当前热层副本")
        await self._step()

        self.system_write("\n[bold]当前 Semantic Memory[/bold]")
        self.system_write(
            _make_table(
                ["ID", "层级", "状态", "内容", "来源", "访问次数", "过期时间"],
                memory_table_rows(semantic_items),
            )
        )

    async def scenario_2(self) -> None:
        self.set_stage("场景 2 · 任务执行 → B2 归档 → B3 升温")
        self.system_section("场景 2 · 会话内执行、归档与升温")

        turns = [
            (
                "现在帮我基于今天 9 点到 11 点的雨量和手册给出值班建议。",
                "正在拉取监测数据并结合手册判断。",
                ["task", "rainfall"],
            ),
            (
                "如果西城站一小时雨量达到 58mm，需要升级到什么响应？",
                "按手册建议进入三级响应，并对西城片区提前布防。",
                ["task", "response-level"],
            ),
        ]

        for message, reply, tags in turns:
            self.user_write(message, who="user")
            await self._step()
            mem = Memory(
                type=MemoryType.WORKING,
                session_id=SESSION_1,
                agent_id=AGENT_ID,
                user_id=USER_ID,
                content=f"[用户] {message}",
                source=SourceType.USER,
                tags=tags,
            )
            await self.env.working.write(mem)
            self.module_log("B2", f"接收对话事件，写入 Working Memory id={mem.id[:8]}，tags={tags}")
            await self._step()
            self.user_write(reply, who="agent")
            await self._step()

        tool_summary = "监测工具返回：西城站 10:00-11:00 一小时雨量 58mm，北河站 42mm。"
        self.module_log("B2", "收到工具调用结果，判定该结果需要归档并支持跨会话检索")
        await self._step()
        self.module_log("B1", "接收工具结果摘要，执行向量化，供后续任务复盘与跨会话检索")
        await self._step()
        tool_ref = await self.env.storage.put("tool/west-city-rainfall", tool_summary.encode("utf-8"))
        tool_memory = Memory(
            type=MemoryType.EPISODIC,
            session_id=SESSION_1,
            agent_id=AGENT_ID,
            user_id=USER_ID,
            content=tool_summary,
            source=SourceType.TOOL,
            tags=["tool", "west-city", "rainfall"],
            p2_ref=tool_ref,
        )
        await self.env.episodic.write(tool_memory)
        self.module_log("B2", f"接收 B1 的 embedding 结果，归档为 Episodic Memory id={tool_memory.id[:8]}")
        await self._step()

        working_items = await self.env.working.query(session_id=SESSION_1)
        archived: list[Memory] = [tool_memory]
        for item in working_items:
            ref = await self.env.storage.put(f"episodic/{item.id}", item.content.encode("utf-8"))
            ep = Memory(
                type=MemoryType.EPISODIC,
                session_id=item.session_id,
                agent_id=item.agent_id,
                user_id=item.user_id,
                content=item.content,
                source=item.source,
                tags=list(item.tags),
                metadata={"archived_from": item.id},
                p2_ref=ref,
            )
            self.module_log("B2", f"会话阶段结束，准备把 Working Memory {item.id[:8]} 迁入 Episodic")
            await self._step()
            self.module_log("B1", f"收到归档请求，对 memory={item.id[:8]} 生成向量表示并绑定来源")
            await self._step()
            await self.env.episodic.write(ep)
            await self.env.working.delete(item.id)
            archived.append(ep)
            self.module_log("B2", f"完成归档：Working {item.id[:8]} → Episodic {ep.id[:8]}")
            await self._step()

        hot_signal = MemorySignal(
            memory_id=tool_memory.id,
            memory_type=tool_memory.type,
            session_id=tool_memory.session_id,
            agent_id=tool_memory.agent_id,
            signal_type=SignalType.PROMOTION_HINT,
            heat=0.82,
            metadata={"reason": "tool_result_reused", "station": "west-city"},
        )
        await self.env.emitter.emit(hot_signal)
        self.module_log("B2", f"输出 MemorySignal id={tool_memory.id[:8]}，heat={hot_signal.heat}")
        await self._step()
        self.module_log("B3", "读取 memory signal，并结合对象访问热度和当前对象层级状态做 Heuristic 判断")
        await self._step()
        self.module_log("B3", "输出 Promote 建议：高复用工具证据继续保留在热层，供后续任务直接命中")
        await self._step()
        self.module_log("B2", "接收 Promote 建议并回写优先级：后续构建 Context Pack 时提高该证据排序")
        await self._step()

        self.system_write("\n[bold]当前 Episodic Memory[/bold]")
        self.system_write(
            _make_table(
                ["ID", "状态", "内容", "Embedding", "P2 object_key", "存储层"],
                episodic_detail_rows(archived),
            )
        )

    async def scenario_3(self) -> None:
        self.set_stage("场景 3 · 新会话召回 → B2 联合召回 → B3 回流")
        self.system_section("场景 3 · 联合召回、调度回流与应答")

        self.user_write("上次西城站那次为什么建议三级响应？给我引用依据。", who="user")
        await self._step()

        request = ContextRequest(
            session_id=SESSION_2,
            agent_id=AGENT_ID,
            user_id=USER_ID,
            query="上次西城站为什么建议三级响应，请给我引用依据",
            memory_types=[MemoryType.EPISODIC, MemoryType.SEMANTIC],
            max_tokens=256,
            max_candidates=8,
        )

        self.module_log("B2", "收到新会话查询，开始构建 ContextRequest，并准备联合召回")
        await self._step()
        self.module_log("B1", "对当前查询做向量化，供 Episodic / Semantic 联合召回")
        query_vec = await self.env.embedder.embed_one(request.query)
        await self._step()
        self.module_log("B1", f"查询向量生成完成，dim={len(query_vec)}")
        await self._step()

        epi_hits = await self.env.episodic.recall(request)
        sem_hits = await self.env.semantic.recall(request)
        self.module_log("B2", f"通过 P2-E1/P2-E2 返回候选：Episodic={len(epi_hits)}，Semantic={len(sem_hits)}")
        await self._step()

        self.system_write(
            _make_table(
                ["类型", "ID", "内容", "score"],
                [
                    ["episodic", hit.memory.id[:8], truncate(hit.memory.content, 28), f"{normalize_score(hit.score):.4f}"]
                    for hit in epi_hits[:3]
                ]
                + [
                    ["semantic", hit.memory.id[:8], truncate(hit.memory.content, 28), f"{normalize_score(hit.score):.4f}"]
                    for hit in sem_hits[:2]
                ],
            )
        )
        await self._step()

        if epi_hits:
            top = epi_hits[0].memory
            signal = MemorySignal(
                memory_id=top.id,
                memory_type=top.type,
                session_id=top.session_id,
                agent_id=top.agent_id,
                signal_type=SignalType.ACCESS,
                heat=0.91,
                metadata={"reason": "top_recall_before_pack", "source": top.source.value},
            )
            await self.env.emitter.emit(signal)
            self.module_log("B2", f"候选池中命中高热记忆 {top.id[:8]}，向 B3 发出 ACCESS signal")
            await self._step()
            self.module_log("B3", "读取 ACCESS signal，并结合 Segment/Object 状态判断该批证据是否需要预取或保温")
            await self._step()
            self.module_log("B3", "输出 Keep / Prefetch 建议：保持手册规则在热层，并优先预取上次任务证据")
            await self._step()
            self.module_log("B2", "收到 B3 调度建议后恢复主流程，继续完成 Context Pack 组装与引用排序")
            await self._step()

        pack = await self.env.builder.build(request)
        self.system_write(
            f"[bold]Context Pack[/bold] 入选={len(pack.memories)}  "
            f"tokens={pack.total_tokens}/{pack.budget_tokens}"
        )
        self.system_write(
            _make_table(
                ["#", "层级", "内容", "recall_score"],
                [
                    [
                        str(index + 1),
                        memory.type.value,
                        truncate(memory.content, 34),
                        f"{normalize_score(pack.recall_scores[memory.id]):.4f}",
                    ]
                    for index, memory in enumerate(pack.memories)
                ],
            )
        )
        await self._step()

        self.module_log("B2", "assembled_text 已返回给 Agent，开始生成带引用依据的最终回复")
        await self._step()
        self.user_write(
            "根据你上传的《汛期值班手册》和上次任务记录，西城站一小时雨量达到 58mm，"
            "已经超过“50mm 进入三级响应”的阈值，因此建议三级响应，并对西城片区提前布防。",
            who="agent",
        )

    async def summary(self) -> None:
        self.set_stage("场景总结 · 三个场景的调度路径与动作命中")
        self.system_section("场景总结 · 模块调度与动作命中")

        self.user_write("三个场景已经演示完，下面汇总每个场景主要调度了哪些模块，以及命中了哪些动作。", who="agent")
        await self._step()

        self.system_write(
            _make_table(
                ["场景", "主要调度模块", "命中动作"],
                [
                    [
                        "场景 1\n手册入库",
                        "B1 → B2 → B3 → B2",
                        "B1：文档识别、chunk 切分、embedding 写入 P2-E1\n"
                        "B2：写入 Semantic / Episodic、输出 authoritative memory signal\n"
                        "B3：命中 Pin / Keep\n"
                        "B2：接收调度结果并记录热层优先使用策略",
                    ],
                    [
                        "场景 2\n任务执行归档",
                        "B2 → B1 → B2 → B3 → B2",
                        "B2：写入 Working、迁入 Episodic、输出 PROMOTION_HINT signal\n"
                        "B1：工具结果向量化、归档向量化\n"
                        "B3：命中 Promote\n"
                        "B2：回写高热证据优先级，提高后续 Context Pack 排序",
                    ],
                    [
                        "场景 3\n新会话召回",
                        "B2 → B1 → B2 → B3 → B2",
                        "B2：构建 ContextRequest、联合召回 Episodic / Semantic、输出 ACCESS signal\n"
                        "B1：查询向量化\n"
                        "B3：命中 Keep / Prefetch\n"
                        "B2：恢复主流程，完成 Context Pack 组装与引用排序",
                    ],
                ],
            )
        )
        await self._step()

        self.system_write("\n[bold]整体规律[/bold]")
        self.system_write("1. 场景 1 体现“先语义化，再记忆化，再调度化”的入库链路。")
        self.system_write("2. 场景 2 体现会话内任务如何沉淀为可复用证据，并触发升温。")
        self.system_write("3. 场景 3 体现召回过程中 B2 中途调用 B3，再回到 B2 完成上下文组装。")


# Backward-compatible aliases for older imports.
P2DataflowApp = P3DataflowApp
B2DemoApp = P3DataflowApp
