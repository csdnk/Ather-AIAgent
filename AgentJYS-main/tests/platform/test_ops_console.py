"""Console queries exercise actual SQL and HTTP authorization without external services."""

import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aether_platform.auth.ruoyi import RuoyiActor
from aether_platform.directory import AccessDeniedError
from aether_platform.operations.api import install_operations
from aether_platform.p3 import P3Client, P3Error

ACTOR = RuoyiActor(
    "admin", "issuer", "subject", "a", "tenant_admin", 1, ("aether:ops:read", "aether:content:read")
)


class Database:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = lambda cursor, row: dict(
            zip([c[0] for c in cursor.description], row, strict=True)
        )
        self.db.executescript("""
        CREATE TABLE users(id,username,display_name,tenant_id,enabled,role);
        CREATE TABLE tenants(id,name);
        INSERT INTO tenants VALUES ('a','Company A'),('b','Company B');
        INSERT INTO users VALUES ('u1','alice','Alice','a',1,'user'),
          ('u2','bob','Bob','a',1,'user'),('u3','alice-b','Alice B','b',1,'user');
        CREATE TABLE conversations(id,user_id,tenant_id,title,archived,created_at,updated_at);
        INSERT INTO conversations VALUES
          ('c1','u1','a','private title',0,'2026-01-01','2026-01-02'),
          ('c2','u2','a','Bob title',0,'2026-01-01','2026-01-02');
        CREATE TABLE chat_turns(id,conversation_id,input,output,status,phase,created_at,
          started_at,first_token_at,memory_evidence);
        INSERT INTO chat_turns VALUES ('t1','c1','private question','actual answer','complete',
          'complete','2026-01-01','2026-01-01',NULL,
          '{"status":"saved","recall_id":"r1","memories":[{"memory_id":"m1","version":1}],
          "source_excerpts":[{"content":"do not revive cached source"}],"token":"secret"}');
        CREATE TABLE ops_content_access(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id,tenant_id,
          user_id,resource,target_id,reason,status,created_at DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE ops_commands(id,actor_id,tenant_id,resource,action,target_id,status,result,
          created_at,updated_at);
        INSERT INTO ops_commands VALUES ('cmd1','admin','a','tasks','cancel','job1','succeeded',
          '{}','2026-01-01','2026-01-01');
        """)

    @contextmanager
    def connection(self):
        yield self
        self.db.commit()

    def execute(self, query, parameters=()):
        parameters = tuple(p.isoformat() if isinstance(p, datetime) else p for p in parameters)
        return self.db.execute(query.replace("%s", "?").replace(" ILIKE ", " LIKE "), parameters)


class Verifier:
    current = ACTOR

    def verify_ruoyi_actor(self, token):
        if token != "valid":
            raise AccessDeniedError("Invalid token")
        return self.current


@pytest.fixture
def console():
    from aether_platform.operations.console import Console

    db = Database()
    service = Console({}, db)
    verifier = Verifier()
    app = FastAPI()
    install_operations(app, {}, db, verifier, service=object(), console=service)
    return TestClient(app), db, service, verifier


HEADERS = {"Authorization": "Bearer valid"}


def test_console_users_filters_in_database_and_returns_true_total(console):
    client, _, _, _ = console
    response = client.get("/platform-ops/v1/console/users?q=ali&limit=1", headers=HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 1
    assert result["items"][0]["id"] == "u1"
    assert result["items"][0]["tenant_name"] == "Company A"
    assert result["tenant_choices"] == [{"id": "a", "name": "Company A"}]
    result = client.get("/platform-ops/v1/console/users?limit=1&offset=1", headers=HEADERS).json()
    assert result["total"] == 2 and result["items"][0]["id"] == "u2"
    result = client.get("/platform-ops/v1/console/users?q=' OR 1=1 --", headers=HEADERS).json()
    assert result["total"] == 0


def test_console_permission_revocation_and_cross_tenant_denied(console):
    client, db, _, verifier = console
    assert (
        client.get("/platform-ops/v1/console/users?tenant_id=b", headers=HEADERS).status_code == 403
    )
    assert (
        client.get("/platform-ops/v1/console/conversations?user_id=u3", headers=HEADERS).status_code
        == 404
    )
    verifier.current = replace(ACTOR, permissions=("aether:ops:read",))
    assert (
        client.get(
            "/platform-ops/v1/console/conversations/c1?user_id=u1", headers=HEADERS
        ).status_code
        == 403
    )
    verifier.current = replace(ACTOR, role="user")
    assert client.get("/platform-ops/v1/console/users", headers=HEADERS).status_code == 403
    rows = db.execute("SELECT * FROM ops_content_access").fetchall()
    assert any(row["status"] == "denied" for row in rows)


def test_conversation_details_are_owner_scoped_and_do_not_replay_cached_sources(console):
    client, db, _, _ = console
    assert (
        client.get(
            "/platform-ops/v1/console/conversations/c1?user_id=u2", headers=HEADERS
        ).status_code
        == 404
    )
    response = client.get("/platform-ops/v1/console/conversations/c1?user_id=u1", headers=HEADERS)
    assert response.status_code == 200
    turn = response.json()["turns"][0]
    assert turn["input"] == "private question" and turn["output"] == "actual answer"
    assert turn["memory_ids"] == ["m1"] and turn["recall_id"] == "r1"
    assert "cached source" not in response.text and "secret" not in response.text
    history = str(db.execute("SELECT * FROM ops_content_access").fetchall())
    assert (
        "private question" not in history
        and "actual answer" not in history
        and "secret" not in history
    )


def test_memory_proxy_resolves_tenant_and_does_not_fabricate_total(console):
    client, _, service, _ = console

    def remote(actor, token, path, params=None):
        assert actor.id == "admin" and token == "valid"
        assert params == {"tenant_id": "a", "user_id": "u1", "limit": 1, "cursor": "scoped"}
        return {"items": [{"ref": {"id": "m1"}}], "next_cursor": "more", "status": "ok"}

    service.p3_read = remote
    response = client.get(
        "/platform-ops/v1/console/memories?user_id=u1&tenant_id=b&limit=1&cursor=scoped",
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert "total" not in response.json() and response.json()["next_cursor"] == "more"


def test_upstream_timeout_and_gone_are_explicit_and_audited(console):
    client, db, service, _ = console

    def remote(*args, **kwargs):
        raise P3Error("CONNECTION_UNCONFIRMED")

    service.p3_read = remote
    response = client.get("/platform-ops/v1/console/memories/m1?user_id=u1", headers=HEADERS)
    assert (
        response.status_code == 503
        and response.json()["detail"]["code"] == "CONNECTION_UNCONFIRMED"
    )
    assert db.execute("SELECT status FROM ops_content_access").fetchone()["status"] == "unavailable"


def test_history_merges_commands_and_accesses_under_tenant_scope(console):
    client, _, _, _ = console
    client.get("/platform-ops/v1/console/conversations/c1?user_id=u1", headers=HEADERS)
    response = client.get("/platform-ops/v1/console/history?limit=1", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2 and len(data["items"]) == 1
    assert data["items"][0]["kind"] == "content_access"


def test_admin_transport_preserves_admin_identity_and_is_read_only():
    observed = []

    def remote(request):
        observed.append(request)
        return httpx.Response(200, json={"items": []})

    with P3Client(
        "https://p3.test", "valid", ACTOR, admin_read=True, transport=httpx.MockTransport(remote)
    ) as p3:
        p3.call("GET", "/p3/admin/memories", params={"tenant_id": "a", "user_id": "u1"})
        for method, path in [("GET", "/p3/memories"), ("POST", "/p3/admin/memories")]:
            with pytest.raises(P3Error):
                p3.call(method, path)
    assert len(observed) == 1
    assert observed[0].headers["X-P3-Tenant"] == "a"
    assert observed[0].headers["Authorization"] == "Bearer valid"


def test_invalid_path_id_cannot_be_written_to_audit(console):
    client, db, _, _ = console
    response = client.get(
        "/platform-ops/v1/console/conversations/arbitrary%20content?user_id=u1", headers=HEADERS
    )
    assert response.status_code == 422
    assert db.execute("SELECT count(*) AS n FROM ops_content_access").fetchone()["n"] == 0


def test_platform_wildcard_role_can_read_both_tenants(console):
    client, _, _, verifier = console
    verifier.current = replace(ACTOR, role="platform_admin", tenant_id=None, permissions=("*:*:*",))
    data = client.get("/platform-ops/v1/console/users?q=alice", headers=HEADERS).json()
    assert data["total"] == 2
    assert {row["tenant_id"] for row in data["items"]} == {"a", "b"}


def test_details_fail_closed_when_access_audit_cannot_be_written(console):
    client, db, _, _ = console
    db.execute("DROP TABLE ops_content_access")
    client = TestClient(client.app, raise_server_exceptions=False)
    response = client.get("/platform-ops/v1/console/conversations/c1?user_id=u1", headers=HEADERS)
    assert response.status_code == 500 and "actual answer" not in response.text


@pytest.mark.parametrize(
    "code,status",
    [
        ("FORBIDDEN", 403),
        ("MEMORY_GONE", 410),
        ("RESULT_INVALIDATED", 410),
        ("INVALID_CURSOR", 422),
        ("INVALID_ARGUMENT", 422),
    ],
)
def test_content_errors_preserve_permissions_lifecycle_and_cursor_status(console, code, status):
    client, _, service, _ = console

    def remote(*args, **kwargs):
        raise P3Error(code)

    service.p3_read = remote
    response = client.get("/platform-ops/v1/console/recalls/r1?user_id=u1", headers=HEADERS)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def test_history_includes_display_names_for_target_user(console):
    client, _, _, _ = console
    client.get("/platform-ops/v1/console/conversations/c1?user_id=u1", headers=HEADERS)
    row = client.get("/platform-ops/v1/console/history", headers=HEADERS).json()["items"][0]
    assert row["user_name"] == "Alice" and row["tenant_name"] == "Company A"


def test_tenant_name_search_and_no_store_header(console):
    client, _, _, _ = console
    response = client.get("/platform-ops/v1/console/users?q=Company", headers=HEADERS)
    assert response.json()["total"] == 2
    assert response.headers["Cache-Control"] == "no-store"


def test_diagnostics_forwards_cursor_instead_of_returning_only_first_page(console):
    client, _, service, verifier = console
    verifier.current = replace(ACTOR, role="platform_admin", tenant_id=None)
    service.business_observations = lambda actor: {"items": [], "status": "not_collected"}

    def remote(actor, token, path, params=None):
        assert params == {"limit": 17, "cursor": "next-page"}
        return {"tasks": {"items": [], "next_cursor": None}, "status": "ok"}

    service.p3_read = remote
    response = client.get(
        "/platform-ops/v1/console/diagnostics?limit=17&cursor=next-page", headers=HEADERS
    )
    assert response.status_code == 200


def test_placement_query_is_platform_only_and_skips_unrelated_business_queries(console):
    client, _, service, verifier = console
    calls = []

    def remote(actor, token, path, params=None):
        calls.append((path, params))
        return {"items": [], "total": 0, "status": "available"}

    service.p3_read = remote
    path = "/platform-ops/v1/console/diagnostics?kind=placement&status=unconfirmed&limit=20"
    assert client.get(path, headers=HEADERS).status_code == 403
    assert not calls
    verifier.current = replace(ACTOR, role="platform_admin", tenant_id=None)
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 200 and response.json()["total"] == 0
    assert calls == [
        ("/p3/admin/diagnostics", {"kind": "placement", "status": "unconfirmed", "limit": 20})
    ]
    assert client.get(path.replace("unconfirmed", "invented"), headers=HEADERS).status_code == 422


def test_denied_cross_tenant_attempt_does_not_enrich_foreign_user_name(console):
    client, _, _, _ = console
    client.get("/platform-ops/v1/console/conversations?user_id=u3", headers=HEADERS)
    response = client.get("/platform-ops/v1/console/history", headers=HEADERS)
    assert "Alice B" not in response.text


def test_business_filters_are_forwarded_without_expanding_target_scope(console):
    client, _, service, verifier = console
    calls = []

    def remote(actor, token, path, params=None):
        calls.append((path, params))
        return {"items": [], "status": "ok"}

    service.p3_read = remote
    response = client.get(
        "/platform-ops/v1/console/memories?user_id=u1&kind=working&status=active", headers=HEADERS
    )
    assert response.status_code == 200
    assert calls[-1] == (
        "/p3/admin/memories",
        {"tenant_id": "a", "user_id": "u1", "limit": 50, "kind": "working", "status": "active"},
    )
    response = client.get(
        "/platform-ops/v1/console/memories?user_id=u1&include_summary=false&collapse_duplicates=true",
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert calls[-1][1] == {
        "tenant_id": "a",
        "user_id": "u1",
        "limit": 50,
        "include_summary": False,
        "collapse_duplicates": True,
    }
    verifier.current = replace(ACTOR, role="platform_admin", tenant_id=None)
    service.business_observations = lambda actor: {"items": [], "status": "not_collected"}
    response = client.get(
        "/platform-ops/v1/console/diagnostics?flow=remember&state=failed", headers=HEADERS
    )
    assert response.status_code == 200
    assert calls[-1] == (
        "/p3/admin/diagnostics",
        {"limit": 50, "flow": "remember", "state": "failed"},
    )


def test_conversation_status_and_time_filters_apply_to_total_and_page(console):
    client, _, _, _ = console
    path = "/platform-ops/v1/console/conversations"
    for params, expected in [
        (
            {
                "user_id": "u1",
                "status": "complete",
                "from": "2025-12-01T00:00:00Z",
                "to": "2026-02-01T00:00:00Z",
            },
            1,
        ),
        ({"user_id": "u1", "status": "failed"}, 0),
        ({"user_id": "u1", "from": "2026-02-01T00:00:00Z"}, 0),
    ]:
        response = client.get(path, params=params, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["total"] == expected
        assert len(response.json()["items"]) == expected
    for params in [
        {"from": "not-a-date"},
        {"from": "1700000000"},
        {"from": "2026-01-01T00:00:00"},
        {"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        {"status": "failed' OR 1=1 --"},
    ]:
        assert (
            client.get(path, params={"user_id": "u1", **params}, headers=HEADERS).status_code == 422
        )
