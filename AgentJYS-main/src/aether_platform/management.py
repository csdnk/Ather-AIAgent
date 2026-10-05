"""Budibase REST resource server: signed user identity, live directory scope."""

from typing import Any
from uuid import uuid4

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from aether_platform.directory import AccessDeniedError, Actor, Directory


class EditUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    display_name: str
    command_id: str = Field(default_factory=lambda: str(uuid4()))


class DisableUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    command_id: str = Field(default_factory=lambda: str(uuid4()))


def install_management(
    app: FastAPI,
    directory: Directory,
    issuer: str,
    secret: str,
    *,
    connect_issuer: str | None = None,
    budibase_url: str | None = None,
    management_url: str | None = None,
) -> None:
    transport = connect_issuer or issuer

    def actor(request: Request) -> Actor:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "请使用 Aether 账号登录管理中心。")
        token = authorization[7:]
        return actor_from_token(token)

    def actor_from_token(token: str) -> Actor:
        try:
            with httpx.Client(timeout=10, trust_env=False) as client:
                keys_response = client.get(transport + "/protocol/openid-connect/certs")
                keys_response.raise_for_status()
                keys = jwt.PyJWKSet.from_dict(keys_response.json()).keys
                kid = jwt.get_unverified_header(token).get("kid")
                keys = [key for key in keys if key.key_id == kid]
                if len(keys) != 1:
                    raise ValueError("Invalid signing key")
                claims = jwt.decode(
                    token,
                    keys[0].key,
                    algorithms=["RS256"],
                    issuer=issuer,
                    audience="budibase",
                    options={"require": ["iss", "sub", "aud", "exp", "iat", "azp"]},
                )
                if claims["azp"] != "budibase":
                    raise ValueError("Invalid client")
                response = client.post(
                    transport + "/protocol/openid-connect/token/introspect",
                    data={"client_id": "budibase", "client_secret": secret, "token": token},
                )
                response.raise_for_status()
                active = response.json()
                if not active.get("active") or active.get("sub") != claims["sub"]:
                    raise ValueError("Expired identity")
        except (jwt.PyJWTError, KeyError, ValueError, TypeError):
            raise HTTPException(401, "管理登录已过期，请重新登录。") from None
        except httpx.HTTPError:
            raise HTTPException(503, "身份服务暂时不可用。") from None
        current = directory.authenticate(issuer, claims["sub"])
        if current.role not in {"platform_admin", "tenant_admin"}:
            raise AccessDeniedError()
        return current

    if budibase_url and management_url:

        @app.get("/ops/temporal-auth")
        def temporal_access(request: Request) -> Response:
            cookie = request.headers.get("cookie")
            if not cookie:
                return RedirectResponse(management_url, status_code=303)
            try:
                with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
                    response = client.get(
                        budibase_url + "/api/global/self", headers={"Cookie": cookie}
                    )
                if response.status_code in {401, 403}:
                    return RedirectResponse(management_url, status_code=303)
                response.raise_for_status()
                current = actor_from_token(response.json()["oauth2"]["accessToken"])
                if current.role != "platform_admin":
                    raise AccessDeniedError()
                return Response(status_code=200)
            except AccessDeniedError:
                raise HTTPException(403, "Temporal 运行页面仅供平台管理员查看。") from None
            except (KeyError, ValueError):
                return RedirectResponse(management_url, status_code=303)
            except httpx.HTTPError:
                raise HTTPException(503, "管理登录服务暂时不可用。") from None

    @app.get("/management-api/users")
    def users(request: Request) -> list[dict[str, Any]]:
        current = actor(request)
        rows = directory.list_users(current)
        with directory.connection() as conn:
            current = directory._admin(conn, current)
            tenants = {
                t["id"]: t
                for t in conn.execute(
                    "SELECT id,name,enabled FROM tenants WHERE (%s OR id=%s)",
                    (current.role == "platform_admin", current.tenant_id),
                ).fetchall()
            }
        roles = {"platform_admin": "平台管理员", "tenant_admin": "租户管理员", "user": "普通用户"}
        return [
            {
                **r,
                "角色": roles[r["role"]],
                "账号": r["username"],
                "姓名": r["display_name"],
                "状态": (
                    "停用"
                    if not r["enabled"]
                    else "租户已停用"
                    if r["tenant_id"] and not tenants[r["tenant_id"]]["enabled"]
                    else "启用"
                ),
                "租户": tenants[r["tenant_id"]]["name"] if r["tenant_id"] else "平台级",
            }
            for r in rows
        ]

    @app.get("/management-api/tenants")
    def tenants(request: Request) -> list[dict[str, Any]]:
        current = actor(request)
        with directory.connection() as conn:
            current = directory._admin(conn, current)
            rows = conn.execute(
                "SELECT t.id,t.name,t.enabled,count(u.id) AS users FROM tenants t "
                "LEFT JOIN users u ON u.tenant_id=t.id WHERE (%s OR t.id=%s) "
                "GROUP BY t.id ORDER BY t.id",
                (current.role == "platform_admin", current.tenant_id),
            ).fetchall()
        return [
            {
                **r,
                "租户名称": r["name"],
                "状态": "启用" if r["enabled"] else "停用",
                "用户数": r["users"],
            }
            for r in rows
        ]

    @app.get("/management-api/audit")
    def audit(request: Request) -> list[dict[str, Any]]:
        current = actor(request)
        with directory.connection() as conn:
            current = directory._admin(conn, current)
            rows = conn.execute(
                "SELECT a.id,a.action,a.created_at,u.username AS target,c.status "
                "FROM audit_events a JOIN users u ON u.id=a.target_id "
                "JOIN identity_commands c ON c.id=a.command_id "
                "WHERE (%s OR u.tenant_id=%s) ORDER BY a.id DESC LIMIT 100",
                (current.role == "platform_admin", current.tenant_id),
            ).fetchall()
        return [
            {
                **r,
                "时间（UTC）": r["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
                "目标账号": r["target"],
                "操作": "更新用户资料或状态",
                "同步状态": "待同步" if r["status"] == "pending" else r["status"],
            }
            for r in rows
        ]

    @app.post("/management-api/profile")
    def update(request: Request, body: EditUser) -> dict[str, Any]:
        current = actor(request)
        users = [u for u in directory.list_users(current) if u["username"] == body.username]
        if len(users) != 1:
            raise AccessDeniedError()
        try:
            return directory.update_user(
                current, users[0]["id"], {"display_name": body.display_name}, body.command_id
            )
        except AccessDeniedError:
            raise
        except ValueError:
            raise HTTPException(409, "资料无效或操作编号冲突，请检查输入。") from None

    @app.post("/management-api/disable")
    def disable(request: Request, body: DisableUser) -> dict[str, Any]:
        current = actor(request)
        users = [u for u in directory.list_users(current) if u["username"] == body.username]
        if len(users) != 1:
            raise AccessDeniedError()
        try:
            return directory.update_user(
                current, users[0]["id"], {"enabled": False}, body.command_id
            )
        except AccessDeniedError:
            raise
        except ValueError:
            raise HTTPException(409, "账号状态变更被拒绝，请检查目标账号。") from None
