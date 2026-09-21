from __future__ import annotations

import asyncio
from typing import Any

from examples.minimal_loop.mock_services import (
    DemoEnv,
    DemoObjectVersion,
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
    AccessStats,
    ActionLogEntry,
    ContextRequest,
    EmbeddingRequest,
    Memory,
    MemorySignal,
    MemoryState,
    MemoryType,
    SchedulableObject,
    ScheduleRequest,
    SemanticSignals,
    SignalType,
    SourceType,
    StorageTier,
)

AGENT_ID = "agent-duty-01"
USER_ID = "user-duty-chief"
SESSION_1 = "sess-duty-001"
SESSION_2 = "sess-duty-002"
MANUAL_SOURCE_ID = "doc-flood-manual"
MANUAL_OBJECT_V3 = "object-flood-manual-v3"
MANUAL_OBJECT_V4 = "object-flood-manual-v4"


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
        self.completed_scenarios: list[str] = []
        self.filtered_object_ids: list[str] = []

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

    async def schedule_memory(
        self,
        memory: Memory,
        *,
        access_frequency: float,
        recency_score: float,
        hit_rate: float,
        semantic_relevance: float,
        task_relevance: float,
        business_priority: float,
        pinned: bool = False,
    ) -> ActionLogEntry:
        current_tier = memory.p2_ref.tier if memory.p2_ref else StorageTier.L2_HDD
        obj = SchedulableObject(
            object_id=memory.object_id or memory.id,
            object_type=memory.type.value,
            current_tier=current_tier,
            tenant_id=memory.tenant_id,
            access=AccessStats(
                access_frequency=access_frequency,
                recency_score=recency_score,
                hit_rate=hit_rate,
                access_count=memory.access_count,
                last_access_time=memory.last_accessed_at,
            ),
            semantic=SemanticSignals(
                semantic_relevance=semantic_relevance,
                importance=memory.importance,
                task_relevance=task_relevance,
            ),
            business_priority=business_priority,
            pinned=pinned,
        )
        result = await self.env.b3_scheduler.run_once(
            ScheduleRequest(objects=[obj], trace_id=memory.trace_id or memory.id)
        )
        return result.entries[0]

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
            await self.wait_for_enter("按 Enter 进入“用户查询 → Context Pack 构建”")
            self.system_log().clear()
            await self.scenario_2()
            await self.wait_for_enter("按 Enter 进入“记忆写入 → 归档与调度”")
            self.system_log().clear()
            await self.scenario_3()
            await self.wait_for_enter("按 Enter 进入“对象更新 → 版本状态同步”")
            self.system_log().clear()
            await self.scenario_4()
            await self.wait_for_enter("按 Enter 查看“四个场景总结”")
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

        self.user_write(
            "我刚上传了《汛期值班手册》，后面问到暴雨响应请按这份手册回答。", who="user"
        )
        await self._step()
        self.user_write("已接收手册，我先解析条款并纳入长期知识。", who="agent")
        await self._step()

        chunks = [
            "一小时雨量达到 50mm 时，建议进入三级响应。",
            "连续两小时雨量持续升高时，需要提前通知西城和北河片区值守。",
            "用户显式指定的值班手册属于权威依据，后续回答应优先引用。",
        ]

        raw_ref = await self.env.storage.put(
            "objects/flood-manual/v3.txt",
            "\n".join(chunks).encode("utf-8"),
        )
        object_version = DemoObjectVersion(
            object_id=MANUAL_OBJECT_V3,
            source_id=MANUAL_SOURCE_ID,
            version_id="v3",
            raw_ref=raw_ref.object_key,
            trace_id="trace-manual-upload-v3",
        )
        self.env.object_versions[MANUAL_OBJECT_V3] = object_version
        self.module_log(
            "B2",
            f"P2-E2 保存原始对象 object_id={MANUAL_OBJECT_V3} / version=v3 / "
            f"raw_ref={raw_ref.object_key}",
        )
        await self._step()

        self.module_log("B1", f"接收 source_id={MANUAL_SOURCE_ID}，识别为需语义化的权威文档")
        await self._step()

        chunk_rows: list[list[str]] = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_id = f"chunk-manual-{index:03d}"
            embedding_result = await self.env.b1_pipeline.process(
                EmbeddingRequest(
                    text=chunk,
                    source_type=SourceType.DOCUMENT,
                    source_id=MANUAL_SOURCE_ID,
                    object_id=MANUAL_OBJECT_V3,
                    trace_id=object_version.trace_id,
                    metadata={"chunk_id": chunk_id, "version_id": "v3"},
                )
            )
            if not embedding_result.records:
                raise RuntimeError(embedding_result.error_message or "B1 embedding failed")
            vector = embedding_result.records[0].vector
            vector_id = embedding_result.records[0].chunk_id
            ref = await self.env.storage.put(f"manual/{chunk_id}", chunk.encode("utf-8"))
            chunk_rows.append([chunk_id, truncate(chunk, 24), len(vector), ref.object_key])
            object_version.chunk_ids.append(chunk_id)
            object_version.vector_ids.append(vector_id)
            self.module_log(
                "B1",
                f"切分 {chunk_id}，绑定 source_id/object_key，"
                f"生成 embedding dim={len(vector)}，写入 P2-E1 / {ref.object_key}",
            )
            await self._step()

        self.system_write(_make_table(["chunk_id", "内容", "dim", "P2 object_key"], chunk_rows))
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
                source_id=MANUAL_SOURCE_ID,
                object_id=MANUAL_OBJECT_V3,
                trace_id=object_version.trace_id,
                tags=["manual", "rule"],
                metadata={
                    "source_id": MANUAL_SOURCE_ID,
                    "version_id": "v3",
                    "raw_ref": raw_ref.object_key,
                },
            )
            await self.env.semantic.write(mem)
            semantic_items.append(mem)
            self.module_log(
                "B2",
                f"接收 B1 返回的 chunk/source/embedding_ref，沉淀 Semantic Memory id={mem.id[:8]}",
            )
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
        self.module_log(
            "B2", f"写入 Episodic Memory id={upload_event.id[:8]}，保留上传事件轨迹与来源追溯"
        )
        await self._step()

        self.module_log(
            "B2",
            "输出 memory signal：authoritative=true / user_defined=true / source_type=document",
        )
        await self._step()
        entry = await self.schedule_memory(
            semantic_items[0],
            access_frequency=0.9,
            recency_score=1.0,
            hit_rate=0.9,
            semantic_relevance=1.0,
            task_relevance=0.9,
            business_priority=1.0,
            pinned=True,
        )
        self.module_log(
            "B3",
            f"V1 Heuristic 计算 score={entry.action.score:.3f}，"
            f"policy={entry.action.policy_version}",
        )
        await self._step()
        self.module_log(
            "B3",
            f"输出 {entry.action.action_type.value.upper()} 建议：{entry.action.expected_effect}",
        )
        await self._step()
        self.module_log(
            "B2",
            f"接收执行反馈 status={entry.feedback.execute_status.value}，写入 action_log",
        )
        await self._step()

        self.system_write("\n[bold]当前 Semantic Memory[/bold]")
        self.system_write(
            _make_table(
                ["ID", "层级", "状态", "内容", "来源", "访问次数", "过期时间"],
                memory_table_rows(semantic_items),
            )
        )
        self.completed_scenarios.append("document_upload")

    async def scenario_3(self) -> None:
        self.set_stage("场景 3 · 记忆写入 → B2 归档 → B3 升温")
        self.system_section("场景 3 · 对话、工具、归档与长期记忆维护")

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
        tool_ref = await self.env.storage.put(
            "tool/west-city-rainfall", tool_summary.encode("utf-8")
        )
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
        self.module_log(
            "B2", f"接收 B1 的 embedding 结果，归档为 Episodic Memory id={tool_memory.id[:8]}"
        )
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
            self.module_log(
                "B2", f"会话阶段结束，准备把 Working Memory {item.id[:8]} 迁入 Episodic"
            )
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
        entry = await self.schedule_memory(
            tool_memory,
            access_frequency=0.95,
            recency_score=0.95,
            hit_rate=0.9,
            semantic_relevance=0.85,
            task_relevance=0.8,
            business_priority=0.9,
        )
        self.module_log(
            "B3",
            f"V1 Heuristic 计算 score={entry.action.score:.3f}，reason={entry.action.reason}",
        )
        await self._step()
        self.module_log(
            "B3",
            f"输出 {entry.action.action_type.value.upper()} → "
            f"{entry.action.target_tier.value if entry.action.target_tier else '—'}",
        )
        await self._step()
        self.module_log(
            "B2",
            f"执行反馈 status={entry.feedback.execute_status.value}，"
            f"new_tier={entry.feedback.new_tier.value if entry.feedback.new_tier else '—'}",
        )
        await self._step()

        self.system_write("\n[bold]当前 Episodic Memory[/bold]")
        self.system_write(
            _make_table(
                ["ID", "状态", "内容", "Embedding", "P2 object_key", "存储层"],
                episodic_detail_rows(archived),
            )
        )
        self.completed_scenarios.append("memory_maintenance")

    async def scenario_2(self) -> None:
        self.set_stage("场景 2 · 用户查询 → B2 Context Pack → Agent 应答")
        self.system_section("场景 2 · 查询向量化、联合召回与证据组装")

        self.user_write("西城站一小时雨量达到 58mm 时应进入什么响应？请给出手册依据。", who="user")
        await self._step()

        request = ContextRequest(
            session_id=SESSION_2,
            agent_id=AGENT_ID,
            user_id=USER_ID,
            query="西城站一小时雨量达到 58mm 时应进入什么响应，请给出手册依据",
            memory_types=[MemoryType.EPISODIC, MemoryType.SEMANTIC],
            max_tokens=256,
            max_candidates=8,
        )

        self.module_log("B2", "收到用户查询，开始构建 ContextRequest，并准备联合召回")
        await self._step()
        self.module_log("B1", "对当前查询做向量化，供 Episodic / Semantic 联合召回")
        query_vec = await self.env.embedder.embed_one(request.query)
        await self._step()
        self.module_log("B1", f"查询向量生成完成，dim={len(query_vec)}")
        await self._step()

        epi_hits = await self.env.episodic.recall(request)
        sem_hits = await self.env.semantic.recall(request)
        self.module_log(
            "B2", f"通过 P2-E1/P2-E2 返回候选：Episodic={len(epi_hits)}，Semantic={len(sem_hits)}"
        )
        await self._step()

        self.system_write(
            _make_table(
                ["类型", "ID", "内容", "score"],
                [
                    [
                        "episodic",
                        hit.memory.id[:8],
                        truncate(hit.memory.content, 28),
                        f"{normalize_score(hit.score):.4f}",
                    ]
                    for hit in epi_hits[:3]
                ]
                + [
                    [
                        "semantic",
                        hit.memory.id[:8],
                        truncate(hit.memory.content, 28),
                        f"{normalize_score(hit.score):.4f}",
                    ]
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
            entry = await self.schedule_memory(
                top,
                access_frequency=0.8,
                recency_score=0.9,
                hit_rate=0.8,
                semantic_relevance=0.9,
                task_relevance=0.95,
                business_priority=0.8,
            )
            self.module_log(
                "B3",
                f"读取 ACCESS signal，V1 Heuristic score={entry.action.score:.3f}",
            )
            await self._step()
            self.module_log(
                "B3",
                f"输出 {entry.action.action_type.value.upper()} 建议，"
                f"target={entry.action.target_tier.value if entry.action.target_tier else '—'}",
            )
            await self._step()
            self.module_log(
                "B2", "收到 B3 调度建议后恢复主流程，继续完成 Context Pack 组装与引用排序"
            )
            await self._step()

        pack = await self.env.b2_service.before_inference(request)
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
            "根据你上传的《汛期值班手册》v3，西城站一小时雨量达到 58mm，"
            "已经超过“50mm 进入三级响应”的阈值，因此建议进入三级响应。",
            who="agent",
        )
        self.completed_scenarios.append("user_query")

    async def scenario_4(self) -> None:
        self.set_stage("场景 4 · 对象更新 → 版本同步 → 旧数据隔离")
        self.system_section("场景 4 · 对象更新、向量替换与状态传播")

        self.user_write(
            "《汛期值班手册》发布了 v4：三级响应阈值改为一小时 55mm。"
            "请替换旧版本，后续不能再引用 v3。",
            who="user",
        )
        await self._step()

        old_object = self.env.object_versions.get(MANUAL_OBJECT_V3)
        if old_object is None:
            raise RuntimeError("scenario 4 requires the v3 object created by scenario 1")

        v4_chunks = [
            "一小时雨量达到 55mm 时，建议进入三级响应。",
            "值班响应必须引用当前 active 版本手册，superseded 版本不得进入 Context Pack。",
        ]
        v4_trace_id = "trace-manual-update-v4"
        new_raw_ref = await self.env.storage.put(
            "objects/flood-manual/v4.txt",
            "\n".join(v4_chunks).encode("utf-8"),
        )
        new_object = DemoObjectVersion(
            object_id=MANUAL_OBJECT_V4,
            source_id=MANUAL_SOURCE_ID,
            version_id="v4",
            raw_ref=new_raw_ref.object_key,
            supersedes=MANUAL_OBJECT_V3,
            trace_id=v4_trace_id,
        )
        self.env.object_versions[MANUAL_OBJECT_V4] = new_object
        old_object.status = "superseded"
        self.module_log(
            "B2",
            f"P2-E2 写入 object_id={MANUAL_OBJECT_V4} / version=v4，并将 "
            f"{MANUAL_OBJECT_V3} 标记为 superseded",
        )
        await self._step()

        old_memories = await self.env.semantic.query(
            agent_id=AGENT_ID,
            user_id=USER_ID,
            state=MemoryState.ACTIVE,
            tags=["manual"],
        )
        superseded_count = 0
        for memory in old_memories:
            if memory.object_id != MANUAL_OBJECT_V3:
                continue
            await self.env.semantic.update_state(memory.id, MemoryState.SUPERSEDED)
            superseded_count += 1
        removed_vectors = await self.env.vector_sink.delete_by_object_id(MANUAL_OBJECT_V3)
        self.module_log(
            "B2",
            f"状态传播到 B2/P2-E1：superseded memories={superseded_count}，"
            f"旧 vector records removed={removed_vectors}",
        )
        await self._step()

        new_memories: list[Memory] = []
        for index, chunk in enumerate(v4_chunks, start=1):
            chunk_id = f"chunk-manual-v4-{index:03d}"
            embedding_result = await self.env.b1_pipeline.process(
                EmbeddingRequest(
                    text=chunk,
                    source_type=SourceType.DOCUMENT,
                    source_id=MANUAL_SOURCE_ID,
                    object_id=MANUAL_OBJECT_V4,
                    trace_id=v4_trace_id,
                    metadata={"chunk_id": chunk_id, "version_id": "v4"},
                )
            )
            if not embedding_result.records:
                raise RuntimeError(embedding_result.error_message or "v4 embedding failed")
            record = embedding_result.records[0]
            new_object.chunk_ids.append(chunk_id)
            new_object.vector_ids.append(record.chunk_id)
            memory = Memory(
                type=MemoryType.SEMANTIC,
                session_id=SESSION_2,
                agent_id=AGENT_ID,
                user_id=USER_ID,
                content=chunk,
                source=SourceType.DISTILLED,
                source_id=MANUAL_SOURCE_ID,
                object_id=MANUAL_OBJECT_V4,
                trace_id=v4_trace_id,
                tags=["manual", "rule", "active-version"],
                metadata={
                    "version_id": "v4",
                    "raw_ref": new_raw_ref.object_key,
                    "supersedes": MANUAL_OBJECT_V3,
                },
                p2_ref=new_raw_ref,
            )
            await self.env.semantic.write(memory)
            new_memories.append(memory)
            self.module_log(
                "B1",
                f"v4 chunk={chunk_id} 完成 embedding，绑定 object/vector/source/trace 映射",
            )
            await self._step()

        self.module_log(
            "B3",
            "构建调度候选时过滤 status=superseded 的 v3 对象；旧版本不进入 Promote/Prefetch",
        )
        old_action_count = sum(
            entry.action.object_id == MANUAL_OBJECT_V3
            for entry in self.env.b3_scheduler.action_log.entries
        )
        self.filtered_object_ids.append(MANUAL_OBJECT_V3)
        await self._step()
        entry = await self.schedule_memory(
            new_memories[0],
            access_frequency=0.8,
            recency_score=1.0,
            hit_rate=0.8,
            semantic_relevance=1.0,
            task_relevance=0.95,
            business_priority=1.0,
            pinned=True,
        )
        self.module_log(
            "B3",
            f"仅对 active v4 输出 {entry.action.action_type.value.upper()}，"
            f"status={entry.feedback.execute_status.value}，action_id={entry.action.action_id[:8]}",
        )
        current_old_action_count = sum(
            action_entry.action.object_id == MANUAL_OBJECT_V3
            for action_entry in self.env.b3_scheduler.action_log.entries
        )
        if current_old_action_count != old_action_count:
            raise RuntimeError("superseded v3 object was scheduled after status propagation")
        await self._step()

        verify_request = ContextRequest(
            session_id="sess-version-check-001",
            agent_id=AGENT_ID,
            user_id=USER_ID,
            query="最新手册规定的三级响应雨量阈值是多少",
            memory_types=[MemoryType.SEMANTIC],
            max_tokens=128,
            max_candidates=6,
            trace_id=v4_trace_id,
        )
        pack = await self.env.b2_service.before_inference(verify_request)
        if any(memory.object_id == MANUAL_OBJECT_V3 for memory in pack.memories):
            raise RuntimeError("superseded v3 memory leaked into Context Pack")

        self.system_write(
            _make_table(
                ["object_id", "version", "status", "chunks", "vectors", "raw_ref"],
                [
                    [
                        old_object.object_id,
                        old_object.version_id,
                        old_object.status,
                        str(len(old_object.chunk_ids)),
                        str(len(old_object.vector_ids)),
                        old_object.raw_ref,
                    ],
                    [
                        new_object.object_id,
                        new_object.version_id,
                        new_object.status,
                        str(len(new_object.chunk_ids)),
                        str(len(new_object.vector_ids)),
                        new_object.raw_ref,
                    ],
                ],
                title="对象版本与状态同步结果",
            )
        )
        self.system_write(
            f"[bold green]隔离验证通过[/bold green]：Context Pack 仅包含 active v4，"
            f"memory_refs={len(pack.memory_refs)}，trace_id={pack.trace_id}"
        )
        await self._step()
        self.user_write(
            "更新完成。v3 已标记为 superseded，旧向量和旧记忆不会再进入召回或调度；"
            "当前三级响应阈值按 v4 执行，为一小时 55mm。",
            who="agent",
        )
        self.completed_scenarios.append("object_update")

    async def summary(self) -> None:
        self.set_stage("场景总结 · 四个典型应用场景的数据流与动作命中")
        self.system_section("场景总结 · 模块调度与动作命中")

        self.user_write(
            "四个典型应用场景已经演示完，下面按文档基准汇总每条数据流和命中动作。",
            who="agent",
        )
        await self._step()

        self.system_write(
            _make_table(
                ["场景", "主要调度模块", "命中动作"],
                [
                    [
                        "场景 1\n文档上传",
                        "P4 → P2-E2 → B1 → P2-E1 → B2 → B3",
                        "P2-E2：保存原文对象和版本元数据\n"
                        "B1：chunk 切分、embedding、object/chunk/vector 映射\n"
                        "B2：写入 Semantic / Episodic 与来源引用\n"
                        "B3：命中 Pin / Keep\n"
                        "结果：形成可追溯、可召回、可调度的文档资源",
                    ],
                    [
                        "场景 2\n用户查询",
                        "B2 → B1 → B2 → B3 → B2",
                        "B1：查询向量化\n"
                        "B2：联合召回、身份过滤、Context Pack 与证据引用\n"
                        "B3：命中 Keep / Prefetch\n"
                        "结果：Agent 获得受 token budget 约束的可追溯上下文",
                    ],
                    [
                        "场景 3\n记忆维护",
                        "B2 → B1 → B2 → B3 → B2",
                        "B2：对话/工具写入 Working，阶段结束归档 Episodic\n"
                        "B1：工具结果与归档记忆向量化\n"
                        "B3：命中 Promote\n"
                        "结果：任务过程沉淀为跨会话可复用证据",
                    ],
                    [
                        "场景 4\n对象更新",
                        "P4 → P2-E2/E1 → B1 → B2 → B3",
                        "P2-E2：写入 v4，并将 v3 标记 superseded\n"
                        "B1/P2-E1：替换旧向量并建立新版本映射\n"
                        "B2：旧记忆退出召回；Context Pack 仅返回 active v4\n"
                        "B3：旧对象不进入 Promote/Prefetch，仅保护 active v4",
                    ],
                ],
            )
        )
        await self._step()

        self.system_write("\n[bold]整体规律[/bold]")
        self.system_write("1. 文档上传负责建立 object → chunk → vector → memory 的来源链。")
        self.system_write("2. 用户查询负责把向量命中解析为安全、可追溯的 Context Pack。")
        self.system_write("3. 记忆维护负责 Working → Episodic/Semantic 生命周期和调度信号。")
        self.system_write("4. 对象更新负责版本、状态和删除隔离，防止旧数据继续召回或调度。")


# Backward-compatible aliases for older imports.
P2DataflowApp = P3DataflowApp
B2DemoApp = P3DataflowApp
