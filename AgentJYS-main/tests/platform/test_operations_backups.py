import pytest

from aether_platform.operations.backups import BackupExecutor, connection_environment


def test_backup_identifier_cannot_escape_configured_directory(tmp_path):
    executor = BackupExecutor({"operations": {"backup_directory": str(tmp_path)}}, None)
    for name in ("../existing", "C:/existing", "a/b", "a\\b", ""):
        with pytest.raises(ValueError):
            executor.paths(name)


def test_backup_requires_explicit_destination_and_cannot_replace_existing(tmp_path):
    with pytest.raises(ValueError):
        BackupExecutor({}, None).paths("backup-1")
    executor = BackupExecutor({"operations": {"backup_directory": str(tmp_path)}}, None)
    dump, manifest = executor.paths("backup-1")
    assert dump.parent == tmp_path.resolve() and manifest.parent == tmp_path.resolve()


def test_database_credentials_only_in_child_environment():
    env = connection_environment(
        "postgresql://user:private-secret@db:5432/platform?sslmode=require"
    )
    assert env["PGPASSWORD"] == "private-secret"
    assert env["PGDATABASE"] == "platform"
    assert env["PGSSLMODE"] == "require"
