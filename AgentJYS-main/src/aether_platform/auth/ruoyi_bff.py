"""Same-origin Agent sessions authenticated only by current Ruoyi authority."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.middleware.base import RequestResponseEndpoint

from aether_platform.auth.ruoyi import RuoyiDirectory, RuoyiIdentityVerifier, RuoyiUnavailableError
from aether_platform.chat import Conversations
from aether_platform.configuration import RuntimeSettings
from aether_platform.directory import AccessDeniedError, Actor
from aether_platform.memory import install_memory
from aether_platform.p3 import P3Error
from aether_platform.ui_routes import install_chat


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=150)
    password: SecretStr = Field(min_length=1, max_length=1024)


def create_ruoyi_app(
    config: dict[str, Any], *, identity_client: httpx.Client | None = None
) -> FastAPI:
    settings = RuntimeSettings.from_config(config)
    directory = RuoyiDirectory(config["database_dsn"])
    verifier = RuoyiIdentityVerifier(config, directory, client=identity_client)
    directory.verifier = verifier
    sessions: dict[str, dict[str, Any]] = {}
    attempts: dict[str, list[float]] = {}
    lock = RLock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            verifier.close()

    app = FastAPI(
        title="Aether Agent", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.ruoyi_verifier = verifier
    app.state.directory = directory

    def digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def invalidate(key: str) -> None:
        with lock:
            session = sessions.pop(key, None)
        if session:
            directory.forget(session["subject"])

    def current(request: Request) -> tuple[str, dict[str, Any], Actor]:
        key = digest(request.cookies.get("aether_session", ""))
        with lock:
            session = sessions.get(key)
        if not session or session["expires"] <= time.time():
            invalidate(key)
            raise HTTPException(401, "请登录后继续。")
        try:
            actor = directory.authenticate(settings.issuer, session["subject"])
            if actor != session["actor"] or actor.role != "user":
                raise AccessDeniedError("Session authority changed")
        except (AccessDeniedError, RuoyiUnavailableError):
            invalidate(key)
            raise HTTPException(401, "登录身份已失效，请重新登录。") from None
        return key, session, actor

    def csrf(request: Request, session: dict[str, Any]) -> None:
        if request.headers.get("origin") != settings.origin or not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""), session["csrf"]
        ):
            raise HTTPException(403, "Request origin or CSRF proof rejected")

    @app.middleware("http")
    async def no_cache(request: Request, call_next: RequestResponseEndpoint) -> Response:
        with lock:
            expired = [
                key for key, session in sessions.items() if session["expires"] <= time.time()
            ]
        for key in expired:
            invalidate(key)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            }
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "输入格式不正确。"}, status_code=422)

    @app.exception_handler(AccessDeniedError)
    async def denied(request: Request, exc: AccessDeniedError) -> JSONResponse:
        return JSONResponse({"detail": "当前身份无权访问。"}, status_code=403)

    @app.exception_handler(RuoyiUnavailableError)
    async def unavailable(request: Request, exc: RuoyiUnavailableError) -> JSONResponse:
        return JSONResponse({"detail": "身份服务暂时不可用。"}, status_code=503)

    @app.get("/auth/login")
    def login_entry() -> RedirectResponse:
        return RedirectResponse(settings.cookie_path, status_code=303)

    @app.post("/auth/login")
    def login(request: Request, body: Credentials) -> JSONResponse:
        if request.headers.get("origin") != settings.origin:
            raise HTTPException(403, "Request origin rejected")
        key = digest(body.username.casefold())
        with lock:
            now = time.time()
            for old in list(attempts):
                attempts[old] = [stamp for stamp in attempts[old] if stamp > now - 60]
                if not attempts[old]:
                    del attempts[old]
            if len(attempts) >= 2000 or len(attempts.get(key, [])) >= 15:
                raise HTTPException(429, "登录尝试过于频繁。")
            attempts.setdefault(key, []).append(now)
            if len(sessions) >= 1000:
                raise HTTPException(503, "登录人数已达当前环境上限。")
        tokens = verifier.remote(
            "POST",
            "/admin-api/aether/identity/login",
            json={"username": body.username, "password": body.password.get_secret_value()},
        )
        token = tokens.get("accessToken")
        if not isinstance(token, str):
            raise AccessDeniedError("Login did not return a credential")
        actor = directory.register(token, time.time() + 3600)
        if actor.role != "user":
            directory.forget(actor.subject)
            raise HTTPException(403, "这是 Agent 用户登录页。管理员请登录管理后台。")
        invalidate(digest(request.cookies.get("aether_session", "")))
        opaque = secrets.token_urlsafe(32)
        session_key = digest(opaque)
        session: dict[str, Any] = {
            "subject": actor.subject,
            "actor": actor,
            "csrf": secrets.token_urlsafe(32),
            "access_token": token,
            "expires": time.time() + 3600,
        }

        def fresh_token() -> str:
            with lock:
                active = sessions.get(session_key) is session
            try:
                if (
                    not active
                    or session["expires"] <= time.time()
                    or directory.authenticate(settings.issuer, actor.subject) != actor
                ):
                    raise AccessDeniedError("Session expired or changed")
                return token
            except (AccessDeniedError, RuoyiUnavailableError):
                invalidate(session_key)
                raise P3Error("AUTHENTICATION_REQUIRED") from None

        session["token_provider"] = fresh_token
        with lock:
            sessions[session_key] = session
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            "aether_session",
            opaque,
            max_age=3600,
            httponly=True,
            samesite="lax",
            secure=settings.secure_cookie,
            path=settings.cookie_path,
        )
        return response

    @app.get("/auth/me", response_model=None)
    def me(request: Request) -> dict[str, Any]:
        _, session, actor = current(request)
        return {
            "user_id": actor.id,
            "role": actor.role,
            "csrf_token": session["csrf"],
            "display_name": actor.id,
        }

    @app.post("/auth/logout")
    def logout(request: Request) -> JSONResponse:
        key, session, actor = current(request)
        csrf(request, session)
        invalidate(key)
        revoked = False
        try:
            result = verifier.http.post(
                verifier.config["base_url"].rstrip("/") + "/admin-api/system/auth/logout",
                headers={"Authorization": "Bearer " + session["access_token"]},
            )
            revoked = result.status_code == 200 and result.json().get("code") == 0
        except (httpx.HTTPError, ValueError):
            pass
        response = JSONResponse({"logged_out": True, "identity_session_revoked": revoked})
        response.delete_cookie(
            "aether_session", path=settings.cookie_path, secure=settings.secure_cookie
        )
        return response

    @app.get("/management")
    def management() -> RedirectResponse:
        return RedirectResponse(settings.management_url, status_code=303)

    install_chat(
        app, Conversations(directory), current, csrf, config.get("llm", {}), config.get("p3", {})
    )
    install_memory(app, directory, current, csrf, config.get("p3", {}))
    web_dist = Path(config["web_dist"]) if config.get("web_dist") else None
    if web_dist:
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

    @app.get("/")
    def index() -> Response:
        return (
            FileResponse(web_dist / "index.html")
            if web_dist
            else HTMLResponse(
                '<html lang="zh-CN"><title>Aether Agent</title><p>请先构建 Agent 页面。</p></html>'
            )
        )

    return app
