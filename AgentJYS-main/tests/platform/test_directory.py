"""Real PostgreSQL checks for the platform's business authorization boundary."""

import importlib.util
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


def test_directory_component_exists():
    assert importlib.util.find_spec("aether_platform.directory") is not None


@pytest.fixture
def directory():
    path = os.environ.get("AETHER_PLATFORM_LAB_CONFIG")
    if not path:
        pytest.skip("explicit isolated PostgreSQL configuration required")
    from aether_platform.directory import Directory

    config = json.loads(Path(path).read_text(encoding="utf-8"))
    from test_demo_seed import module

    module().validate_target(config)
    schema = "test_" + uuid4().hex
    db = Directory(config["database_dsn"], schema=schema)
    db.migrate()
    tenants = [
        {"id": "a", "name": "A", "enabled": True},
        {"id": "b", "name": "B", "enabled": True},
        {"id": "c", "name": "C", "enabled": False},
    ]
    users = [
        {
            "id": uid,
            "subject": uid,
            "issuer": "https://id.test",
            "username": uid,
            "display_name": uid,
            "tenant_id": tenant,
            "role": role,
            "enabled": enabled,
        }
        for uid, tenant, role, enabled in [
            ("root", None, "platform_admin", True),
            ("aa", "a", "tenant_admin", True),
            ("ab", "b", "tenant_admin", True),
            ("a1", "a", "user", True),
            ("a2", "a", "user", True),
            ("b1", "b", "user", True),
            ("off", "a", "user", False),
            ("c1", "c", "user", True),
        ]
    ]
    db.seed("test-fixture", tenants, users)
    yield db, tenants, users
    with psycopg.connect(config["database_dsn"]) as conn:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_seed_is_repeatable_but_never_resets_modified_records(directory):
    db, tenants, users = directory
    root = db.authenticate("https://id.test", "root")
    db.update_user(root, "a1", {"display_name": "manually maintained"}, "edit-1")
    db.seed("test-fixture", tenants, users)
    assert db.get_user(root, "a1")["display_name"] == "manually maintained"
    with pytest.raises(ValueError):
        db.seed("different-dataset", tenants, users)


def test_tenant_admin_lists_only_own_users_and_cannot_read_other_tenant(directory):
    from aether_platform.directory import AccessDeniedError

    db, _, _ = directory
    actor = db.authenticate("https://id.test", "aa")
    assert {u["id"] for u in db.list_users(actor)} == {"aa", "a1", "a2", "off"}
    with pytest.raises(AccessDeniedError):
        db.get_user(actor, "b1")
    with pytest.raises(AccessDeniedError):
        db.list_users(db.authenticate("https://id.test", "a1"))


@pytest.mark.parametrize("subject", ["off", "c1", "missing"])
def test_disabled_user_tenant_and_unknown_subject_fail_closed(directory, subject):
    from aether_platform.directory import AccessDeniedError

    db, _, _ = directory
    with pytest.raises(AccessDeniedError):
        db.authenticate("https://id.test", subject)


def test_admin_cannot_promote_move_or_disable_other_admin(directory):
    from aether_platform.directory import AccessDeniedError

    db, _, _ = directory
    actor = db.authenticate("https://id.test", "aa")
    for target, patch in [
        ("a1", {"role": "tenant_admin"}),
        ("a1", {"tenant_id": "b"}),
        ("ab", {"enabled": False}),
        ("aa", {"enabled": False}),
        ("b1", {"display_name": "attack"}),
    ]:
        with pytest.raises(AccessDeniedError):
            db.update_user(actor, target, patch, uuid4().hex)


def test_disable_rechecks_existing_actor_and_records_one_pending_command(directory):
    from aether_platform.directory import AccessDeniedError

    db, _, _ = directory
    root = db.authenticate("https://id.test", "root")
    previous = db.authenticate("https://id.test", "aa")
    first = db.update_user(root, "aa", {"enabled": False}, "disable-aa")
    retry = db.update_user(root, "aa", {"enabled": False}, "disable-aa")
    assert first == retry
    assert first["sync_status"] == "pending"
    with pytest.raises(AccessDeniedError):
        db.list_users(previous)
    with pytest.raises(ValueError):
        db.update_user(root, "a1", {"enabled": False}, "disable-aa")
    with db.connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM identity_commands WHERE id='disable-aa'"
            ).fetchone()["n"]
            == 1
        )


def test_last_platform_admin_cannot_be_disabled(directory):
    from aether_platform.directory import AccessDeniedError

    db, _, _ = directory
    with pytest.raises(AccessDeniedError):
        db.update_user(
            db.authenticate("https://id.test", "root"), "root", {"enabled": False}, "disable-root"
        )


def test_database_rejects_second_mapping_and_user_without_tenant(directory):
    db, tenants, users = directory
    duplicate = {**users[3], "id": "other", "tenant_id": "b"}
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.seed("test-fixture", tenants, [duplicate])
    with pytest.raises(psycopg.errors.CheckViolation):
        db.seed(
            "test-fixture",
            tenants,
            [{**users[3], "id": "orphan", "subject": "orphan", "tenant_id": None}],
        )
