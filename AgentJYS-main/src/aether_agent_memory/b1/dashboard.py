"""Textual real-time dashboard for the B1 Sidecar."""

from __future__ import annotations

import argparse
import asyncio
import time

import httpx
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Static


def _flag(value: bool) -> str:
    return "YES" if value else "no"


class B1Dashboard(App[None]):
    TITLE = "Aether P3-B1 Sidecar - real-time process"
    CSS = """
    Screen { layout: vertical; }
    #top { height: 8; }
    #health, #backend { width: 1fr; border: solid $primary; padding: 0 1; }
    #flow { height: 5; border: solid $accent; padding: 0 1; }
    #middle { height: 12; }
    #metrics, #simd { width: 1fr; border: solid $primary; padding: 0 1; }
    #events { height: 1fr; border: solid $accent; padding: 0 1; overflow-y: auto; }
    """

    def __init__(self, base_url: str, refresh_seconds: float) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.refresh_seconds = refresh_seconds
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=5.0)
        self._refresh_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="top"):
            yield Static("Waiting for Sidecar...", id="health", markup=False)
            yield Static("Waiting for backend details...", id="backend", markup=False)
        yield Static(
            "INTERCEPT -> VALIDATE -> CHUNK -> CPU EMBED -> L2 NORMALIZE -> RETURN VECTOR",
            id="flow",
            markup=False,
        )
        with Horizontal(id="middle"):
            yield Static("Waiting for metrics...", id="metrics", markup=False)
            yield Static("Waiting for CPU capabilities...", id="simd", markup=False)
        with Vertical():
            yield Static("Waiting for process events...", id="events", markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_data()
        self.set_interval(self.refresh_seconds, self.refresh_data)

    async def on_unmount(self) -> None:
        await self.client.aclose()

    async def refresh_data(self) -> None:
        if self._refresh_lock.locked():
            return
        async with self._refresh_lock:
            try:
                (
                    health_response,
                    metrics_response,
                    capabilities_response,
                    events_response,
                ) = await asyncio.gather(
                    self.client.get("/health/ready"),
                    self.client.get("/metrics"),
                    self.client.get("/v1/capabilities"),
                    self.client.get("/v1/events", params={"limit": 12}),
                )
                health = health_response.json()
                metrics = metrics_response.json()
                capabilities = capabilities_response.json()
                events = events_response.json().get("items", [])
            except Exception as exc:
                self.query_one("#health", Static).update(
                    f"OFFLINE\nURL: {self.base_url}\n{type(exc).__name__}: {exc}\n"
                    f"Last check: {time.strftime('%H:%M:%S')}"
                )
                return

            status = health.get("status", "unknown")
            self.query_one("#health", Static).update(
                f"STATUS: {status.upper()}\n"
                f"URL: {self.base_url}\n"
                f"Threads: {health.get('threads')}\n"
                f"Uptime: {metrics.get('uptime_seconds', 0):.1f}s\n"
                f"Last check: {time.strftime('%H:%M:%S')}"
            )
            runtime = capabilities.get("current_backend", {})
            self.query_one("#backend", Static).update(
                f"Backend: {health.get('backend')}\n"
                f"Requested: {health.get('requested_backend')} / "
                f"fallback={health.get('fallback_used')}\n"
                f"Engine: {health.get('engine')}\n"
                f"Model: {health.get('model')}\n"
                f"Provider: {runtime.get('provider', health.get('provider'))}\n"
                f"Dimension / dtype: {health.get('dimension')} / {health.get('precision')}"
            )
            self.query_one("#metrics", Static).update(
                f"Requests / items: {metrics.get('requests', 0)} / {metrics.get('items', 0)}\n"
                f"Success / skip / fail: {metrics.get('success', 0)} / "
                f"{metrics.get('skipped', 0)} / {metrics.get('failed', 0)}\n"
                f"RPS / items/s: {metrics.get('requests_per_second', 0):.3f} / "
                f"{metrics.get('items_per_second', 0):.3f}\n"
                f"P50 / P95 / P99 ms: {metrics.get('request_latency_p50_ms', 0):.2f} / "
                f"{metrics.get('request_latency_p95_ms', 0):.2f} / "
                f"{metrics.get('request_latency_p99_ms', 0):.2f}\n"
                f"Chars / chunks / vectors: {metrics.get('total_input_chars', 0)} / "
                f"{metrics.get('total_chunks', 0)} / {metrics.get('total_vectors', 0)}\n"
                f"Queue / backend timeouts: {metrics.get('queue_timeouts', 0)} / "
                f"{metrics.get('backend_timeouts', 0)}\n"
                f"Errors: {metrics.get('error_codes', {})}"
            )
            cpu = capabilities.get("cpu_runtime", {})
            features = cpu.get("simd_features", {})
            strategies = capabilities.get("backend_strategy", {}).get("strategies", [])
            strategy_text = ", ".join(
                f"{value.get('key')}={value.get('implementation_status')}" for value in strategies
            )
            self.query_one("#simd", Static).update(
                f"CPU: {cpu.get('cpu', 'unknown')}\n"
                f"Detected tier: {cpu.get('highest_detected_simd_tier')}\n"
                f"SSE2 {_flag(features.get('SSE2', False))} | "
                f"AVX2 {_flag(features.get('AVX2', False))} | "
                f"AVX-512 {_flag(features.get('AVX512F', False))}\n"
                f"AMX TILE/INT8/BF16: {_flag(features.get('AMX_TILE', False))} / "
                f"{_flag(features.get('AMX_INT8', False))} / "
                f"{_flag(features.get('AMX_BF16', False))}\n"
                f"Runtime policy: {cpu.get('active_simd_policy')}\n"
                f"Backend strategies: {strategy_text}"
            )
            event_lines = ["SEQ  STATUS   ID                 FLOW / ERROR"]
            for event in events:
                flow = " -> ".join(event.get("stages", []))
                if event.get("error_code"):
                    flow += f" [{event['error_code']}]"
                event_lines.append(
                    f"{event.get('sequence', 0):>3}  {str(event.get('status', '')):<8} "
                    f"{str(event.get('request_id', ''))[:18]:<18} {flow}"
                )
            self.query_one("#events", Static).update("\n".join(event_lines))


def main() -> None:
    parser = argparse.ArgumentParser(description="Open the B1 real-time textual dashboard.")
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    parser.add_argument("--refresh-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if args.refresh_seconds <= 0:
        parser.error("--refresh-seconds must be positive")
    B1Dashboard(args.base_url, args.refresh_seconds).run()


if __name__ == "__main__":
    main()
