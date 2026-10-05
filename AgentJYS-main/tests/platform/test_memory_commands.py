"""Command retries must obey current P3 validity and the current business owner."""

from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from test_directory import directory  # noqa: F401

from aether_platform.memory import MemoryCommand, MemoryService, build_command
from aether_platform.p3 import P3Error


def test_database_timestamp_is_normalized_to_p3_contract():
    from aether_agent_memory.remember.contracts.models import RememberRequest

    p3 = Mock()
    p3.selection.return_value = {"user_id": "a1"}
    _, body, _ = build_command(
        p3,
        MemoryCommand(command_id="time1", action="save", params={"text": "fact"}),
        "2026-10-05T08:00:00.123456+00:00",
    )
    assert RememberRequest.model_validate(body).source.occurred_at == "2026-10-05T08:00:00.123Z"


def test_cached_recall_is_revalidated_and_history_does_not_restore_deleted_text(directory):  # noqa: F811
    db, _, _ = directory
    actor = db.authenticate("https://id.test", "a1")
    p3 = Mock(actor=actor)
    p3.selection.return_value = {"user_id": actor.id}
    p3.call.return_value = {"recall_id": "r1", "rendered_context": "private reference"}
    command = MemoryCommand(command_id="retry1", action="recall", params={"text": "question"})
    service = MemoryService(db)
    assert service.run(p3, command)["result"]["recall_id"] == "r1"
    p3.call.side_effect = P3Error("RESULT_INVALIDATED")
    with pytest.raises(P3Error, match="RESULT_INVALIDATED"):
        service.run(p3, command)
    p3.call.assert_called_with("GET", "/p3/recalls/r1/result")


def test_stable_command_cannot_be_reused_by_another_user(directory):  # noqa: F811
    db, _, _ = directory
    p3 = Mock(actor=db.authenticate("https://id.test", "a1"))
    p3.selection.return_value = {"user_id": "a1"}
    p3.call.return_value = {"saved": True}
    service = MemoryService(db)
    command = MemoryCommand(command_id="owned1", action="save", params={"text": "owned"})
    service.run(p3, command)
    p3.actor = db.authenticate("https://id.test", "a2")
    with pytest.raises(HTTPException) as exc:
        service.run(p3, command)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        service.retry(p3, "owned1")
    assert exc.value.status_code == 404


def test_retention_omission_does_not_clear_existing_expiry():
    _, body, _ = build_command(
        Mock(),
        MemoryCommand(
            command_id="retain",
            action="retention",
            memory_id="m1",
            params={"expected_version": 1, "expected_object_revision": 1, "reason": "edit"},
        ),
        "2026-10-05T00:00:00.000Z",
    )
    assert "expires_at" not in body


def test_identity_projection_versions_tenant_changes_and_all_disabled(
    directory, tmp_path, monkeypatch  # noqa: F811
):
    import importlib.util
    from pathlib import Path

    import yaml

    spec = importlib.util.spec_from_file_location(
        "prepare_projection", Path("scripts/platform/prepare_p3_lab.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db, _, _ = directory
    monkeypatch.setattr(module, "Directory", lambda dsn: db)
    config = {"database_dsn": "not-used", "issuer": "https://id.test"}
    module.publish_identities(config, tmp_path)

    def read():
        return yaml.safe_load((tmp_path / "identities.yaml").read_text())

    first = read()["revision"]
    module.publish_identities(config, tmp_path)
    assert read()["revision"] == first
    with db.connection() as conn:
        conn.execute("UPDATE tenants SET enabled=false WHERE id='b'")
    module.publish_identities(config, tmp_path)
    assert read()["revision"] == first + 1
    with db.connection() as conn:
        conn.execute("UPDATE users SET enabled=false")
    module.publish_identities(config, tmp_path)
    assert read()["revision"] == first + 2
    assert read()["jwt_issuers"] == []


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "scope", "selection", "refs", "source"])
def test_ui_cannot_supply_scope_or_source(field):
    with pytest.raises(HTTPException) as exc:
        build_command(
            Mock(),
            MemoryCommand(command_id="x", action="save", params={"text": "x", field: "forged"}),
            "2026-10-05T00:00:00Z",
        )
    assert exc.value.status_code == 422
