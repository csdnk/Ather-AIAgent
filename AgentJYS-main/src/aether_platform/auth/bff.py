"""Local Agent form login, independent of Budibase's browser session.

The first-party form exchanges credentials only with the configured local
Keycloak client. Passwords are never stored. This localhost-only factory is not
a production deployment: HTTPS and shared sessions remain deployment work.
"""

import hashlib
import json
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Any

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from aether_platform.auth.session import SessionTokens
from aether_platform.chat import Conversations
from aether_platform.configuration import RuntimeSettings
from aether_platform.directory import AccessDeniedError, Actor, Directory
from aether_platform.management import install_management
from aether_platform.memory import install_memory
from aether_platform.p3 import P3Error
from aether_platform.ui_routes import install_chat

ORIGIN = "http://localhost:19010"
BB = "http://localhost:19000"
ISSUER = "http://localhost:19080/realms/aether-lab"
PROTOCOL = ISSUER + "/protocol/openid-connect"
MANAGEMENT_APP = BB + "/app/default%20workspace/aether-admin"


class LoginCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=150)
    password: SecretStr = Field(min_length=1, max_length=1024)


def create_lab_app(config_path: Path) -> FastAPI:
    if json.loads(config_path.read_text(encoding="utf-8")).get("mode", "lab") != "lab":
        raise ValueError("Use create_cloud_app for cloud configuration")
    return _create_app(config_path)


def create_cloud_app(config_path: Path) -> FastAPI:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("mode") != "cloud":
        raise ValueError("Cloud configuration must explicitly select cloud mode")
    settings = RuntimeSettings.from_config(config)
    application = _create_app(config_path)

    @asynccontextmanager
    async def lifespan(root: FastAPI) -> AsyncIterator[None]:
        async with application.router.lifespan_context(application):
            yield

    root = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    root.mount(settings.prefix, application)
    return root


def _create_app(config_path: Path) -> FastAPI:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    settings = RuntimeSettings.from_config(config)
    origin, issuer = settings.origin, settings.issuer
    protocol = issuer + "/protocol/openid-connect"
    management_app = settings.management_url
    private = json.loads((Path(config["identity_lab_directory"]) / "private.json").read_text())
    directory = Directory(config["database_dsn"])
    # Keep the public issuer unchanged for JWT verification. Only the transport
    # address differs; this factory already restricts the entire lab to loopback.
    connect_issuer = settings.connect_issuer
    identity_http = httpx.Client(timeout=15, trust_env=False, follow_redirects=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            identity_http.close()

    app = FastAPI(
        title="Aether Agent", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    sessions: dict[str, dict[str, Any]] = {}
    attempts: dict[str, list[float]] = {}
    attempt_lock = Lock()

    def token_form(**values: Any) -> dict[str, Any]:
        return {
            "client_id": "platform-bff",
            "client_secret": private["bff_client_secret"],
            **values,
        }

    def hash_token(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def remote(method: str, url: str, **kwargs: Any) -> Any:
        try:
            response = identity_http.request(
                method, connect_issuer + url.removeprefix(issuer), **kwargs
            )
            if response.status_code in (400, 401, 403):
                raise HTTPException(401, "账号或密码不正确，或账号已停用。")
            if response.status_code != 200:
                raise HTTPException(503, "登录服务暂时不可用，请稍后重试。")
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "登录服务暂时不可用，请稍后重试。") from None

    def current(request: Request) -> tuple[str, dict[str, Any], Actor]:
        key = hash_token(request.cookies.get("aether_session", ""))
        session = sessions.get(key)
        if not session or session["expires"] <= time.time():
            sessions.pop(key, None)
            raise HTTPException(401, "请登录后继续。")
        try:
            session["token_provider"]()
            actor = directory.authenticate(issuer, session["subject"])
            if actor.role != "user":
                raise AccessDeniedError()
        except (P3Error, HTTPException, AccessDeniedError, KeyError, ValueError):
            sessions.pop(key, None)
            raise HTTPException(401, "登录已过期，请重新登录。") from None
        return key, session, actor

    def csrf(request: Request, session: dict[str, Any]) -> None:
        if request.headers.get("origin") != origin or not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""), session["csrf"]
        ):
            raise HTTPException(403, "Request origin or CSRF proof rejected")

    @app.middleware("http")
    async def no_cache(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        for key, value in list(sessions.items()):
            if value["expires"] <= time.time():
                sessions.pop(key, None)
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
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo a password through FastAPI's default validation input field.
        return JSONResponse({"detail": "输入格式不正确，请检查必填内容。"}, status_code=422)

    @app.exception_handler(AccessDeniedError)
    async def denied(request: Request, exc: AccessDeniedError) -> JSONResponse:
        return JSONResponse({"detail": "当前账号无权访问该内容。"}, status_code=403)

    @app.get("/auth/login")
    def login_page() -> RedirectResponse:
        return RedirectResponse(settings.cookie_path, status_code=303)

    @app.post("/auth/login")
    def login(request: Request, body: LoginCredentials) -> JSONResponse:
        if request.headers.get("origin") != origin:
            raise HTTPException(403, "Request origin rejected")
        # The cloud peer is the shared gateway. Limit by account there so one
        # user cannot exhaust every tester's quota. Never trust forwarding headers.
        # Direct local access additionally gets a per-peer limit.
        keys = ["user:" + hash_token(body.username.casefold())]
        if not settings.secure_cookie:
            peer = request.client.host if request.client else "unknown"
            keys.append("ip:" + peer)
        now = time.time()
        with attempt_lock:
            for key in list(attempts):
                attempts[key] = [stamp for stamp in attempts[key] if stamp > now - 60]
                if not attempts[key]:
                    attempts.pop(key)
            if len(attempts) >= 2000 or any(len(attempts.get(key, [])) >= 15 for key in keys):
                raise HTTPException(429, "登录尝试过于频繁，请稍后再试。")
            for key in keys:
                attempts.setdefault(key, []).append(now)
        sessions.pop(hash_token(request.cookies.get("aether_session", "")), None)
        if len(sessions) >= 1000:
            raise HTTPException(503, "登录人数已达当前环境上限。")
        tokens = remote(
            "POST",
            protocol + "/token",
            data=token_form(
                grant_type="password",
                scope="openid profile",
                username=body.username,
                password=body.password.get_secret_value(),
            ),
        )
        jwks = remote("GET", protocol + "/certs")
        try:
            header = jwt.get_unverified_header(tokens["id_token"])
            keys_jwt = [
                key for key in jwt.PyJWKSet.from_dict(jwks).keys if key.key_id == header.get("kid")
            ]
            if len(keys_jwt) != 1:
                raise ValueError("Ambiguous key")
            claims = jwt.decode(
                tokens["id_token"],
                keys_jwt[0].key,
                algorithms=["RS256"],
                issuer=issuer,
                audience="platform-bff",
                options={"require": ["iss", "sub", "aud", "exp", "iat"]},
            )
            if claims.get("azp", "platform-bff") != "platform-bff":
                raise ValueError("Invalid client")
            actor = directory.authenticate(issuer, claims["sub"])
            if actor.role != "user":
                raise HTTPException(403, "这是 Agent 用户登录页。管理员请直接登录管理后台。")
            opaque = secrets.token_urlsafe(32)
            key = hash_token(opaque)
            session = {
                "subject": claims["sub"],
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token"),
                "csrf": secrets.token_urlsafe(32),
                "version": actor.version,
                "access_expires": time.time() + int(tokens["expires_in"]),
                "expires": time.time() + 3600,
            }
            sessions[key] = session
            lease = SessionTokens(
                session,
                active=lambda: sessions.get(key) is session,
                refresh=lambda token: remote(
                    "POST",
                    protocol + "/token",
                    data=token_form(grant_type="refresh_token", refresh_token=token),
                ),
                introspect=lambda token: remote(
                    "POST", protocol + "/token/introspect", data=token_form(token=token)
                ),
                version=lambda: directory.authenticate(issuer, session["subject"]).version,
            )

            def fresh_token() -> str:
                try:
                    return lease.get()
                except (HTTPException, AccessDeniedError, KeyError, ValueError):
                    sessions.pop(key, None)
                    raise P3Error("AUTHENTICATION_REQUIRED") from None

            session["token_provider"] = fresh_token
        except AccessDeniedError:
            raise HTTPException(403, "当前账号或所属组织已停用，请联系管理员。") from None
        except (jwt.PyJWTError, KeyError, ValueError, TypeError):
            raise HTTPException(401, "登录身份校验失败，请重新登录。") from None
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

    @app.get("/auth/me")
    def me(request: Request) -> dict[str, Any]:
        _, session, actor = current(request)
        with directory.connection() as conn:
            profile = conn.execute(
                "SELECT display_name FROM users WHERE id=%s", (actor.id,)
            ).fetchone()
        return {
            "user_id": actor.id,
            "role": actor.role,
            "csrf_token": session["csrf"],
            "display_name": profile["display_name"] if profile else actor.id,
        }

    @app.post("/auth/logout")
    def logout(request: Request) -> JSONResponse:
        if request.headers.get("origin") != origin:
            raise HTTPException(403, "Request origin rejected")
        key = hash_token(request.cookies.get("aether_session", ""))
        session = sessions.get(key)
        if session:
            csrf(request, session)
        sessions.pop(key, None)
        revoked = False
        if session and session.get("refresh_token"):
            try:
                result = identity_http.post(
                    connect_issuer + "/protocol/openid-connect/logout",
                    data=token_form(refresh_token=session["refresh_token"]),
                )
                revoked = result.status_code == 204
            except httpx.HTTPError:
                pass
        response = JSONResponse({"logged_out": True, "identity_session_revoked": revoked})
        response.delete_cookie(
            "aether_session", path=settings.cookie_path, secure=settings.secure_cookie
        )
        response.delete_cookie(
            "aether_login", path=settings.cookie_path, secure=settings.secure_cookie
        )
        return response

    @app.get("/management")
    def management_entry() -> RedirectResponse:
        # Compatibility bookmark only: never read or change either application's session.
        return RedirectResponse(management_app, status_code=303)

    install_chat(
        app, Conversations(directory), current, csrf, config.get("llm", {}), config.get("p3", {})
    )
    install_memory(app, directory, current, csrf, config.get("p3", {}))
    install_management(
        app,
        directory,
        issuer,
        private["budibase_client_secret"],
        connect_issuer=connect_issuer,
        budibase_url=config.get("budibase_internal_url") if settings.secure_cookie else None,
        management_url=management_app,
    )
    web_dist = Path(config["web_dist"]) if config.get("web_dist") else None
    if web_dist:
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def index() -> Response:
        if web_dist:
            return FileResponse(web_dist / "index.html")
        return HTMLResponse(
            "<html lang='zh-CN'><title>Aether Agent</title><p>请先构建 Agent 页面。</p></html>"
        )

    return app
