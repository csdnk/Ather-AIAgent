from contextlib import contextmanager
from types import SimpleNamespace

from aether_platform.operations.presentation import attach_names


def test_names_are_scoped_and_identifiers_preserved():
    class Connection:
        def execute(self, query, params):
            assert params[-2:] == (False, "tenant-a")
            if "FROM tenants" in query:
                return SimpleNamespace(
                    fetchall=lambda: [{"id": "tenant-a", "name": "星海科技（演示）"}]
                )
            return SimpleNamespace(fetchall=lambda: [{"id": "user-a", "display_name": "陈晨"}])

    class Directory:
        @contextmanager
        def connection(self):
            yield Connection()

    data = {
        "items": [
            {"tenant_id": "tenant-a", "user_id": "user-a", "status": "complete"},
            {"tenant_id": "tenant-b", "user_id": "user-b"},
        ]
    }
    attach_names(
        data, "requests", SimpleNamespace(role="tenant_admin", tenant_id="tenant-a"), Directory()
    )
    assert data["items"][0]["tenant_name"] == "星海科技（演示）"
    assert data["items"][0]["user_name"] == "陈晨"
    assert data["items"][0]["user_id"] == "user-a"
    assert "user_name" not in data["items"][1]
    assert "tenant_name" not in data["items"][1]
