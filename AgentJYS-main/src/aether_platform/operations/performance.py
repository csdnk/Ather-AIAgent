"""Tenant-scoped dashboard aggregates; no conversation content or invented measurements."""

from datetime import UTC, datetime, timedelta


def performance(directory, actor):
    observed = datetime.now(UTC)
    result = {"observed_at": observed.isoformat(), "status": "ok", "windows": {}}
    query = """
        WITH scoped AS (
          SELECT date_bin(%s * interval '1 second',t.created_at,
                          timestamptz '2000-01-01') AS bucket,
                 t.status,t.memory_evidence->>'status' AS memory_status,
                 t.recall_return_ms AS latency
          FROM chat_turns t JOIN conversations c ON c.id=t.conversation_id
          WHERE (%s OR c.tenant_id=%s) AND t.created_at>=%s AND t.created_at<%s
        )
        SELECT bucket AS at,count(*) AS requests,
          count(*) FILTER(WHERE status='complete') AS complete,
          count(*) FILTER(WHERE status='failed') AS failed,
          count(*) FILTER(WHERE status='pending') AS pending,
          count(*) FILTER(WHERE memory_status='saved') AS saved,
          count(latency) AS recall_latency_samples,
          percentile_cont(0.95) WITHIN GROUP(ORDER BY latency) AS recall_return_p95_ms
        FROM scoped GROUP BY GROUPING SETS ((bucket),()) ORDER BY bucket NULLS LAST
    """
    with directory.connection() as conn:
        for key, seconds, bucket in (("1h", 3600, 60), ("24h", 86400, 900), ("7d", 604800, 3600)):
            start = observed - timedelta(seconds=seconds)
            rows = conn.execute(
                query, (bucket, actor.role == "platform_admin", actor.tenant_id, start, observed)
            ).fetchall()
            empty = dict(
                requests=0,
                complete=0,
                failed=0,
                pending=0,
                saved=0,
                recall_latency_samples=0,
                recall_return_p95_ms=None,
            )
            summary = next((dict(r) for r in rows if r["at"] is None), dict(empty))
            terminal = summary["complete"] + summary["failed"]
            summary["success_rate"] = summary["complete"] / terminal * 100 if terminal else None
            points = {int(r["at"].timestamp()): dict(r) for r in rows if r["at"] is not None}
            series = []
            tick = int(start.timestamp()) // bucket * bucket
            while tick < observed.timestamp():
                point = points.get(tick, dict(empty))
                series.append({**point, "at": datetime.fromtimestamp(tick, UTC).isoformat()})
                tick += bucket
            result["windows"][key] = {
                "summary": summary,
                "series": series,
                "from": start.isoformat(),
                "to": observed.isoformat(),
                "bucket_seconds": bucket,
            }
    return result
