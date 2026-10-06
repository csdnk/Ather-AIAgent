"""Durable ops records, CAS edits and an at-most-once external command handoff."""

import hashlib
import json
from contextlib import nullcontext
from uuid import uuid4

from psycopg.types.json import Jsonb

from aether_platform.operations.models import Command, page

SCHEMA = """
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
"""


class OpsStore:
    def __init__(self, directory):
        self.directory = directory

    def migrate(self):
        with self.directory.connection() as conn:
            conn.execute(SCHEMA)

    @staticmethod
    def scope(actor):
        return actor.role == "platform_admin", actor.tenant_id

    def admit(self, actor, command: Command, *, connection=None):
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
            if row["actor_id"] != actor.id or row["tenant_id"] != actor.tenant_id:
                raise PermissionError("Command belongs to a different actor")
            if row["fingerprint"] != fingerprint:
                raise ValueError("Command identifier conflict")
            return dict(row), False

    def finish(self, command_id, status, result, *, connection=None):
        with (
            nullcontext(connection)
            if connection is not None
            else self.directory.connection() as conn
        ):
            return conn.execute(
                "UPDATE ops_commands SET status=%s,result=%s,updated_at=now() WHERE id=%s "
                "RETURNING *",
                (status, Jsonb(result), command_id),
            ).fetchone()

    def commands(self, actor, limit=50, offset=0):
        with self.directory.connection() as conn:
            total = conn.execute(
                "SELECT count(*) AS n FROM ops_commands WHERE (%s OR tenant_id=%s)",
                self.scope(actor),
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT id,actor_id,tenant_id,resource,action,target_id,status,result,created_at,"
                "updated_at "
                "FROM ops_commands WHERE (%s OR tenant_id=%s) ORDER BY created_at DESC LIMIT %s "
                "OFFSET %s",
                (*self.scope(actor), limit, offset),
            ).fetchall()
        return page(rows, total)

    def command_record(self, actor, command_id):
        with self.directory.connection() as conn:
            return conn.execute(
                "SELECT id,actor_id,tenant_id,resource,action,target_id,status,result,"
                "created_at,updated_at FROM ops_commands WHERE id=%s AND (%s OR tenant_id=%s)",
                (command_id, *self.scope(actor)),
            ).fetchone()

    def records(self, actor, resource, limit=50, offset=0):
        with self.directory.connection() as conn:
            args = (resource, *self.scope(actor))
            total = conn.execute(
                "SELECT count(*) AS n FROM ops_records WHERE resource=%s AND (%s OR tenant_id=%s)",
                args,
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT * FROM ops_records WHERE resource=%s AND (%s OR tenant_id=%s) "
                "ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                (*args, limit, offset),
            ).fetchall()
        return page(rows, total)

    def save_record(
        self, actor, resource, record_id, payload, expected_version, *, connection=None
    ):
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
            return conn.execute(
                "INSERT INTO ops_records(resource,id,tenant_id,payload,updated_by) VALUES "
                "(%s,%s,%s,%s,%s) "
                "ON CONFLICT(resource,id) DO UPDATE SET "
                "payload=EXCLUDED.payload,version=ops_records.version+1,"
                "updated_by=EXCLUDED.updated_by,updated_at=now() RETURNING *",
                (resource, record_id, actor.tenant_id, Jsonb(payload), actor.id),
            ).fetchone()

    def observe(self, metric, tenant_id, value, threshold):
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

    def alerts(self, actor):
        with self.directory.connection() as conn:
            return conn.execute(
                "SELECT * FROM ops_alerts WHERE (%s OR tenant_id=%s) ORDER BY last_seen DESC "
                "LIMIT 100",
                self.scope(actor),
            ).fetchall()

    def update_alert(self, actor, alert_id, action, note, *, silence_minutes=30, connection=None):
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
                return conn.execute(
                    "UPDATE ops_alerts SET owner_id=%s,note=%s WHERE id=%s RETURNING *",
                    (actor.id, note, alert_id),
                ).fetchone()
            if action == "silence" and 1 <= silence_minutes <= 1440:
                return conn.execute(
                    "UPDATE ops_alerts SET silenced_until=now()+(%s * interval '1 "
                    "minute'),note=%s WHERE id=%s RETURNING *",
                    (silence_minutes, note, alert_id),
                ).fetchone()
            raise ValueError("Alert closure requires an observed recovery")
