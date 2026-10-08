from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request

from aether_platform.auth.ruoyi import RuoyiIdentityVerifier
from aether_platform.directory import AccessDeniedError, Actor, Directory
from aether_platform.operations.console import Console, install_console
from aether_platform.operations.models import Command, Resource
from aether_platform.operations.service import Operations

GLOBAL_RESOURCES = {"configuration", "backups", "resources", "tasks", "rules", "quotas"}


def install_operations(
    app: FastAPI,
    config: dict[str, Any],
    directory: Directory,
    verifier: RuoyiIdentityVerifier,
    *,
    service: Operations | None = None,
    console: Console | None = None,
) -> Operations:
    if service is None:
        service = Operations(config, directory)

    def identity(request: Request, *, permission: str = "aether:ops:read") -> tuple[Actor, str]:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or not authorization[7:]:
            raise HTTPException(401, "请登录管理后台")
        token = authorization[7:]
        try:
            actor = verifier.verify_ruoyi_actor(token)
        except AccessDeniedError:
            raise HTTPException(401, "身份已失效") from None
        except (RuntimeError, ValueError):
            raise HTTPException(503, "身份服务暂时不可用") from None
        if actor.role not in {"platform_admin", "tenant_admin"}:
            raise HTTPException(403, "没有管理权限")
        permissions = getattr(actor, "permissions", ())
        if "*:*:*" not in permissions and (
            permission not in permissions or "aether:ops:read" not in permissions
        ):
            raise HTTPException(403, "没有此操作的权限")
        return actor, token

    install_console(app, console or Console(config, directory), identity)
    from aether_platform.operations.memory_admin import install_memory_admin

    install_memory_admin(app, service, identity)

    @app.get("/platform-ops/v1/{resource}", response_model=None)
    def read(
        resource: Resource,
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        command_id: str | None = Query(
            None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"
        ),
        cursor: str | None = Query(None, max_length=4096),
        q: str | None = Query(None, max_length=64),
        task_id: str | None = Query(None, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
        status: str | None = Query(None, max_length=64),
    ) -> dict[str, Any]:
        actor, token = identity(request)
        if resource == "support" and (q == "task_cases" or task_id):
            if actor.role != "platform_admin":
                raise HTTPException(403, detail={"code": "CASE_PERMISSION_REQUIRED"})
            from aether_platform.operations.task_cases import TaskCases

            return TaskCases(service).read(
                actor, task_id=task_id, state=status, limit=limit, offset=offset
            )
        if resource in GLOBAL_RESOURCES and actor.role != "platform_admin":
            raise HTTPException(403, "仅平台运维可访问")
        if command_id:
            if resource != "commands":
                raise HTTPException(422, "command_id 仅适用于操作查询")
            return service.command_status(actor, token, command_id)
        if cursor:
            if resource != "tasks":
                raise HTTPException(422, "cursor 仅适用于任务列表")
            return service.read(actor, token, resource, limit, offset, cursor=cursor)
        return service.read(actor, token, resource, limit, offset)

    @app.post("/platform-ops/v1/commands", response_model=None)
    def command(request: Request, body: Command) -> dict[str, Any]:
        actor, token = identity(request, permission=f"aether:{body.resource}:execute")
        if body.resource in GLOBAL_RESOURCES and actor.role != "platform_admin":
            raise HTTPException(403, "仅平台运维可操作")
        return service.command(actor, token, body)

    return service
