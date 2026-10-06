"""Real PostgreSQL ownership migration; authority HTTP remains a controlled fixture."""

import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from aether_platform.auth.ruoyi import RuoyiDirectory
from aether_platform.directory import AccessDeniedError


@pytest.fixture
def directory():
    config = os.environ.get("AETHER_RUOYI_TEST_CONFIG")
    if not config:
        pytest.skip("dedicated PostgreSQL test configuration required")
    dsn = json.loads(Path(config).read_text())["database_dsn"]
    schema = "ruoyi_identity_test_" + uuid4().hex[:16]
    directory = RuoyiDirectory(dsn)
    directory.schema = schema
    directory.migrate()
    try:
        yield directory
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_old_business_history_is_preserved_and_mapping_cannot_change(directory):
    with directory.connection() as conn:
        conn.execute("INSERT INTO tenants(id,name,enabled) VALUES ('old-tenant','Old',true)")
        for user_id in ("old-user", "other-user"):
            conn.execute(
                "INSERT INTO users(id,issuer,subject,username,display_name,tenant_id,role,enabled) "
                "VALUES (%s,'old-issuer',%s,%s,%s,'old-tenant','user',true)",
                (user_id, user_id, user_id, user_id),
            )
        conn.execute(
            "CREATE TABLE retained_history(id text PRIMARY KEY,user_id text REFERENCES users(id))"
        )
        conn.execute("INSERT INTO retained_history VALUES ('history-1','old-user')")
    mapping = dict(
        ruoyi_user_id="17",
        ruoyi_tenant_id="2",
        business_user_id="old-user",
        business_tenant_id="old-tenant",
    )
    directory.ensure_ruoyi_user("ruoyi:test", mapping, {})
    with pytest.raises(AccessDeniedError):
        directory.ensure_ruoyi_user("ruoyi:test", {**mapping, "business_user_id": "other-user"}, {})
    with directory.connection() as conn:
        assert (
            conn.execute("SELECT user_id FROM retained_history WHERE id='history-1'").fetchone()[
                "user_id"
            ]
            == "old-user"
        )
        assert (
            conn.execute("SELECT issuer FROM users WHERE id='old-user'").fetchone()["issuer"]
            == "old-issuer"
        )


def test_dynamic_identity_provision_is_idempotent_and_cannot_move_tenant(directory):
    binding = dict(
        ruoyi_user_id="99",
        ruoyi_tenant_id="2",
        business_user_id="ry_user_99",
        business_tenant_id="ry_tenant_2",
    )
    directory.ensure_ruoyi_user("ruoyi:test", binding, {"display_name": "New User"})
    directory.ensure_ruoyi_user("ruoyi:test", binding, {"display_name": "Renamed User"})
    assert directory.mapped_user("ry_user_99")["tenant_id"] == "ry_tenant_2"
    with pytest.raises(AccessDeniedError):
        directory.ensure_ruoyi_user(
            "ruoyi:test", {**binding, "business_tenant_id": "ry_tenant_3"}, {}
        )
    with directory.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 1
