"""Database backups and explicitly isolated restores; never replace the live DB."""

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import psycopg
from psycopg.conninfo import conninfo_to_dict

from aether_platform.directory import Directory
from aether_platform.operations.models import now


def connection_environment(dsn: str) -> dict[str, str]:
    values = conninfo_to_dict(dsn)
    names = {
        "host": "PGHOST",
        "port": "PGPORT",
        "dbname": "PGDATABASE",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
    }
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(
        {
            names[key]: str(value)
            for key, value in values.items()
            if key in names and value is not None
        }
    )
    env["PGCONNECT_TIMEOUT"] = "10"
    return env


class BackupExecutor:
    def __init__(self, config: dict[str, Any], directory: Directory) -> None:
        self.settings = config.get("operations", {})
        self.directory = directory

    def paths(self, backup_id: str) -> tuple[Path, Path]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", backup_id):
            raise ValueError("Invalid backup identifier")
        if not self.settings.get("backup_directory"):
            raise ValueError("Backup storage is not configured")
        root = Path(self.settings["backup_directory"]).resolve()
        return root / (backup_id + ".dump"), root / (backup_id + ".json")

    @staticmethod
    def execute(args: Sequence[str], dsn: str) -> None:
        try:
            result = subprocess.run(
                args, env=connection_environment(dsn), capture_output=True, timeout=300, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("BACKUP_EXECUTOR_UNAVAILABLE_OR_TIMEOUT") from None
        if result.returncode:
            # Tool stderr can include connection details; keep it out of the API.
            raise RuntimeError("BACKUP_EXECUTOR_FAILED")

    def completed(self, action: str, backup_id: str, command_id: str) -> dict[str, Any] | None:
        dump, manifest_path = self.paths(backup_id)
        receipt = (
            manifest_path if action == "create" else manifest_path.with_suffix(".restore.json")
        )
        if not receipt.exists():
            return None
        manifest = cast(dict[str, Any], json.loads(receipt.read_text(encoding="utf-8")))
        if manifest.get("command_id") != command_id or manifest.get("status") not in {
            "complete",
            "restored",
        }:
            return None
        with dump.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != manifest["sha256"]:
                raise ValueError("Backup checksum mismatch")
        return manifest

    @staticmethod
    def write_receipt(path: Path, data: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)

    def run(self, action: str, backup_id: str, *, command_id: str | None = None) -> dict[str, Any]:
        dump, manifest_path = self.paths(backup_id)
        if action == "create":
            dump.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive reservation also prevents a different command overwriting a backup.
            with manifest_path.open("x", encoding="utf-8") as handle:
                json.dump({"id": backup_id, "status": "creating", "created_at": now()}, handle)
            if dump.exists():
                raise ValueError("Backup already exists")
            self.execute(
                ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(dump)],
                self.directory.dsn,
            )
            with dump.open("rb") as handle:
                checksum = hashlib.file_digest(handle, "sha256").hexdigest()
            manifest: dict[str, Any] = {
                "id": backup_id,
                "command_id": command_id,
                "status": "complete",
                "created_at": now(),
                "sha256": checksum,
                "bytes": dump.stat().st_size,
                "scope": "platform_postgresql_database",
                "excluded": [
                    "P3 database",
                    "Ceph objects",
                    "Milvus",
                    "Temporal",
                    "Ruoyi MySQL",
                    "Redis",
                ],
            }
            self.write_receipt(manifest_path, manifest)
            return manifest
        if action != "restore_drill":
            raise ValueError("Unsupported backup operation")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "complete":
            raise ValueError("Backup is incomplete")
        with dump.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != manifest["sha256"]:
                raise ValueError("Backup checksum mismatch")
        target = self.settings.get("restore_database_dsn")
        if not target:
            raise ValueError("An isolated restore database must be configured")
        source_info, target_info = conninfo_to_dict(self.directory.dsn), conninfo_to_dict(target)
        if source_info.get("dbname") == target_info.get("dbname"):
            raise ValueError("Restore target must use a different database name")
        target_database = target_info["dbname"]
        assert isinstance(target_database, str)
        with psycopg.connect(target) as conn:
            # Serialize all drills targeting this database, including different backup IDs.
            conn.execute("SELECT pg_advisory_xact_lock(195442028)")
            tables_row = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN "
                "('pg_catalog','information_schema')"
            ).fetchone()
            assert tables_row is not None
            if tables_row[0]:
                raise ValueError("Restore target must be empty")
            self.execute(
                [
                    "pg_restore",
                    "--no-owner",
                    "--no-acl",
                    "--exit-on-error",
                    "--single-transaction",
                    "--dbname",
                    target_database,
                    str(dump),
                ],
                target,
            )
            count_row = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN "
                "('pg_catalog','information_schema')"
            ).fetchone()
            assert count_row is not None
        result = {
            "backup_id": backup_id,
            "command_id": command_id,
            "status": "restored",
            "sha256": manifest["sha256"],
            "tables_restored": count_row[0],
            "verified_at": now(),
            "verification": "checksum_and_database_restore",
            "business_acceptance": "pending",
        }
        self.write_receipt(manifest_path.with_suffix(".restore.json"), result)
        return result
