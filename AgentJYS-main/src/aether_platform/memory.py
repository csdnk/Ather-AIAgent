"""User-owned P3 commands and reads, with durable request identities."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from aether_platform.chat import Conversations
from aether_platform.directory import Directory
from aether_platform.p3 import P3Client, P3Error, identifier


class MemoryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    action: Literal[
        "save",
        "recall",
        "consolidate",
        "distill",
        "correct",
        "archive",
        "activate",
        "delete",
        "reprocess",
        "reindex",
        "retention",
        "reflection",
        "source_delete",
        "source_revoke",
    ]
    memory_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    memory_ids: list[str] = Field(default_factory=list, max_length=32)
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    params: dict[str, Any] = Field(default_factory=dict)


def source(command_id: str, occurred_at: str, kind: str = "text") -> dict[str, Any]:
    timestamp = datetime.fromisoformat(occurred_at).astimezone(UTC)
    return {
        "kind": kind,
        "external_id": identifier(command_id),
        "external_version": "1",
        "occurred_at": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }


def build_command(
    p3: P3Client, command: MemoryCommand, occurred_at: str
) -> tuple[str, dict[str, Any] | list[dict[str, Any]], str | None]:
    from aether_agent_memory.remember.contracts.models import (
        CorrectionRequest,
        DeleteRequest,
        LifecycleRequest,
        ReflectionRequest,
        RetentionRequest,
    )

    body: Any
    action, values = command.action, dict(command.params)
    if command.memory_ids and action != "distill":
        raise HTTPException(422, "只有知识提炼支持多条记忆。")
    # Identity/scope fields are always server-owned, including in nested contracts.
    if set(values) & {
        "scope",
        "selection",
        "tenant_id",
        "user_id",
        "principal_id",
        "source",
        "refs",
        "content_ref",
    }:
        raise HTTPException(422, "身份与范围由系统确定。")
    mid = identifier(command.memory_id) if command.memory_id else None
    prefix = "/p3/remember/" + mid if mid else ""
    if action in {"save", "recall"}:
        if (
            set(values)
            - ({"text", "sources", "token_budget"} if action == "recall" else {"text", "category"})
            or not isinstance(values.get("text"), str)
            or not 1 <= len(values["text"]) <= 12000
        ):
            raise HTTPException(422, "请输入 1–12000 字符的内容。")
        if action == "recall":
            requested = values.get("sources", "long_term")
            if requested not in {"auto", "both", "long_term", "working"} or (
                requested in {"working", "both"} and not command.session_id
            ):
                raise HTTPException(422, "工作记忆检索必须选择当前会话。")
            budget = values.get("token_budget", 1800)
            if type(budget) is not int or not 128 <= budget <= 8192:
                raise HTTPException(422, "检索预算应为 128–8192 的整数。")
            return (
                "/p3/recall",
                {
                    "query": values["text"],
                    "selection": p3.selection(command.session_id),
                    "sources": requested,
                    "token_budget": budget,
                },
                "recall.execute",
            )
        category = values.get("category", "fact")
        if category not in {"observation", "event", "fact", "decision", "explicit_constraint"}:
            raise HTTPException(422, "记忆类别无效。")
        return (
            "/p3/remember",
            {
                "source": source(command.command_id, occurred_at),
                "selection": p3.selection(command.session_id),
                "content": {"kind": "text", "text": values["text"]},
                "importance_category": category,
            },
            "remember.save",
        )
    if action == "consolidate":
        if values:
            raise HTTPException(422, "不支持这些参数。")
        return "/p3/remember/consolidate", p3.selection(command.session_id), "remember.consolidate"
    if action == "reflection":
        body = ReflectionRequest.model_validate(
            {"selection": p3.selection(command.session_id), **values}
        )
        return "/p3/remember/reflection", body.model_dump(mode="json"), "remember.reflection"
    if action == "distill":
        if values:
            raise HTTPException(422, "不支持这些参数。")
        ids = command.memory_ids or ([mid] if mid else [])
        if not ids or len(set(ids)) != len(ids):
            raise HTTPException(422, "请选择不同的经历记忆。")
        refs = [p3.call("GET", "/p3/remember/" + identifier(value))["ref"] for value in ids]
        return "/p3/remember/distill", refs, "remember.distill"
    if not mid:
        raise HTTPException(422, "请先选择记忆。")
    if action == "correct":
        body = CorrectionRequest.model_validate(
            {**values, "source": source(command.command_id, occurred_at)}
        )
        return prefix + "/correct", body.model_dump(mode="json"), "remember.correct"
    if action in {"archive", "activate"}:
        body = LifecycleRequest.model_validate(
            {**values, "target": "archived" if action == "archive" else "active"}
        )
        return prefix + "/lifecycle", body.model_dump(mode="json"), "remember.lifecycle"
    if action == "retention":
        body = RetentionRequest.model_validate(values)
        return (
            prefix + "/retention",
            body.model_dump(mode="json", exclude_unset=True),
            "remember.retention",
        )
    if action == "delete":
        body = DeleteRequest.model_validate(values)
        return prefix + "/delete", body.model_dump(mode="json"), "remember.delete"
    if action in {"reindex", "reprocess"}:
        if values:
            raise HTTPException(422, "不支持这些参数。")
        return prefix + "/" + action, {}, "remember." + action
    snapshot = p3.call("GET", prefix)
    # Resolve the source from an authorized memory; a caller cannot supply a source ID.
    index = values.pop("source_index", 0)
    body = DeleteRequest.model_validate(values)
    sources = snapshot.get("sources", [])
    if type(index) is not int or index < 0 or index >= len(sources):
        raise HTTPException(422, "请选择有效来源。")
    sid = identifier(sources[index]["source_id"])
    return (
        "/p3/sources/" + sid + ("/delete" if action == "source_delete" else "/revoke"),
        body.model_dump(mode="json"),
        "source.delete" if action == "source_delete" else "source.revoke",
    )


class MemoryService:
    def __init__(self, directory: Directory) -> None:
        self.directory = directory
        with directory.connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS memory_commands (
                id text PRIMARY KEY, user_id text NOT NULL, tenant_id text NOT NULL,
                action text NOT NULL, fingerprint text NOT NULL, payload jsonb,
                result jsonb, error text, created_at timestamptz NOT NULL DEFAULT now()
            )""")

    def retry(self, p3: P3Client, command_id: str) -> dict[str, Any]:
        actor = p3.actor
        with self.directory.connection() as conn:
            row = conn.execute(
                "SELECT * FROM memory_commands WHERE id=%s AND user_id=%s AND tenant_id=%s",
                (command_id, actor.id, actor.tenant_id),
            ).fetchone()
        if not row:
            raise HTTPException(404, "操作记录不存在。")
        if not row["payload"]:
            raise HTTPException(422, "此请求尚无有效参数；文档导入请重新选择原文件。")
        payload = row["payload"]
        if row["action"] == "recall" and row["result"]:
            result = p3.call(
                "GET", "/p3/recalls/" + identifier(row["result"]["recall_id"]) + "/result"
            )
        else:
            result = p3.call(
                "POST",
                payload["path"],
                body=payload["body"],
                operation_id=command_id,
                kind=payload["kind"],
            )
        if self.directory.authenticate(actor.issuer, actor.subject) != actor:
            raise HTTPException(403, "账号权限已变化。")
        with self.directory.connection() as conn:
            conn.execute(
                "UPDATE memory_commands SET result=%s,error=NULL WHERE id=%s",
                (Jsonb(result), command_id),
            )
        return {"command_id": command_id, "result": result}

    def run(self, p3: P3Client, command: MemoryCommand) -> dict[str, Any]:
        actor = p3.actor
        fingerprint = hashlib.sha256(
            json.dumps(command.model_dump(), sort_keys=True).encode()
        ).hexdigest()
        with self.directory.connection() as conn:
            conn.execute(
                "INSERT INTO memory_commands(id,user_id,tenant_id,action,fingerprint) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (command.command_id, actor.id, actor.tenant_id, command.action, fingerprint),
            )
            row = conn.execute(
                "SELECT * FROM memory_commands WHERE id=%s", (command.command_id,)
            ).fetchone()
            if (
                not row
                or row["user_id"] != actor.id
                or row["tenant_id"] != actor.tenant_id
                or row["fingerprint"] != fingerprint
            ):
                raise HTTPException(409, "请求编号已被使用。")
            if row["result"] is not None and command.action == "recall":
                result = row["result"]
                result = p3.call(
                    "GET", "/p3/recalls/" + identifier(result["recall_id"]) + "/result"
                )
                if self.directory.authenticate(actor.issuer, actor.subject) != actor:
                    raise HTTPException(403, "账号权限已变化。")
                return {"command_id": command.command_id, "result": result}
        # Store exactly the first resolved command so retries retain time and version.
        payload = row["payload"]
        if payload is None:
            path, body, kind = build_command(p3, command, row["created_at"].isoformat())
            proposed = {"path": path, "body": body, "kind": kind}
            with self.directory.connection() as conn:
                row = conn.execute(
                    "UPDATE memory_commands SET payload=COALESCE(payload,%s) "
                    "WHERE id=%s RETURNING payload",
                    (Jsonb(proposed), command.command_id),
                ).fetchone()
                assert row is not None
                payload = row["payload"]
        try:
            result = p3.call(
                "POST",
                payload["path"],
                body=payload["body"],
                operation_id=command.command_id,
                kind=payload["kind"],
            )
        except P3Error as error:
            with self.directory.connection() as conn:
                conn.execute(
                    "UPDATE memory_commands SET error=%s WHERE id=%s",
                    (json.dumps({"code": error.code, "job_id": error.job_id}), command.command_id),
                )
            raise
        # Recheck live business authority before publishing a result.
        if self.directory.authenticate(actor.issuer, actor.subject) != actor:
            raise HTTPException(403, "账号权限已变化。")
        with self.directory.connection() as conn:
            conn.execute(
                "UPDATE memory_commands SET result=%s,error=NULL WHERE id=%s",
                (Jsonb(result), command.command_id),
            )
        return {"command_id": command.command_id, "result": result}

    def document(
        self, p3: P3Client, upload_id: str, content: bytes, media_type: str
    ) -> dict[str, Any]:
        actor = p3.actor
        fingerprint = hashlib.sha256(media_type.encode() + b"\0" + content).hexdigest()
        key = "document_" + identifier(upload_id)
        with self.directory.connection() as conn:
            conn.execute(
                "INSERT INTO memory_commands(id,user_id,tenant_id,action,fingerprint) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (key, actor.id, actor.tenant_id, "document", fingerprint),
            )
            row = conn.execute("SELECT * FROM memory_commands WHERE id=%s", (key,)).fetchone()
            if (
                not row
                or row["user_id"] != actor.id
                or row["tenant_id"] != actor.tenant_id
                or row["fingerprint"] != fingerprint
            ):
                raise HTTPException(409, "文件请求编号已被使用。")
            if row["result"] is not None:
                return {"result": row["result"]}
        document_id = "doc_" + hashlib.sha256((actor.id + upload_id).encode()).hexdigest()[:48]
        document = p3.call(
            "PUT",
            "/p3/documents/" + document_id,
            params={"version": "1"},
            content=content,
            media_type=media_type,
            operation_id="upload_" + upload_id,
            kind="remember.document",
        )
        result = p3.call(
            "POST",
            "/p3/remember",
            body={
                "source": source(key, row["created_at"].isoformat(), "document"),
                "selection": p3.selection(),
                "content": document,
                "importance_category": "fact",
            },
            operation_id="docsave_" + upload_id,
            kind="remember.save",
        )
        if self.directory.authenticate(actor.issuer, actor.subject) != actor:
            raise HTTPException(403, "账号权限已变化。")
        with self.directory.connection() as conn:
            conn.execute("UPDATE memory_commands SET result=%s WHERE id=%s", (Jsonb(result), key))
        return {"result": result}


def install_memory(
    app: FastAPI, directory: Directory, current: Any, csrf: Any, settings: dict[str, Any]
) -> None:
    service = MemoryService(directory)

    def client(request: Request, *, write: bool = False) -> P3Client:
        _, session, actor = current(request)
        if write:
            csrf(request, session)
        if actor.role != "user":
            raise HTTPException(403, "仅普通用户可访问自己的记忆。")
        if not settings.get("base_url"):
            raise HTTPException(503, "P3 尚未配置。")
        return P3Client(settings["base_url"], session["token_provider"], actor,
                        trusted_http_host=settings.get("internal_http_host"))

    @app.exception_handler(P3Error)
    async def p3_error(request: Request, error: P3Error) -> Any:
        status = {
            "FORBIDDEN": 403,
            "UNAUTHENTICATED": 401,
            "AUTHENTICATION_REQUIRED": 401,
            "NOT_FOUND": 404,
            "MEMORY_NOT_FOUND": 404,
            "SOURCE_NOT_FOUND": 404,
            "INVALID_IDENTIFIER": 422,
            "VALIDATION_ERROR": 422,
            "VERSION_CONFLICT": 409,
            "REVISION_CONFLICT": 409,
            "RESULT_INVALIDATED": 410,
            "MEMORY_GONE": 410,
            "IDEMPOTENCY_CONFLICT": 409,
            "INVALID_ARGUMENT": 422,
        }.get(error.code, 503)
        return JSONResponse(
            {"detail": "记忆服务未完成操作：" + error.code, "job_id": error.job_id},
            status_code=status,
        )

    @app.get("/memory-api/status")
    def status(request: Request) -> Any:
        with client(request) as p3:
            p3.identity()
            return {"connected": True, "capabilities": p3.call("GET", "/p3/capabilities")}

    @app.post("/memory-api/documents")
    async def document(request: Request) -> Any:
        from starlette.concurrency import run_in_threadpool

        p3 = await run_in_threadpool(client, request, write=True)
        try:
            upload_id = identifier(request.headers.get("x-upload-id", ""))
            if len(upload_id) > 64:
                raise HTTPException(422, "文件请求编号过长。")
            media_type = request.headers.get("content-type", "").split(";")[0]
            if media_type not in {
                "text/plain",
                "text/markdown",
                "text/csv",
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }:
                raise HTTPException(415, "支持 TXT、Markdown、CSV、PDF 和 DOCX。")
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 5 * 1024 * 1024:
                    raise HTTPException(413, "文档不能超过 5 MB。")
            if not data:
                raise HTTPException(422, "文件为空。")
            await run_in_threadpool(p3.identity)
            return await run_in_threadpool(service.document, p3, upload_id, bytes(data), media_type)
        finally:
            p3.http.close()

    @app.get("/memory-api/memories")
    def memories(request: Request, cursor: str | None = None) -> Any:
        with client(request) as p3:
            p3.identity()
            return p3.call(
                "GET",
                "/p3/memories",
                params={"limit": 30, **({"cursor": cursor} if cursor else {})},
            )

    @app.get("/memory-api/memories/{memory_id}")
    def detail(request: Request, memory_id: str) -> Any:
        with client(request) as p3:
            snapshot = p3.call("GET", "/p3/remember/" + identifier(memory_id))
            snapshot["source_metadata"] = [
                p3.call("GET", "/p3/sources/" + identifier(item["source_id"]))
                for item in snapshot.get("sources", [])
            ]
            return snapshot

    @app.get("/memory-api/memories/{memory_id}/{view}")
    def memory_view(
        request: Request,
        memory_id: str,
        view: Literal["processing", "retention", "body", "range", "source"],
        start: int = 0,
        end: int | None = None,
    ) -> Any:
        with client(request) as p3:
            mid = identifier(memory_id)
            if view in {"processing", "retention"}:
                return p3.call("GET", "/p3/remember/" + mid + "/" + view)
            snapshot = p3.call("GET", "/p3/remember/" + mid)
            if view == "body":
                return p3.call("POST", "/p3/remember/body", body=snapshot["ref"])
            if start < 0 or (end is not None and end < start):
                raise HTTPException(422, "范围无效。")
            return p3.call(
                "POST",
                "/p3/sources/read-range" if view == "source" else "/p3/remember/body/range",
                body=snapshot["sources"][0] if view == "source" else snapshot["ref"],
                params={"start": start, **({"end": end} if end is not None else {})},
            )

    @app.get("/memory-api/reflection")
    def reflection(request: Request, session_id: str | None = None) -> Any:
        with client(request) as p3:
            if session_id:
                with directory.connection() as conn:
                    Conversations(directory).owned(conn, p3.actor, identifier(session_id))
            return p3.call(
                "GET",
                "/p3/remember/reflection",
                params={"session_id": session_id} if session_id else {},
            )

    @app.get("/memory-api/operations/{job_id}")
    def operation(request: Request, job_id: str) -> Any:
        with client(request) as p3:
            return p3.call("GET", "/p3/operations/" + identifier(job_id))

    @app.get("/memory-api/recalls/{recall_id}")
    def recalled(request: Request, recall_id: str) -> Any:
        with client(request) as p3:
            return p3.call("GET", "/p3/recalls/" + identifier(recall_id) + "/result")

    @app.post("/memory-api/commands")
    def command(request: Request, body: MemoryCommand) -> Any:
        from pydantic import ValidationError

        with client(request, write=True) as p3:
            p3.identity()
            if body.session_id:
                with directory.connection() as conn:
                    Conversations(directory).owned(conn, p3.actor, body.session_id)
            try:
                return service.run(p3, body)
            except ValidationError:
                raise HTTPException(422, "参数格式或版本不正确。") from None

    @app.get("/memory-api/commands")
    def commands(request: Request) -> Any:
        with client(request) as p3, directory.connection() as conn:
            return {
                "items": conn.execute(
                    "SELECT id,action,(result IS NOT NULL) AS completed,error,created_at "
                    "FROM memory_commands WHERE user_id=%s AND tenant_id=%s "
                    "ORDER BY created_at DESC LIMIT 30",
                    (p3.actor.id, p3.actor.tenant_id),
                ).fetchall()
            }

    @app.post("/memory-api/commands/{command_id}/retry")
    def retry(request: Request, command_id: str) -> Any:
        with client(request, write=True) as p3:
            p3.identity()
            return service.retry(p3, identifier(command_id))
