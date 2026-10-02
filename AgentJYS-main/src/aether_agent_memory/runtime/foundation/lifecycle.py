"""Deployment configuration and verified local SQLite recovery artifacts.

Backups are operator-only and cover the RF database, not external providers.
Restore always creates a new isolated database; it never overwrites a live one.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic import TypeAdapter

from aether_agent_memory.runtime.contracts.foundation import (
    BackupManifest,
    ConfigurationSnapshot,
    ResourceLocation,
)
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Identifier,
    Permission,
    RecordRef,
    TrustedContext,
)

from .common import fingerprint, now
from .identity import Identity
from .storage import SQLiteUnitOfWork
from .telemetry import observed


@observed("runtime.lifecycle")
class RuntimeLifecycle:
    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        root: Path,
        *,
        operators: tuple[str, ...] = (),
    ) -> None:
        self.uow, self.identity, self.root, self.operators = (
            uow,
            identity,
            root.resolve(),
            operators,
        )

    def authorize(self, ctx: TrustedContext) -> None:
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            if (
                ctx.principal.principal_id not in self.operators
                or Permission.CONFIGURE not in ctx.principal.permissions
            ):
                tx.abort(ErrorCode.FORBIDDEN, "deployment operator required")

    def activate(
        self, ctx: TrustedContext, snapshot: ConfigurationSnapshot, *, expected_version: str | None
    ) -> ConfigurationSnapshot:
        self.authorize(ctx)
        expected_hash = fingerprint(
            snapshot.model_dump(mode="json", exclude={"config_hash", "activated_at"})
        )
        if snapshot.config_hash != expected_hash or snapshot.activated_at > now():
            raise ValueError("configuration fingerprint or activation time mismatch")
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            active = tx.read("runtime_configuration", "active")
            if active == snapshot.model_dump(mode="json"):
                return snapshot
            if (active["version"] if active else None) != expected_version:
                tx.abort(ErrorCode.VERSION_CONFLICT, "configuration changed")
            if tx.read("configuration_history", snapshot.version):
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "configuration version cannot be reused")
            tx.write("configuration_history", snapshot.version, snapshot.model_dump(mode="json"))
            tx.write("runtime_configuration", "active", snapshot.model_dump(mode="json"))
        return snapshot

    def configuration(self, ctx: TrustedContext) -> ConfigurationSnapshot | None:
        self.authorize(ctx)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            raw = tx.read("runtime_configuration", "active")
        return ConfigurationSnapshot.model_validate(raw) if raw else None

    def path(self, identifier: str, *, restore: bool = False) -> Path:
        identifier = TypeAdapter(Identifier).validate_python(identifier)
        directory = self.root / ("restores" if restore else "snapshots")
        directory.mkdir(parents=True, exist_ok=True)
        path = (directory / (identifier + ".db")).resolve()
        if not path.is_relative_to(self.root) or path == self.uow.path.resolve():
            raise ValueError("backup path escapes configured root")
        return path

    @staticmethod
    def digest(path: Path) -> str:
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest()

    @staticmethod
    def inspect(path: Path) -> tuple[int, str]:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("snapshot integrity check failed")
            rows = db.execute(
                "SELECT namespace,tenant,key,value FROM capability_records "
                "ORDER BY namespace,tenant,key"
            ).fetchall()
            watermark = next(
                (
                    json.loads(v)
                    for n, t, k, v in rows
                    if (n, t, k) == ("p3_rf_meta", "system", "watermark")
                ),
                0,
            )
        return int(watermark), fingerprint(rows)

    def backup(self, ctx: TrustedContext, backup_id: str) -> BackupManifest:
        self.authorize(ctx)
        if getattr(self.uow, "backend", "sqlite") == "postgresql":
            raise ValueError(
                "PostgreSQL backup requires pg_dump; the SQLite snapshot API is not applicable"
            )
        configuration = self.configuration(ctx)
        if configuration is None:
            raise ValueError("activate a deployment configuration before backup")
        target = self.path(backup_id)
        with self.uow.transaction() as tx:
            prior = tx.read("backup_manifests", backup_id)
        if prior:
            manifest = BackupManifest.model_validate(prior)
            if not target.exists() or self.digest(target) != manifest.location.content_hash:
                raise ValueError("previous backup no longer matches its manifest")
            return manifest
        # Exclusive create prevents accidental replacement, including failed or
        # concurrent attempts. A leftover unregistered file requires inspection.
        with target.open("xb"):
            pass
        source = sqlite3.connect(self.uow.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            source.close()
            destination.close()
        watermark, _ = self.inspect(target)
        # Bind the configuration in the snapshot itself, not a pre-backup read.
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as db:
            copied = db.execute(
                "SELECT value FROM capability_records "
                "WHERE namespace='p3_rf_runtime_configuration' AND key='active'"
            ).fetchone()
        if copied is None:
            raise ValueError("snapshot lacks configuration")
        configuration = ConfigurationSnapshot.model_validate_json(copied[0])
        manifest = BackupManifest(
            backup_id=backup_id,
            config_version=configuration.version,
            schema_version="p3_1",
            consistency_watermark=watermark,
            location=ResourceLocation(
                kind="backup",
                provider_id="sqlite",
                provider_instance_id=configuration.deployment_id,
                namespace="rf_database",
                object_key=backup_id + ".db",
                generation=backup_id,
                content_hash=self.digest(target),
            ),
            created_at=now(),
            restore_state="untested",
        )
        self.authorize(ctx)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            tx.write("backup_manifests", backup_id, manifest.model_dump(mode="json"))
        return manifest

    def restore(self, ctx: TrustedContext, backup_id: str, restore_id: str) -> BackupManifest:
        self.authorize(ctx)
        if getattr(self.uow, "backend", "sqlite") == "postgresql":
            raise ValueError("PostgreSQL restore requires pg_restore into an offline target")
        source_path = self.path(backup_id)
        with self.uow.transaction() as tx:
            raw = tx.read("backup_manifests", backup_id)
        if raw is None:
            raise ValueError("backup manifest not found")
        manifest = BackupManifest.model_validate(raw)
        if self.digest(source_path) != manifest.location.content_hash:
            raise ValueError("backup hash mismatch")
        target = self.path(restore_id, restore=True)
        with target.open("xb"):
            pass
        source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            source.close()
            destination.close()
        original = self.inspect(source_path)
        restored = self.inspect(target)
        if original != restored or restored[0] != manifest.consistency_watermark:
            raise ValueError("restored state differs from snapshot")
        self.authorize(ctx)
        proof = RecordRef(
            owner="runtime",
            object_type="restore_proof",
            object_id=restore_id,
            scope=ctx.principal.home_scope,
        )
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            tx.put_if_revision(
                proof,
                {
                    "backup_id": backup_id,
                    "restore_id": restore_id,
                    "watermark": restored[0],
                    "records_hash": restored[1],
                    "verified_at": now(),
                    "scope": "rf_sqlite_only",
                },
                None,
            )
            updated = manifest.model_copy(
                update={
                    "restore_state": "passed",
                    "restore_evidence": (*manifest.restore_evidence, proof),
                }
            )
            tx.write("backup_manifests", backup_id, updated.model_dump(mode="json"))
        return updated
