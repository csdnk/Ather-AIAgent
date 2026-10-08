"""Owned document receipts and revalidated parsed-source context for chat."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aether_platform.directory import Actor
from aether_platform.p3 import P3Error


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$")


def bind_attachments(
    conn: Any, actor: Actor, attachments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if len(attachments) > 3:
        raise ValueError("Too many attachments")
    result, seen = [], set()
    for value in attachments:
        attachment = Attachment.model_validate(value).model_dump()
        uid = attachment["upload_id"]
        if uid in seen:
            raise ValueError("Duplicate attachment")
        seen.add(uid)
        row = conn.execute(
            "SELECT result FROM memory_commands WHERE id=%s AND user_id=%s "
            "AND tenant_id=%s AND action='document'",
            ("document_" + uid, actor.id, actor.tenant_id),
        ).fetchone()
        receipt = row["result"] if row else None
        if not receipt or not receipt.get("saved") or not receipt.get("memories"):
            raise ValueError("Document upload is not confirmed")
        memory, source = receipt["memories"][0], receipt["source"]
        scope = memory["scope"]
        if any(
            scope.get(key) != expected
            for key, expected in (
                ("tenant_id", actor.tenant_id),
                ("user_id", actor.id),
                ("application_id", "agent-platform"),
                ("agent_id", "aether"),
            )
        ):
            raise ValueError("Document scope differs")
        result.append({**attachment, "memory": memory, "source": source})
    return result


def validate_attachment(p3: Any, binding: dict[str, Any]) -> None:
    try:
        _validate_attachment(p3, binding)
    except P3Error as error:
        if error.code in {"MEMORY_GONE", "NOT_FOUND", "FORBIDDEN", "PERMISSION_DENIED"}:
            raise P3Error("RESULT_INVALIDATED") from error
        raise


def _validate_attachment(p3: Any, binding: dict[str, Any]) -> None:
    memory, source = binding["memory"], binding["source"]
    current = p3.call("GET", "/p3/remember/" + memory["memory_id"])
    if (
        current.get("ref", {}).get("memory_id") != memory["memory_id"]
        or current.get("ref", {}).get("scope") != memory["scope"]
        or current.get("status") != "active"
        or source not in current.get("sources", [])
    ):
        raise P3Error("RESULT_INVALIDATED")
    metadata = p3.call("POST", "/p3/sources/read-range", body=source, params={"start": 0, "end": 0})
    if metadata.get("source") != source:
        raise P3Error("RESULT_INVALIDATED")


def read_attachment(p3: Any, binding: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    validate_attachment(p3, binding)
    result = p3.call("POST", "/p3/sources/read-range", body=binding["source"])
    if result.get("source") != binding["source"] or not isinstance(result.get("content"), str):
        raise P3Error("RESULT_INVALIDATED")
    text = result["content"][:12000]
    evidence = {
        **binding,
        "included_chars": len(text),
        "total_chars": result["total_chars"],
        "truncated": not result["is_complete"] or len(result["content"]) > len(text),
    }
    validate_attachment(p3, binding)
    return json.dumps(
        {"filename": binding["name"], "text": text, "truncated": evidence["truncated"]},
        ensure_ascii=False,
    ), evidence


def recent_attachments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected, seen = [], set()
    for row in reversed(rows):
        if row["role"] != "user":
            continue
        for attachment in row.get("attachments", []):
            if attachment["upload_id"] not in seen:
                selected.append(attachment)
                seen.add(attachment["upload_id"])
                if len(selected) == 3:
                    return selected
    return selected
