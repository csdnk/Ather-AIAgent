"""Business identity authority. All public operations recheck current authority.

This increment supplies directory reads, profile edits and fail-closed disabling.
Outbox completion and account enable/transfer require the projection worker.
"""

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class AccessDeniedError(ValueError):
    """The current business identity cannot perform this operation."""


@dataclass(frozen=True)
class Actor:
    id: str
    issuer: str
    subject: str
    tenant_id: str | None
    role: str
    version: int


SCHEMA = """
CREATE TABLE IF NOT EXISTS fixture_installation (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton), dataset text NOT NULL
);
CREATE TABLE IF NOT EXISTS tenants (
    id text PRIMARY KEY, name text NOT NULL, enabled boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id text PRIMARY KEY, issuer text NOT NULL, subject text NOT NULL,
    username text NOT NULL UNIQUE, display_name text NOT NULL,
    tenant_id text REFERENCES tenants(id),
    role text NOT NULL CHECK (role IN ('platform_admin','tenant_admin','user')),
    enabled boolean NOT NULL, version integer NOT NULL DEFAULT 1,
    sync_status text NOT NULL DEFAULT 'identity_only',
    UNIQUE(issuer, subject),
    CHECK ((role = 'platform_admin' AND tenant_id IS NULL)
        OR (role IN ('tenant_admin','user') AND tenant_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS identity_commands (
    id text PRIMARY KEY, actor_id text NOT NULL REFERENCES users(id),
    target_id text NOT NULL REFERENCES users(id), fingerprint text NOT NULL,
    payload jsonb NOT NULL, result jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    command_id text NOT NULL UNIQUE REFERENCES identity_commands(id),
    actor_id text NOT NULL, target_id text NOT NULL,
    action text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
"""


class Directory:
    def __init__(self, dsn: str, *, schema: str = "aether_platform") -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ValueError("Invalid database schema")
        self.dsn, self.schema = dsn, schema

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)))
            yield conn

    def migrate(self) -> None:
        with self.connection() as conn:
            conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema))
            )
            conn.execute(SCHEMA)

    def seed(
        self, dataset: str, tenants: list[dict[str, Any]], users: list[dict[str, Any]]
    ) -> None:
        """Lab bootstrap only; repeated imports preserve all maintained rows."""
        with self.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(190102026)")
            installed = conn.execute("SELECT dataset FROM fixture_installation").fetchone()
            if installed and installed["dataset"] != dataset:
                raise ValueError("Database belongs to a different fixture dataset")
            if not installed:
                if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                    raise ValueError("Refusing to seed a non-fixture directory")
                conn.execute("INSERT INTO fixture_installation(dataset) VALUES (%s)", (dataset,))
            for tenant in tenants:
                conn.execute(
                    "INSERT INTO tenants(id,name,enabled) VALUES (%s,%s,%s) "
                    "ON CONFLICT (id) DO NOTHING",
                    (tenant["id"], tenant["name"], tenant["enabled"]),
                )
            for user in users:
                conn.execute(
                    "INSERT INTO users(id,issuer,subject,username,display_name,"
                    "tenant_id,role,enabled) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (id) DO NOTHING",
                    tuple(
                        user[k]
                        for k in (
                            "id",
                            "issuer",
                            "subject",
                            "username",
                            "display_name",
                            "tenant_id",
                            "role",
                            "enabled",
                        )
                    ),
                )

    @staticmethod
    def _authenticate(conn: psycopg.Connection[dict[str, Any]], issuer: str, subject: str) -> Actor:
        row = conn.execute(
            "SELECT u.* FROM users u LEFT JOIN tenants t ON u.tenant_id=t.id "
            "WHERE u.issuer=%s AND u.subject=%s AND u.enabled "
            "AND (u.tenant_id IS NULL OR t.enabled)",
            (issuer, subject),
        ).fetchone()
        if not row:
            raise AccessDeniedError("Identity is unavailable")
        return Actor(**{k: row[k] for k in Actor.__dataclass_fields__})

    def authenticate(self, issuer: str, subject: str) -> Actor:
        with self.connection() as conn:
            return self._authenticate(conn, issuer, subject)

    def _admin(self, conn: psycopg.Connection[dict[str, Any]], actor: Actor) -> Actor:
        current = self._authenticate(conn, actor.issuer, actor.subject)
        if current != actor or current.role not in {"platform_admin", "tenant_admin"}:
            raise AccessDeniedError("Administration is unavailable")
        return current

    def list_users(self, actor: Actor) -> list[dict[str, Any]]:
        with self.connection() as conn:
            actor = self._admin(conn, actor)
            return conn.execute(
                "SELECT id,username,display_name,tenant_id,role,enabled,"
                "version,sync_status FROM users "
                "WHERE (%s OR tenant_id=%s) ORDER BY username",
                (actor.role == "platform_admin", actor.tenant_id),
            ).fetchall()

    def get_user(self, actor: Actor, user_id: str) -> dict[str, Any]:
        for row in self.list_users(actor):
            if row["id"] == user_id:
                return row
        raise AccessDeniedError("User is unavailable")

    def update_user(
        self, actor: Actor, user_id: str, changes: dict[str, Any], command_id: str
    ) -> dict[str, Any]:
        if not changes or set(changes) - {"display_name", "enabled"}:
            raise AccessDeniedError("Unsupported account change")
        if "display_name" in changes and (
            not isinstance(changes["display_name"], str)
            or not 1 <= len(changes["display_name"].strip()) <= 120
        ):
            raise ValueError("Invalid display name")
        if "enabled" in changes and changes["enabled"] is not False:
            raise AccessDeniedError("Enabling requires verified identity projections")
        if not command_id or len(command_id) > 128:
            raise ValueError("Invalid command identifier")
        fingerprint = hashlib.sha256(
            json.dumps([user_id, changes], sort_keys=True).encode()
        ).hexdigest()
        with self.connection() as conn:
            # Serialize business writes, including last-admin checks and bootstrap.
            conn.execute("SELECT pg_advisory_xact_lock(190102026)")
            actor = self._admin(conn, actor)
            target = conn.execute(
                "SELECT * FROM users WHERE id=%s FOR UPDATE", (user_id,)
            ).fetchone()
            if not target or (
                actor.role == "tenant_admin"
                and (target["tenant_id"] != actor.tenant_id or target["role"] != "user")
            ):
                raise AccessDeniedError("User is unavailable")
            old = conn.execute(
                "SELECT * FROM identity_commands WHERE id=%s", (command_id,)
            ).fetchone()
            if old:
                if old["actor_id"] != actor.id or old["fingerprint"] != fingerprint:
                    raise ValueError("Command identifier already used")
                return cast(dict[str, Any], old["result"])
            if target["role"] == "platform_admin" and changes.get("enabled") is False:
                counted = conn.execute(
                    "SELECT count(*) AS n FROM users WHERE role='platform_admin' AND enabled"
                ).fetchone()
                assert counted is not None
                if counted["n"] <= 1:
                    raise AccessDeniedError("The last platform administrator must remain enabled")
            name = changes.get("display_name", target["display_name"])
            enabled = changes.get("enabled", target["enabled"])
            row = conn.execute(
                "UPDATE users SET display_name=%s,enabled=%s,version=version+1,"
                "sync_status='pending' WHERE id=%s RETURNING id,display_name,"
                "enabled,version,sync_status",
                (name, enabled, user_id),
            ).fetchone()
            assert row is not None
            result = {**row, "command_id": command_id}
            conn.execute(
                "INSERT INTO identity_commands(id,actor_id,target_id,fingerprint,payload,result) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (command_id, actor.id, user_id, fingerprint, Jsonb(changes), Jsonb(result)),
            )
            conn.execute(
                "INSERT INTO audit_events(command_id,actor_id,target_id,action) "
                "VALUES (%s,%s,%s,'update_user')",
                (command_id, actor.id, user_id),
            )
            return result
