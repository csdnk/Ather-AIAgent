"""Read-only business console; the live management identity never becomes a user token."""

import json
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import HTTPException, Path, Query, Request
from pydantic import AwareDatetime, BeforeValidator

from aether_platform.operations.models import now
from aether_platform.operations.presentation import attach_names
from aether_platform.p3 import P3Client, P3Error, identifier

ObjectId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


def iso_timestamp(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("ISO8601 timestamp with timezone required")
    return value


IsoTimestamp = Annotated[AwareDatetime, BeforeValidator(iso_timestamp)]


def envelope(items, total=None, **extra):
    return {
        "items": items,
        **({"total": total} if total is not None else {}),
        "observed_at": now(),
        "status": "ok",
        **extra,
    }


class Console:
    def __init__(self, config, directory):
        self.config, self.directory = config, directory

    @staticmethod
    def scope(actor):
        return actor.role == "platform_admin", actor.tenant_id

    def audit(self, actor, resource, target_id, user_id, tenant_id, status):
        # Fixed-purpose metadata only: never save request headers, bodies or provider errors.
        with self.directory.connection() as conn:
            conn.execute(
                "INSERT INTO ops_content_access"
                "(actor_id,tenant_id,user_id,resource,target_id,reason,status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (actor.id, tenant_id, user_id, resource, target_id, "operations_diagnosis", status),
            )

    def target(self, actor, user_id):
        with self.directory.connection() as conn:
            row = conn.execute(
                "SELECT id,tenant_id FROM users WHERE id=%s AND tenant_id IS NOT NULL "
                "AND (%s OR tenant_id=%s)",
                (user_id, *self.scope(actor)),
            ).fetchone()
        if not row:
            raise HTTPException(404, "用户不存在或不在授权范围")
        return row

    def content(self, actor, resource, target_id, user_id, operation: Callable):
        status, tenant_id = "unavailable", actor.tenant_id
        try:
            permissions = getattr(actor, "permissions", ())
            if actor.role not in {"platform_admin", "tenant_admin"} or (
                "*:*:*" not in permissions and "aether:content:read" not in permissions
            ):
                raise HTTPException(403, "没有用户内容读取权限")
            target = self.target(actor, user_id) if user_id else None
            if target:
                tenant_id = target["tenant_id"]
            result = operation(target)
            status = "succeeded"
            return result
        except HTTPException as error:
            status = "denied" if error.status_code in {401, 403, 404} else "unavailable"
            raise
        except P3Error as error:
            status = (
                "denied"
                if error.code in {"FORBIDDEN", "NOT_FOUND", "HTTP_403", "HTTP_404"}
                else "unavailable"
            )
            raise self.upstream_error(error) from None
        finally:
            # An audit failure fails the read closed; unaudited content is never returned.
            self.audit(actor, resource, target_id, user_id, tenant_id, status)

    @staticmethod
    def upstream_error(error):
        codes = {
            "FORBIDDEN": 403,
            "HTTP_403": 403,
            "NOT_FOUND": 404,
            "HTTP_404": 404,
            "MEMORY_GONE": 410,
            "RESULT_INVALIDATED": 410,
            "AUTHENTICATION_REQUIRED": 401,
            "HTTP_401": 401,
            "INVALID_CURSOR": 422,
            "INVALID_ARGUMENT": 422,
            "INVALID_IDENTIFIER": 422,
        }
        code = (
            error.code
            if error.code in codes
            or error.code
            in {
                "P3_NOT_CONFIGURED",
                "CONNECTION_UNCONFIRMED",
                "INVALID_RESPONSE",
                "REQUEST_IN_PROGRESS",
                "RESULT_UNCONFIRMED",
            }
            else "UPSTREAM_UNAVAILABLE"
        )
        return HTTPException(
            codes.get(error.code, 503),
            {
                "code": code,
                "status": "unavailable",
                "message": "业务服务暂不能返回此记录",
            },
        )

    def users(self, actor, q, tenant_id, limit, offset):
        if tenant_id and actor.role != "platform_admin" and tenant_id != actor.tenant_id:
            raise HTTPException(403, "企业不在授权范围")
        query = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where = (
            "FROM users u LEFT JOIN tenants t ON t.id=u.tenant_id "
            "WHERE u.tenant_id IS NOT NULL AND (%s OR u.tenant_id=%s) "
            "AND (CAST(%s AS text) IS NULL OR u.tenant_id=%s) "
            "AND (u.username ILIKE %s ESCAPE '\\' OR u.display_name ILIKE %s ESCAPE '\\' "
            "OR t.name ILIKE %s ESCAPE '\\')"
        )
        args = (*self.scope(actor), tenant_id, tenant_id, query, query, query)
        with self.directory.connection() as conn:
            total = conn.execute("SELECT count(*) AS n " + where, args).fetchone()["n"]
            items = conn.execute(
                "SELECT u.id,u.username,u.display_name,u.tenant_id,t.name AS tenant_name,"
                "u.enabled,u.role " + where + " ORDER BY u.username,u.id LIMIT %s OFFSET %s",
                (*args, limit, offset),
            ).fetchall()
            tenants = conn.execute(
                "SELECT id,name FROM tenants WHERE (%s OR id=%s) ORDER BY name,id LIMIT 1001",
                self.scope(actor),
            ).fetchall()
        return envelope(
            items,
            total,
            tenant_choices=tenants[:1000],
            tenant_choices_truncated=len(tenants) > 1000,
        )

    def conversations(self, target, limit, offset, *, status=None, from_time=None, to_time=None):
        if from_time and to_time and from_time > to_time:
            raise HTTPException(422, "开始时间不能晚于结束时间")
        where = "FROM conversations c WHERE c.user_id=%s AND c.tenant_id=%s AND NOT c.archived "
        args = [target["id"], target["tenant_id"]]
        latest = (
            "(SELECT status FROM chat_turns t WHERE t.conversation_id=c.id "
            "ORDER BY t.created_at DESC,t.id DESC LIMIT 1)"
        )
        if status:
            where += "AND " + latest + "=%s "
            args.append(status)
        if from_time:
            where += "AND c.updated_at>=%s "
            args.append(from_time)
        if to_time:
            where += "AND c.updated_at<=%s "
            args.append(to_time)
        with self.directory.connection() as conn:
            total = conn.execute(
                "SELECT count(*) AS n " + where,
                args,
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT c.id,c.title,c.created_at,c.updated_at,"
                "(SELECT count(*) FROM chat_turns t WHERE t.conversation_id=c.id) AS turn_count,"
                + latest
                + " AS last_status "
                + where
                + "ORDER BY c.updated_at DESC,c.id LIMIT %s OFFSET %s",
                (*args, limit, offset),
            ).fetchall()
        return envelope(rows, total)

    def conversation(self, target, conversation_id, limit, offset):
        with self.directory.connection() as conn:
            conversation = conn.execute(
                "SELECT id,title,user_id,tenant_id,created_at,updated_at FROM conversations "
                "WHERE id=%s AND user_id=%s AND tenant_id=%s AND NOT archived",
                (conversation_id, target["id"], target["tenant_id"]),
            ).fetchone()
            if not conversation:
                raise HTTPException(404, "会话不存在或不在授权范围")
            total = conn.execute(
                "SELECT count(*) AS n FROM chat_turns WHERE conversation_id=%s",
                (conversation_id,),
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT id,substr(input,1,65536) AS input,substr(output,1,65536) AS output,"
                "length(input)>65536 OR length(output)>65536 AS content_truncated,"
                "status,phase,created_at,started_at,first_token_at,memory_evidence "
                "FROM chat_turns WHERE conversation_id=%s ORDER BY created_at,id "
                "LIMIT %s OFFSET %s",
                (conversation_id, limit, offset),
            ).fetchall()
        turns = []
        for row in rows:
            evidence = row.pop("memory_evidence") or {}
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            memory_ids = []
            for memory in evidence.get("memories", []):
                if isinstance(memory, dict) and isinstance(memory.get("memory_id"), str):
                    memory_ids.append(memory["memory_id"])
            if isinstance(evidence.get("memory_id"), str):
                memory_ids.append(evidence["memory_id"])
            turns.append(
                {
                    **row,
                    "memory_status": evidence.get("status", "not_recorded"),
                    "memory_ids": list(dict.fromkeys(memory_ids)),
                    "recall_id": evidence.get("recall_id"),
                    "job_id": evidence.get("job_id"),
                    "operation_id": evidence.get("operation_id"),
                    "task_ids": evidence.get("task_ids", []),
                    "link_status": "recorded"
                    if memory_ids or evidence.get("recall_id")
                    else "not_recorded",
                    "recall_evidence_status": "linked_result_requires_live_read"
                    if evidence.get("recall_id")
                    else "not_recorded",
                }
            )
        return {
            "conversation": conversation,
            "turns": turns,
            "turns_total": total,
            "turns_truncated": offset + len(turns) < total,
            "observed_at": now(),
            "status": "ok",
        }

    def p3_read(self, actor, token, path, params=None):
        settings = self.config.get("p3", {})
        if not settings.get("base_url"):
            raise P3Error("P3_NOT_CONFIGURED")
        with P3Client(
            settings["base_url"],
            token,
            actor,
            admin_read=True,
            wait_seconds=0,
            trusted_http_host=settings.get("internal_http_host")
            or settings.get("trusted_http_host"),
        ) as client:
            return client.call("GET", path, params=params)

    def business(self, actor, token, resource, target, object_id=None, **filters):
        path = "/p3/admin/" + resource + ("/" + identifier(object_id) if object_id else "")
        result = self.p3_read(
            actor,
            token,
            path,
            {
                "tenant_id": target["tenant_id"],
                "user_id": target["id"],
                **{k: v for k, v in filters.items() if v is not None},
            },
        )
        return {"observed_at": now(), "status": "ok", **result}

    def diagnostics(
        self, actor, token, task_id=None, *, limit=50, cursor=None, flow=None, state=None
    ):
        if actor.role != "platform_admin":
            raise HTTPException(403, "仅平台运维可访问")
        try:
            result = self.p3_read(
                actor,
                token,
                "/p3/admin/" + ("tasks/" + identifier(task_id) if task_id else "diagnostics"),
                params=None
                if task_id
                else {
                    "limit": limit,
                    **{
                        k: v
                        for k, v in {"cursor": cursor, "flow": flow, "state": state}.items()
                        if v
                    },
                },
            )
            if not task_id:
                result = {**result, "business": self.business_observations(actor)}
            rows = [result["task"]] if task_id else result.get("tasks", {}).get("items", [])
            for row in rows:
                scope = row.get("subject", {}).get("scope", {})
                row["tenant_id"], row["user_id"] = scope.get("tenant_id"), scope.get("user_id")
            attach_names({"items": rows}, "tasks", actor, self.directory)
            return result
        except P3Error as error:
            raise self.upstream_error(error) from None

    def business_observations(self, actor):
        stale_after = max(
            60, int(self.config.get("operations", {}).get("sample_interval_seconds", 30)) * 3
        )
        with self.directory.connection() as conn:
            rows = conn.execute(
                "SELECT latest.*,t.name AS tenant_name FROM ("
                "SELECT DISTINCT ON (metric,tenant_id) metric,tenant_id,value,observed_at,"
                "observed_at<now()-(%s * interval '1 second') AS stale FROM ops_samples "
                "WHERE (%s OR tenant_id=%s) AND metric IN "
                "('request_failures_15m','oldest_pending_seconds','chat_turns_15m',"
                "'memory_save_failures_15m','memory_save_pending','stale_pending_requests') "
                "ORDER BY metric,tenant_id,observed_at DESC,id DESC) latest "
                "LEFT JOIN tenants t ON t.id=latest.tenant_id ORDER BY latest.tenant_id,metric",
                (stale_after, *self.scope(actor)),
            ).fetchall()
        status = (
            "not_collected" if not rows else "stale" if any(row["stale"] for row in rows) else "ok"
        )
        return envelope(
            rows,
            status=status,
            stale_after_seconds=stale_after,
            scope_note=(
                "平台会话与保存回执；15m 指请求创建时间窗口，pending 为当前全部未完成；"
                "不代表 P3 全部记忆与任务"
            ),
        )

    def history(self, actor, limit, offset):
        scope = self.scope(actor)
        with self.directory.connection() as conn:
            total = sum(
                conn.execute(
                    "SELECT count(*) AS n FROM " + table + " WHERE (%s OR tenant_id=%s)",
                    scope,
                ).fetchone()["n"]
                for table in ("ops_commands", "ops_content_access")
            )
            rows = conn.execute(
                "SELECT events.*,a.display_name AS actor_name,u.display_name AS user_name,"
                "t.name AS tenant_name "
                "FROM (SELECT id,'command' AS kind,actor_id,tenant_id,NULL AS user_id,"
                "resource,action,target_id,"
                "status,created_at,'' AS reason FROM ops_commands WHERE (%s OR tenant_id=%s) "
                "UNION ALL SELECT CAST(id AS text) AS id,'content_access' AS kind,"
                "actor_id,tenant_id,user_id,resource,'read' AS action,target_id,status,"
                "created_at,reason FROM ops_content_access WHERE (%s OR tenant_id=%s)) AS events "
                "LEFT JOIN users a ON a.id=events.actor_id "
                "LEFT JOIN users u ON u.id=events.user_id AND u.tenant_id=events.tenant_id "
                "LEFT JOIN tenants t ON t.id=events.tenant_id "
                "ORDER BY events.created_at DESC,kind,events.id "
                "LIMIT %s OFFSET %s",
                (*scope, *scope, limit, offset),
            ).fetchall()
        return envelope(rows, total)


def install_console(app, service, identity):
    prefix = "/platform-ops/v1/console"
    user_query = Query(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")

    @app.middleware("http")
    async def prevent_content_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(prefix + "/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.get(prefix + "/users")
    def users(
        request: Request,
        q: str = Query("", max_length=120),
        tenant_id: str | None = Query(None, max_length=128),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
    ):
        actor, _ = identity(request)
        return service.content(
            actor, "users", None, None, lambda _: service.users(actor, q, tenant_id, limit, offset)
        )

    @app.get(prefix + "/conversations")
    def conversations(
        request: Request,
        user_id: str = user_query,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        status: Literal["pending", "complete", "failed"] | None = None,
        from_time: Annotated[IsoTimestamp | None, Query(alias="from")] = None,
        to_time: Annotated[IsoTimestamp | None, Query(alias="to")] = None,
    ):
        actor, _ = identity(request)
        return service.content(
            actor,
            "conversations",
            None,
            user_id,
            lambda target: service.conversations(
                target, limit, offset, status=status, from_time=from_time, to_time=to_time
            ),
        )

    @app.get(prefix + "/conversations/{conversation_id}")
    def conversation(
        conversation_id: ObjectId,
        request: Request,
        user_id: str = user_query,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
    ):
        actor, _ = identity(request)
        return service.content(
            actor,
            "conversations",
            conversation_id,
            user_id,
            lambda target: service.conversation(target, conversation_id, limit, offset),
        )

    @app.get(prefix + "/memories")
    def memories(
        request: Request,
        user_id: str = user_query,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None, max_length=4096),
        kind: Literal["working", "episodic", "semantic"] | None = None,
        status: Literal["active", "archived", "superseded", "expired", "deleted"] | None = None,
    ):
        actor, token = identity(request)
        return service.content(
            actor,
            "memories",
            None,
            user_id,
            lambda target: service.business(
                actor,
                token,
                "memories",
                target,
                limit=limit,
                cursor=cursor,
                kind=kind,
                status=status,
            ),
        )

    @app.get(prefix + "/memories/{memory_id}")
    def memory(memory_id: ObjectId, request: Request, user_id: str = user_query):
        actor, token = identity(request)
        return service.content(
            actor,
            "memories",
            memory_id,
            user_id,
            lambda target: service.business(actor, token, "memories", target, memory_id),
        )

    @app.get(prefix + "/recalls/{recall_id}")
    def recall(recall_id: ObjectId, request: Request, user_id: str = user_query):
        actor, token = identity(request)
        return service.content(
            actor,
            "recalls",
            recall_id,
            user_id,
            lambda target: service.business(actor, token, "recalls", target, recall_id),
        )

    @app.get(prefix + "/diagnostics")
    def diagnostics(
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None, max_length=4096),
        flow: str | None = Query(None, max_length=64, pattern=r"^[a-z_]+$"),
        state: str | None = Query(None, max_length=64, pattern=r"^[a-z_]+$"),
    ):
        actor, token = identity(request)
        return service.diagnostics(actor, token, limit=limit, cursor=cursor, flow=flow, state=state)

    @app.get(prefix + "/tasks/{task_id}")
    def task(task_id: ObjectId, request: Request):
        actor, token = identity(request)
        return service.diagnostics(actor, token, task_id)

    @app.get(prefix + "/history")
    def history(
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
    ):
        actor, _ = identity(request)
        return service.history(actor, limit, offset)
