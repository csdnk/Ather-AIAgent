"""Read-only monitor catalogs must retain existing tenant and trace boundaries."""

import json

import yaml
from fastapi.testclient import TestClient
from tests.integration.test_continuous_service import configuration as configuration
from tests.integration.test_continuous_service import eventually, headers, save

from aether_agent_memory.runtime.flows.application import Service


def test_monitor_catalogs_page_and_filter_without_exposing_bodies(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        for index in range(3):
            assert save(
                client, text="private-body-never-in-catalog", operation=f"monitor_{index}"
            ).is_success
        eventually(lambda: client.get("/p3/tasks", headers=headers()).json()["items"])
        first = client.get("/p3/tasks?limit=1", headers=headers()).json()
        assert len(first["items"]) == 1 and first["next_cursor"]
        second = client.get(
            "/p3/tasks", params={"limit": 1, "cursor": first["next_cursor"]}, headers=headers()
        ).json()
        assert first["items"][0]["task_id"] != second["items"][0]["task_id"]
        assert first["items"][0]["trace_id"]
        assert (
            client.get(
                "/p3/tasks", params={"cursor": first["next_cursor"]}, headers=headers("bob")
            ).status_code
            == 400
        )
        state = first["items"][0]["state"]
        filtered = client.get("/p3/tasks", params={"state": state}, headers=headers()).json()
        assert all(t["state"] == state for t in filtered["items"])
        assert "private-body-never-in-catalog" not in json.dumps(first)
        assert client.get("/p3/tasks?limit=101", headers=headers()).status_code == 422
        assert client.get("/p3/tasks?state=bogus", headers=headers()).status_code == 422

        trace_page = client.get("/p3/traces?limit=1", headers=headers()).json()
        assert trace_page["coverage"] == "retained_records_only"
        assert len(trace_page["items"]) == 1 and trace_page["next_before"]
        other = client.get(
            "/p3/traces",
            params={"limit": 1, "before": trace_page["next_before"]},
            headers=headers(),
        ).json()
        assert trace_page["items"][0]["trace_id"] != other["items"][0]["trace_id"]
        trace_id = trace_page["items"][0]["trace_id"]
        logs = client.get(f"/p3/logs/{trace_id}", headers=headers()).json()
        assert logs["records"]
        assert all(row["schema_version"] == "p3/log/2" for row in logs["records"])
        assert "private-body-never-in-catalog" not in json.dumps([trace_page, logs])
        assert client.get("/p3/traces?before=0", headers=headers()).status_code == 422
        business = client.get("/p3/traces?flow=business", headers=headers()).json()["items"]
        assert business
        for trace in business:
            records = client.get(f"/p3/logs/{trace['trace_id']}", headers=headers()).json()[
                "records"
            ]
            assert any(row["flow"] in {"remember", "recall", "operate"} for row in records)
        assert client.get("/p3/traces?flow=bogus", headers=headers()).status_code == 422


def test_monitor_catalogs_enforce_auth_scope_and_cursor_binding(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        assert save(client).is_success
        trace_id = client.get("/p3/traces", headers=headers()).json()["items"][0]["trace_id"]
        for user in ("bob", "eve"):
            assert client.get("/p3/tasks", headers=headers(user)).json()["items"] == []
            assert client.get("/p3/traces", headers=headers(user)).json()["items"] == []
            assert client.get(f"/p3/logs/{trace_id}", headers=headers(user)).json()["records"] == []
        for path in ("/p3/tasks", "/p3/traces"):
            assert client.get(path).status_code == 401
        # Downgrade the already registered identity; no cached permission bypass.
        config = yaml.safe_load(configuration.identity_file.read_text("utf-8"))
        config["revision"] = 2
        config["identities"][0]["principal"]["auth_epoch"] = 2
        config["identities"][0]["principal"]["permissions"] = ["memory:write"]
        configuration.identity_file.write_text(yaml.safe_dump(config), encoding="utf-8")
        eventually(lambda: client.get("/p3/tasks", headers=headers()).status_code == 403)
        for path in ("/p3/tasks", "/p3/traces", f"/p3/logs/{trace_id}"):
            assert client.get(path, headers=headers()).status_code == 403
