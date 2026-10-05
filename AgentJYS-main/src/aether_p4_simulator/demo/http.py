"""Independent, loopback-only demo HTTP boundary; never use legacy error handlers."""

import ipaddress
import json
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

from pydantic import ValidationError as ModelError

from aether_p4_simulator.validation.errors import ValidationError

from .models import StartRequest
from .service import DemoService

ORIGINS = frozenset({"http://127.0.0.1:5173", "http://localhost:5173"})
PREFIX = "/api/v1/demo/"


def configured_origins(raw: str | None) -> frozenset[str]:
    """Accept exact local browser origins without changing the loopback trust boundary."""
    if raw is None:
        return ORIGINS
    try:
        if len(raw) > 4096:
            raise ValueError
        values = json.loads(raw)
        if not isinstance(values, list) or not 1 <= len(values) <= 16:
            raise ValueError
        for value in values:
            if not isinstance(value, str) or any(c.isspace() for c in value):
                raise ValueError
            url = urlsplit(value)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.path
                or "?" in value
                or "#" in value
                or url.username is not None
                or url.password is not None
                or url.netloc.endswith(":")
                or (url.port is not None and url.port == 0)
            ):
                raise ValueError
            if url.hostname != "localhost" and not ipaddress.ip_address(url.hostname).is_loopback:
                raise ValueError
        return frozenset(values)
    except (ValueError, TypeError):
        raise ValueError("demo requires a non-empty list of exact loopback origins") from None


def _send_demo(handler: BaseHTTPRequestHandler, status: int, payload: Mapping[str, object]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else b""
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Vary", "Origin")
    handler.send_header("Connection", "close")
    origins = handler.headers.get_all("Origin", [])
    if len(origins) == 1 and origins[0] in getattr(handler.server, "demo_origins", ORIGINS):
        handler.send_header("Access-Control-Allow-Origin", origins[0])
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)
    handler.close_connection = True


def _guard(handler: BaseHTTPRequestHandler) -> bytes:
    if not ipaddress.ip_address(handler.client_address[0]).is_loopback:
        raise ValidationError(403, "local_only", "演示仅允许本机访问")
    lengths = handler.headers.get_all("Content-Length", [])
    if handler.headers.get_all("Transfer-Encoding") or len(lengths) > 1:
        raise ValidationError(400, "invalid_framing", "不支持的请求长度或编码")
    if lengths and (not lengths[0].isascii() or not lengths[0].isdigit()):
        raise ValidationError(400, "invalid_framing", "请求长度不合法")
    if lengths and (len(lengths[0]) > 6 or int(lengths[0]) > 4096):
        raise ValidationError(413, "body_too_large", "请求超过 4096 字节")
    # Consume only a bounded, well-framed body before rejecting Host/Origin.
    # Closing with unread request data can reset TCP and discard the JSON error.
    length = int(lengths[0]) if lengths else 0
    handler.connection.settimeout(5)
    try:
        body = handler.rfile.read(length)
    except TimeoutError:
        raise ValidationError(408, "body_timeout", "读取请求超时；未启动演示") from None
    if len(body) != length:
        raise ValidationError(400, "incomplete_body", "请求正文不完整")
    hosts = handler.headers.get_all("Host", [])
    assert isinstance(handler.server, HTTPServer)
    allowed = {
        f"{host}:{handler.server.server_port}" for host in ("127.0.0.1", "localhost", "[::1]")
    }
    if len(hosts) != 1 or hosts[0] not in allowed:
        raise ValidationError(403, "invalid_host", "请求 Host 不受信任")
    origins = handler.headers.get_all("Origin", [])
    allowed_origins = getattr(handler.server, "demo_origins", ORIGINS)
    if (
        len(origins) > 1
        or (origins and origins[0] not in allowed_origins)
        or (handler.command == "POST" and not origins)
    ):
        raise ValidationError(403, "invalid_origin", "请求来源不受信任")
    return body


def _start_body(handler: BaseHTTPRequestHandler, body: bytes) -> StartRequest:
    content_types = handler.headers.get_all("Content-Type", [])
    if (
        len(content_types) != 1
        or content_types[0].split(";")[0].strip().lower() != "application/json"
    ):
        raise ValidationError(415, "unsupported_media_type", "请使用 JSON 请求")
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        raise ValidationError(400, "invalid_json", "请求不是合法 JSON") from None
    try:
        return StartRequest.model_validate(value)
    except ModelError:
        raise ValidationError(422, "invalid_argument", "只接受固定场景与合法请求 UUID") from None


def handle_demo(handler: BaseHTTPRequestHandler, service: DemoService | None) -> bool:
    if not handler.path.startswith(PREFIX):
        return False
    try:
        body = _guard(handler)
        path, method = handler.path, handler.command
        if method == "OPTIONS":
            _send_demo(handler, 204, {})
        elif method == "GET" and path == PREFIX + "scenarios":
            _send_demo(
                handler,
                200,
                service.list_scenarios()
                if service
                else {
                    "items": [],
                    "enabled": False,
                    "unavailable_reason": "演示未启用",
                },
            )
        elif method == "POST" and path == PREFIX + "runs":
            request = _start_body(handler, body)
            if service is None:
                raise ValidationError(503, "demo_unavailable", "演示未启用")
            created, run = service.start_with_status(request)
            _send_demo(handler, 202 if created else 200, run.model_dump(mode="json"))
        elif method == "GET" and path.startswith(PREFIX + "runs/"):
            if service is None:
                raise ValidationError(503, "demo_unavailable", "演示未启用")
            run = service.get(path.removeprefix(PREFIX + "runs/"))
            _send_demo(handler, 200, run.model_dump(mode="json"))
        else:
            raise ValidationError(404, "not_found", "没有此演示接口")
    except (BrokenPipeError, ConnectionError):
        handler.close_connection = True
    except Exception as error:
        safe = (
            error
            if isinstance(error, ValidationError)
            else ValidationError(500, "internal_error", "演示服务出现异常，请查询原运行核对结果")
        )
        try:
            _send_demo(handler, safe.status, {"error": safe.to_dict()})
        except OSError:
            handler.close_connection = True
    return True
