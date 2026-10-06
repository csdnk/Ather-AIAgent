"""Browser-independent observations with durable alert state and delivery receipts."""

import logging
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)


def validate_probe(url):
    parsed = urlsplit(url)
    return bool(
        parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and (
            parsed.scheme == "https"
            or parsed.scheme == "http"
            and parsed.hostname.endswith(".svc.cluster.local")
        )
    )


def classify_probe(status):
    return 0 if status is not None and 200 <= status < 300 else 1


class Collector:
    def __init__(self, config, store):
        self.settings, self.store = config.get("operations", {}), store
        self.stopping = Event()
        self.thread = None

    def start(self):
        if self.thread is None:
            self.thread = Thread(target=self.loop, name="aether-ops-collector", daemon=True)
            self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=25)

    def loop(self):
        while not self.stopping.is_set():
            try:
                self.collect()
            except Exception:
                # Never log exception values containing database credentials or response bodies.
                log.error("Operational collector failed; observations may be stale")
            self.stopping.wait(max(10, int(self.settings.get("sample_interval_seconds", 30))))

    def collect(self):
        with self.store.directory.connection() as conn:
            # One collector owns a sampling transaction even with multiple API replicas.
            if not conn.execute("SELECT pg_try_advisory_xact_lock(195442026)").fetchone()[
                "pg_try_advisory_xact_lock"
            ]:
                return
            rows = conn.execute(
                "SELECT c.tenant_id,count(*) FILTER(WHERE t.status='failed' AND "
                "t.created_at>now()-interval '15 minutes') AS failures,"
                "COALESCE(max(EXTRACT(EPOCH FROM (now()-t.started_at))) FILTER(WHERE "
                "t.status='pending'),0) AS oldest_pending "
                "FROM conversations c JOIN chat_turns t ON t.conversation_id=c.id GROUP BY "
                "c.tenant_id"
            ).fetchall()
            rules = {
                r["payload"].get("metric"): r["payload"]
                for r in conn.execute(
                    "SELECT payload FROM ops_records WHERE resource='rules'"
                ).fetchall()
            }
            for row in rows:
                for metric, field, default in (
                    ("request_failures_15m", "failures", 0),
                    ("oldest_pending_seconds", "oldest_pending", 120),
                ):
                    rule = rules.get(metric, {})
                    if rule.get("enabled", True):
                        self.store.observe(
                            metric,
                            row["tenant_id"],
                            float(row[field]),
                            float(rule.get("threshold", default)),
                        )
            for probe in self.settings.get("http_probes", []):
                if not validate_probe(probe["url"]):
                    raise ValueError("Probe URL not permitted")
                try:
                    response = httpx.get(
                        probe["url"], timeout=5, trust_env=False, follow_redirects=False
                    )
                    value = classify_probe(response.status_code)
                except httpx.HTTPError:
                    value = 1
                self.store.observe("dependency_" + probe["name"], None, value, 0)
            conn.execute("DELETE FROM ops_samples WHERE observed_at < now()-interval '30 days'")
        self.deliver()

    def deliver(self):
        url = self.settings.get("notification_url")
        if not url:
            return
        if not validate_probe(url):
            raise ValueError("Notification destination not permitted")
        key = Path(self.settings["notification_key_file"]).read_text().strip()
        if len(key) < 32:
            raise ValueError("Notification credential is not configured")
        with self.store.directory.connection() as conn:
            if not conn.execute("SELECT pg_try_advisory_xact_lock(195442027)").fetchone()[
                "pg_try_advisory_xact_lock"
            ]:
                return
            alerts = conn.execute(
                "SELECT a.* FROM ops_alerts a WHERE (silenced_until IS NULL OR "
                "silenced_until<now()) AND NOT EXISTS "
                "(SELECT 1 FROM ops_deliveries d WHERE d.alert_id=a.id AND d.channel=a.state AND "
                "(d.state='delivered' OR d.created_at>now()-interval '5 minutes')) ORDER BY "
                "a.first_seen LIMIT 20"
            ).fetchall()
            for alert in alerts:
                payload = {
                    k: alert[k]
                    for k in ("id", "metric", "tenant_id", "state", "value", "threshold")
                }
                payload["alert_id"] = payload.pop("id")
                state, code = "failed", "TRANSPORT_ERROR"
                try:
                    response = httpx.post(
                        url,
                        json=payload,
                        headers={"X-Aether-Notification-Key": key},
                        timeout=5,
                        trust_env=False,
                        follow_redirects=False,
                    )
                    data = response.json()
                    if response.is_success and data.get("code") == 0:
                        state, code = "delivered", "OK"
                    else:
                        code = "DESTINATION_REJECTED"
                except (httpx.HTTPError, ValueError):
                    pass
                conn.execute(
                    "INSERT INTO ops_deliveries(alert_id,channel,state,code) VALUES (%s,%s,%s,%s)",
                    (alert["id"], alert["state"], state, code),
                )
