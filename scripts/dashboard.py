"""Browser dashboard for the unified P2/P3 integration demo."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import sys

from aether_agent_memory.p2 import P2GrpcClient
from dashboard_page import DASHBOARD_HTML


STATE: dict[str, object] = {
    "last_run": None,
    "last_output": "尚未从浏览器发起验证。",
    "last_status": "idle",
    "last_report": None,
}


async def p2_reachable() -> bool:
    client = P2GrpcClient(os.getenv("AETHER_P2_GRPC", "engine:50052"))
    try:
        await client.ensure_collection(32)
    except Exception:
        return False
    finally:
        await client.close()
    return True


async def run_smoke() -> None:
    STATE["last_status"] = "running"
    try:
        script = Path(__file__).resolve().parents[1] / "examples" / "p2_integration_smoke.py"
        spec = spec_from_file_location("p2_integration_smoke", script)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载联调验证脚本")
        module = module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        report = await module.run()
    except Exception as exc:
        STATE["last_status"] = "failed"
        STATE["last_output"] = f"{type(exc).__name__}: {exc}"
        STATE["last_report"] = None
    else:
        STATE["last_status"] = "passed"
        STATE["last_output"] = report["summary"]
        STATE["last_report"] = report
    STATE["last_run"] = datetime.now(UTC).isoformat()


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", DASHBOARD_HTML.encode())
            return
        if self.path == "/api/status":
            payload = {
                "p2_online": asyncio.run(p2_reachable()),
                "p2_endpoint": os.getenv("AETHER_P2_GRPC", "engine:50052"),
                **STATE,
            }
            self._send(HTTPStatus.OK, "application/json", json.dumps(payload).encode())
            return
        self._send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/run-smoke":
            self._send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")
            return
        asyncio.run(run_smoke())
        self._send(HTTPStatus.OK, "application/json", json.dumps(STATE).encode())

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


LEGACY_DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Aether 融合联调仪表盘</title>
<style>
:root{color-scheme:dark;--bg:#101516;--panel:#182021;--line:#334244;--text:#e8f0ef;--muted:#9eafad;--teal:#4bc0ae;--amber:#e6b85c;--red:#ed786b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Arial,sans-serif}header{height:64px;display:flex;align-items:center;padding:0 32px;border-bottom:1px solid var(--line);background:#151c1d}h1{margin:0;font-size:20px;font-weight:600}.sub{color:var(--muted);margin-left:16px}main{max-width:1160px;margin:32px auto;padding:0 24px}.toolbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}h2{font-size:16px;margin:0;font-weight:600}button{border:1px solid var(--teal);background:var(--teal);color:#08201d;font-weight:700;padding:9px 14px;border-radius:4px;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.card{border:1px solid var(--line);background:var(--panel);padding:18px;min-height:130px;border-radius:4px}.label{color:var(--muted);text-transform:uppercase;font-size:11px;letter-spacing:.08em}.value{font-size:24px;margin-top:14px;font-weight:600}.ok{color:var(--teal)}.warn{color:var(--amber)}.bad{color:var(--red)}.flow{margin-top:18px;border:1px solid var(--line);border-radius:4px;padding:18px;background:var(--panel)}.steps{display:flex;gap:10px;align-items:center;margin-top:14px;flex-wrap:wrap}.step{border:1px solid var(--line);padding:8px 12px;border-radius:4px;background:#12191a}.arrow{color:var(--muted)}pre{margin:18px 0 0;padding:14px;min-height:92px;white-space:pre-wrap;background:#0b1011;border:1px solid var(--line);color:#c8d6d4;border-radius:4px}@media(max-width:720px){header{padding:0 18px}.sub{display:none}main{margin:20px auto;padding:0 16px}.grid{grid-template-columns:1fr}.toolbar{align-items:flex-start;gap:12px;flex-direction:column}}
</style></head><body><header><h1>Aether 融合联调仪表盘</h1><span class="sub">P2 存储引擎与 P3 语义调度</span></header><main><div class="toolbar"><h2>运行概览</h2><button id="run">执行融合验证</button></div><section class="grid"><div class="card"><div class="label">P2 gRPC</div><div class="value" id="p2">正在检查</div><div id="endpoint" class="label"></div></div><div class="card"><div class="label">最近验证</div><div class="value" id="result">未执行</div><div id="time" class="label"></div></div><div class="card"><div class="label">验证范围</div><div class="value ok">B1 / B2 / B3</div><div class="label">P2 对象与向量服务</div></div></section><section class="flow"><div class="label">融合链路</div><div class="steps"><span class="step">B1 向量化</span><span class="arrow">-&gt;</span><span class="step">P2 对象与向量</span><span class="arrow">-&gt;</span><span class="step">B2 上下文包</span><span class="arrow">-&gt;</span><span class="step">B3 调度决策</span></div><pre id="output">正在加载状态...</pre></section></main><script>
const p2=document.querySelector('#p2'),result=document.querySelector('#result'),output=document.querySelector('#output'),time=document.querySelector('#time'),endpoint=document.querySelector('#endpoint'),run=document.querySelector('#run'),labels={idle:'未执行',running:'执行中',passed:'通过',failed:'失败'};function render(s){p2.textContent=s.p2_online?'在线':'不可用';p2.className='value '+(s.p2_online?'ok':'bad');endpoint.textContent=s.p2_endpoint;result.textContent=labels[s.last_status]||'未执行';result.className='value '+(s.last_status==='passed'?'ok':s.last_status==='failed'?'bad':'warn');time.textContent=s.last_run?new Date(s.last_run).toLocaleString():'尚未执行验证';output.textContent=s.last_output||''}async function refresh(){const r=await fetch('/api/status');render(await r.json())}run.onclick=async()=>{run.disabled=true;run.textContent='正在执行验证';const r=await fetch('/api/run-smoke',{method:'POST'});render(await r.json());run.disabled=false;run.textContent='执行融合验证'};refresh();setInterval(refresh,5000);
</script></body></html>"""


if __name__ == "__main__":
    port = int(os.getenv("AETHER_DASHBOARD_PORT", "8081"))
    ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler).serve_forever()
