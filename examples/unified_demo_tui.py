"""Textual presentation of the unified Aether project flow."""

from __future__ import annotations

import asyncio
import sys
from importlib.util import find_spec
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

if find_spec("textual") is None:
    raise SystemExit("Install the demo group first: uv sync --group demo")

from rich.panel import Panel  # noqa: E402
from rich.text import Text  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.containers import Horizontal, Vertical  # noqa: E402
from textual.widgets import Footer, Header, RichLog, Static  # noqa: E402

from aether_agent_memory.demo import FlowEvent, UnifiedDemoRunner  # noqa: E402


class UnifiedDemoApp(App[None]):
    TITLE = "Aether Unified Demo"
    SUB_TITLE = "P4 -> P2 -> P3 B1/B2/B3 -> P1/P2"

    CSS = """
    Screen { layout: vertical; }
    #main { height: 1fr; }
    #request, #flow { border: round $primary; padding: 0 1; }
    #request { width: 4fr; }
    #flow { width: 8fr; }
    RichLog { height: 1fr; }
    #status { height: 3; padding: 0 2; background: $panel; content-align: center middle; }
    """

    def __init__(self, step_delay: float = 0.8) -> None:
        super().__init__()
        self.step_delay = step_delay

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="request"):
                yield RichLog(id="request-log", wrap=True, markup=True)
            with Vertical(id="flow"):
                yield RichLog(id="flow-log", wrap=True, markup=True)
        yield Static("Preparing unified demo...", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#request", Vertical).border_title = "P4 / Agent request"
        self.query_one("#flow", Vertical).border_title = "Internal project flow"
        self.query_one("#request-log", RichLog).write(
            Panel(Text("Store a document and make it available to an Agent."), title="Incoming request")
        )
        self.run_worker(self.run_demo, exclusive=True)

    async def run_demo(self) -> None:
        report = await UnifiedDemoRunner().run()
        flow_log = self.query_one("#flow-log", RichLog)
        status = self.query_one("#status", Static)
        for event in report.events:
            status.update(f"[{event.sequence}/{len(report.events)}] {event.node}: {event.action}")
            flow_log.write(self.event_panel(event))
            await asyncio.sleep(self.step_delay)
        status.update("Unified flow complete. Press q or Ctrl+C to exit.")

    @staticmethod
    def event_panel(event: FlowEvent) -> Panel:
        color = "green" if event.status == "success" else "yellow" if event.status == "skipped" else "red"
        body = Text(f"{event.action}\n{event.detail}")
        return Panel(body, title=f"[{color}]{event.node} | {event.status.upper()}[/{color}]", border_style=color)


if __name__ == "__main__":
    UnifiedDemoApp().run()
