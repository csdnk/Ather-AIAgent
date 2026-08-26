from __future__ import annotations

import asyncio
import json
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pydantic import ValidationError

from aether_p4_simulator.client import P3ClientError, P3MemoryClient
from aether_p4_simulator.models import (
    CreateAgentRequest,
    CreateSessionRequest,
    SendMessageRequest,
    SubmitDocumentRequest,
)
from aether_p4_simulator.service import P4SimulatorService

SESSION_RE = re.compile(r"^/api/v1/sessions/([^/]+)$")
MESSAGE_RE = re.compile(r"^/api/v1/sessions/([^/]+)/messages$")
DOCUMENT_RE = re.compile(r"^/api/v1/sessions/([^/]+)/documents$")
TASK_RE = re.compile(r"^/api/v1/tasks/([^/]+)$")


def build_service() -> P4SimulatorService:
    base_url = os.getenv("AETHER_P3_BASE_URL", "http://localhost:8080")
    timeout = float(os.getenv("AETHER_P4_P3_TIMEOUT_SECONDS", "8"))
    return P4SimulatorService(P3MemoryClient(base_url, timeout_seconds=timeout))


SERVICE = build_service()


class P4Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(HTTPStatus.NO_CONTENT, {})

    def do_GET(self) -> None:  # noqa: N802
        try:
            if self.path == "/health":
                self._send(HTTPStatus.OK, asyncio.run(SERVICE.health()).model_dump(mode="json"))
                return
            if self.path == "/api/v1/agents":
                self._send(
                    HTTPStatus.OK,
                    {"items": [item.model_dump(mode="json") for item in SERVICE.list_agents()]},
                )
                return
            session_match = SESSION_RE.fullmatch(self.path)
            if session_match:
                session = SERVICE.get_session(session_match.group(1))
                self._send(HTTPStatus.OK, session.model_dump(mode="json"))
                return
            task_match = TASK_RE.fullmatch(self.path)
            if task_match:
                self._send(HTTPStatus.OK, asyncio.run(SERVICE.get_task(task_match.group(1))))
                return
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except Exception as exc:
            self._handle_error(exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._body()
            if self.path == "/api/v1/agents":
                agent = SERVICE.create_agent(CreateAgentRequest.model_validate(body))
                self._send(HTTPStatus.CREATED, agent.model_dump(mode="json"))
                return
            if self.path == "/api/v1/sessions":
                session = SERVICE.create_session(CreateSessionRequest.model_validate(body))
                self._send(HTTPStatus.CREATED, session.model_dump(mode="json"))
                return
            message_match = MESSAGE_RE.fullmatch(self.path)
            if message_match:
                result = asyncio.run(
                    SERVICE.send_message(
                        message_match.group(1),
                        SendMessageRequest.model_validate(body),
                    )
                )
                self._send(HTTPStatus.OK, result.model_dump(mode="json"))
                return
            document_match = DOCUMENT_RE.fullmatch(self.path)
            if document_match:
                task = asyncio.run(
                    SERVICE.submit_document(
                        document_match.group(1),
                        SubmitDocumentRequest.model_validate(body),
                    )
                )
                self._send(HTTPStatus.ACCEPTED, task)
                return
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except Exception as exc:
            self._handle_error(exc)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return dict(payload)

    def _handle_error(self, exc: Exception) -> None:
        if isinstance(exc, ValidationError):
            self._send(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "invalid request", "details": exc.errors(include_url=False)},
            )
            return
        if isinstance(exc, KeyError):
            self._send(HTTPStatus.NOT_FOUND, {"error": str(exc).strip("'")})
            return
        if isinstance(exc, P3ClientError):
            status = exc.status_code if 400 <= exc.status_code < 600 else 502
            self._send(
                HTTPStatus(status),
                {"error": str(exc), "source": "p3", "p3_payload": exc.payload},
            )
            return
        if isinstance(exc, (ValueError, json.JSONDecodeError)):
            self._send(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
            return
        self._send(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {"error": f"{type(exc).__name__}: {exc}"},
        )

    def _send(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8") if payload else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", os.getenv("AETHER_P4_CORS_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = int(os.getenv("AETHER_P4_PORT", "8090"))
    ThreadingHTTPServer(("0.0.0.0", port), P4Handler).serve_forever()


if __name__ == "__main__":
    main()
