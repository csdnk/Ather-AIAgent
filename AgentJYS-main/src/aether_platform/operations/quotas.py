"""Tenant admission limits; observed token budget is not prepaid token reservation."""

from collections.abc import Mapping
from typing import Any

from psycopg import Connection


class QuotaExceededError(ValueError):
    pass


def check_limits(limits: Mapping[str, int], usage: Mapping[str, int], new_turn: bool) -> None:
    for setting, field in (("concurrent_turns", "pending"), ("observed_token_budget", "tokens")):
        if setting in limits and usage[field] >= limits[setting]:
            raise QuotaExceededError("当前租户已达到并发或用量限制，请稍后重试或联系管理员。")
    if new_turn and "daily_requests" in limits and usage["requests"] >= limits["daily_requests"]:
        raise QuotaExceededError("当前租户已达到最近 24 小时请求限额。")


def enforce_admission(conn: Connection[dict[str, Any]], tenant_id: str, *, new_turn: bool) -> None:
    table = conn.execute("SELECT to_regclass('ops_records') AS table_name").fetchone()
    assert table is not None
    if not table["table_name"]:
        return
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("quota:" + tenant_id,))
    row = conn.execute(
        "SELECT payload FROM ops_records WHERE resource='quotas' AND id=%s", (tenant_id,)
    ).fetchone()
    if not row:
        return
    usage = conn.execute(
        "SELECT count(*) FILTER(WHERE t.status='pending') AS pending,"
        "count(*) FILTER(WHERE t.created_at>now()-interval '24 hours') AS requests,"
        "COALESCE(sum((t.model_usage->>'total_tokens')::bigint) "
        "FILTER(WHERE t.created_at>now()-interval '24 hours'),0) AS tokens "
        "FROM chat_turns t JOIN conversations c ON c.id=t.conversation_id WHERE c.tenant_id=%s",
        (tenant_id,),
    ).fetchone()
    assert usage is not None
    tokens = conn.execute(
        "SELECT COALESCE(sum((m.usage->>'total_tokens')::bigint),0) AS tokens "
        "FROM chat_model_usage m JOIN chat_turns t ON t.id=m.turn_id "
        "JOIN conversations c ON c.id=t.conversation_id WHERE c.tenant_id=%s "
        "AND m.observed_at>now()-interval '24 hours'",
        (tenant_id,),
    ).fetchone()
    assert tokens is not None
    usage["tokens"] = tokens["tokens"]
    check_limits(row["payload"], usage, new_turn)
