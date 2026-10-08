"""Durable ops records, CAS edits and an at-most-once external command handoff."""

import hashlib
import json
from contextlib import nullcontext
from typing import Any
from uuid import uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from aether_platform.directory import Actor, Directory
from aether_platform.operations.models import Command, page

SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_recall_schemes (
 id text PRIMARY KEY, tenant_id text NOT NULL, user_id text NOT NULL, payload jsonb NOT NULL,
 version integer NOT NULL DEFAULT 1, deleted boolean NOT NULL DEFAULT false,
 updated_by text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ops_recall_schemes_scope ON ops_recall_schemes(tenant_id,user_id);
CREATE TABLE IF NOT EXISTS ops_memory_commands (
 id text PRIMARY KEY, actor_id text NOT NULL,
 tenant_id text NOT NULL, user_id text NOT NULL, action text NOT NULL, request jsonb NOT NULL,
 status text NOT NULL DEFAULT 'pending', result jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ops_memory_commands_scope
 ON ops_memory_commands(tenant_id,user_id,created_at DESC);
CREATE TABLE IF NOT EXISTS ops_task_cases (
 task_id text PRIMARY KEY, tenant_id text, payload jsonb NOT NULL,
 version integer NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ops_commands (
 id text PRIMARY KEY, actor_id text NOT NULL, tenant_id text, resource text NOT NULL,
 action text NOT NULL, target_id text, fingerprint text NOT NULL,
 status text NOT NULL, result jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ops_records (
 resource text NOT NULL, id text NOT NULL, tenant_id text, payload jsonb NOT NULL,
 version integer NOT NULL DEFAULT 1, updated_by text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(resource,id)
);
CREATE TABLE IF NOT EXISTS ops_alerts (
 id text PRIMARY KEY, metric text NOT NULL, tenant_id text, state text NOT NULL,
 value double precision NOT NULL, threshold double precision NOT NULL,
 occurrences integer NOT NULL DEFAULT 1, owner_id text, note text,
 first_seen timestamptz NOT NULL DEFAULT now(), last_seen timestamptz NOT NULL DEFAULT now(),
 resolved_at timestamptz, silenced_until timestamptz
);
CREATE TABLE IF NOT EXISTS ops_samples (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, metric text NOT NULL,
 tenant_id text, value double precision NOT NULL, observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ops_samples_time ON ops_samples(observed_at);
CREATE TABLE IF NOT EXISTS ops_deliveries (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, alert_id text NOT NULL,
 channel text NOT NULL, state text NOT NULL, code text,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ops_content_access (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 actor_id text NOT NULL, tenant_id text, user_id text, resource text NOT NULL,
 target_id text, reason text NOT NULL, status text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ops_content_access_scope_time
 ON ops_content_access(tenant_id,created_at DESC);
"""


class OpsStore:
    def __init__(self, directory: Directory) -> None:
        self.directory = directory

    def migrate(self) -> None:
        with self.directory.connection() as conn:
            conn.execute(SCHEMA)

    @staticmethod
    def scope(actor: Actor) -> tuple[bool, str | None]:
        return actor.role == "platform_admin", actor.tenant_id

    def admit(
        self,
        actor: Actor,
        command: Command,
        *,
        connection: Connection[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        fingerprint = hashlib.sha256(
            json.dumps(command.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with (
            nullcontext(connection)
            if connection is not None
            else self.directory.connection() as conn
        ):
            inserted = conn.execute(
                "INSERT INTO "
                "ops_commands(id,actor_id,tenant_id,resource,action,target_id,fingerprint,status)"
                " "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'submitted') ON CONFLICT(id) DO NOTHING RETURNING *",
                (
                    command.command_id,
                    actor.id,
                    actor.tenant_id,
                    command.resource,
                    command.action,
                    command.target_id,
                    fingerprint,
                ),
            ).fetchone()
            if inserted:
                return dict(inserted), True
            row = conn.execute(
                "SELECT * FROM ops_commands WHERE id=%s", (command.command_id,)
            ).fetchone()
            assert row is not None
            if row["actor_id"] != actor.id or row["tenant_id"] != actor.tenant_id:
                raise PermissionError("Command belongs to a different actor")
            if row["fingerprint"] != fingerprint:
                raise ValueError("Command identifier conflict")
            return dict(row), False

    def finish(
        self,
        command_id: str,
        status: str,
        result: dict[str, Any],
        *,
        connection: Connection[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with (
            nullcontext(connection)
            if connection is not None
            else self.directory.connection() as conn
        ):
            row = conn.execute(
                "UPDATE ops_commands SET status=%s,result=%s,updated_at=now() WHERE id=%s "
                "RETURNING *",
                (status, Jsonb(result), command_id),
            ).fetchone()
            assert row is not None
            return row

    def commands(self, actor: Actor, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        with self.directory.connection() as conn:
            count = conn.execute(
                "SELECT count(*) AS n FROM ops_commands WHERE (%s OR tenant_id=%s)",
                self.scope(actor),
            ).fetchone()
            assert count is not None
            rows = conn.execute(
                "SELECT id,actor_id,tenant_id,resource,action,target_id,status,result,created_at,"
                "updated_at "
                "FROM ops_commands WHERE (%s OR tenant_id=%s) ORDER BY created_at DESC LIMIT %s "
                "OFFSET %s",
                (*self.scope(actor), limit, offset),
            ).fetchall()
        return page(rows, count["n"])

    def command_record(self, actor: Actor, command_id: str) -> dict[str, Any] | None:
        with self.directory.connection() as conn:
            return conn.execute(
                "SELECT id,actor_id,tenant_id,resource,action,target_id,status,result,"
                "created_at,updated_at FROM ops_commands WHERE id=%s AND (%s OR tenant_id=%s)",
                (command_id, *self.scope(actor)),
            ).fetchone()

    def records(
        self, actor: Actor, resource: str, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        with self.directory.connection() as conn:
            args = (resource, *self.scope(actor))
            count = conn.execute(
                "SELECT count(*) AS n FROM ops_records WHERE resource=%s AND (%s OR tenant_id=%s)",
                args,
            ).fetchone()
            assert count is not None
            rows = conn.execute(
                "SELECT * FROM ops_records WHERE resource=%s AND (%s OR tenant_id=%s) "
                "ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                (*args, limit, offset),
            ).fetchall()
        return page(rows, count["n"])

    def save_record(
        self,
        actor: Actor,
        resource: str,
        record_id: str,
        payload: dict[str, Any],
        expected_version: int | str | None,
        *,
        connection: Connection[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with (
            nullcontext(connection)
            if connection is not None
            else self.directory.connection() as conn
        ):
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (resource + ":" + record_id,),
            )
            old = conn.execute(
                "SELECT * FROM ops_records WHERE resource=%s AND id=%s FOR UPDATE",
                (resource, record_id),
            ).fetchone()
            if old and actor.role != "platform_admin" and old["tenant_id"] != actor.tenant_id:
                raise PermissionError("Record is outside your tenant")
            if (
                old
                and old["version"] != expected_version
                or not old
                and expected_version not in (None, 0)
            ):
                raise ValueError("Record version conflict")
            row = conn.execute(
                "INSERT INTO ops_records(resource,id,tenant_id,payload,updated_by) VALUES "
                "(%s,%s,%s,%s,%s) "
                "ON CONFLICT(resource,id) DO UPDATE SET "
                "payload=EXCLUDED.payload,version=ops_records.version+1,"
                "updated_by=EXCLUDED.updated_by,updated_at=now() RETURNING *",
                (resource, record_id, actor.tenant_id, Jsonb(payload), actor.id),
            ).fetchone()
            assert row is not None
            return row

    def observe(self, metric: str, tenant_id: str | None, value: float, threshold: float) -> None:
        with self.directory.connection() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (metric + ":" + str(tenant_id),),
            )
            conn.execute(
                "INSERT INTO ops_samples(metric,tenant_id,value) VALUES (%s,%s,%s)",
                (metric, tenant_id, value),
            )
            old = conn.execute(
                "SELECT * FROM ops_alerts WHERE metric=%s AND tenant_id IS NOT DISTINCT FROM %s "
                "AND state!='resolved' FOR UPDATE",
                (metric, tenant_id),
            ).fetchone()
            if value > threshold:
                if old:
                    conn.execute(
                        "UPDATE ops_alerts SET "
                        "value=%s,threshold=%s,occurrences=occurrences+1,last_seen=now() WHERE "
                        "id=%s",
                        (value, threshold, old["id"]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO ops_alerts(id,metric,tenant_id,state,value,threshold) VALUES"
                        " (%s,%s,%s,'open',%s,%s)",
                        (str(uuid4()), metric, tenant_id, value, threshold),
                    )
            elif old:
                conn.execute(
                    "UPDATE ops_alerts SET "
                    "state='resolved',value=%s,last_seen=now(),resolved_at=now() WHERE id=%s",
                    (value, old["id"]),
                )

    def alerts(self, actor: Actor) -> list[dict[str, Any]]:
        with self.directory.connection() as conn:
            return conn.execute(
                "SELECT * FROM ops_alerts WHERE (%s OR tenant_id=%s) ORDER BY last_seen DESC "
                "LIMIT 100",
                self.scope(actor),
            ).fetchall()

    def update_alert(
        self,
        actor: Actor,
        alert_id: str,
        action: str,
        note: str | None,
        *,
        silence_minutes: int = 30,
        connection: Connection[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with (
            nullcontext(connection)
            if connection is not None
            else self.directory.connection() as conn
        ):
            row = conn.execute(
                "SELECT * FROM ops_alerts WHERE id=%s AND (%s OR tenant_id=%s) FOR UPDATE",
                (alert_id, *self.scope(actor)),
            ).fetchone()
            if not row:
                raise PermissionError("Alert unavailable")
            if action == "acknowledge":
                updated = conn.execute(
                    "UPDATE ops_alerts SET owner_id=%s,note=%s WHERE id=%s RETURNING *",
                    (actor.id, note, alert_id),
                ).fetchone()
                assert updated is not None
                return updated
            if action == "silence" and 1 <= silence_minutes <= 1440:
                updated = conn.execute(
                    "UPDATE ops_alerts SET silenced_until=now()+(%s * interval '1 "
                    "minute'),note=%s WHERE id=%s RETURNING *",
                    (silence_minutes, note, alert_id),
                ).fetchone()
                assert updated is not None
                return updated
            raise ValueError("Alert closure requires an observed recovery")
