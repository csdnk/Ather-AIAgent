"""Deployment configuration history and authorization for recovery operations.

PostgreSQL backup and restore use external tools and an isolated target.
The service never replaces a live database.
"""

from __future__ import annotations

from pathlib import Path

from aether_agent_memory.runtime.contracts.foundation import (
    BackupManifest,
    ConfigurationSnapshot,
)
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Permission,
    TrustedContext,
)
from aether_agent_memory.runtime.storage.ports import MetadataUnitOfWork

from .common import FoundationError, fingerprint, now
from .identity import Identity
from .telemetry import observed


@observed("runtime.lifecycle")
class RuntimeLifecycle:
    def __init__(
        self,
        uow: MetadataUnitOfWork,
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
            if not self.identity.is_maintenance_operator(
                tx, ctx, Permission.CONFIGURE, self.operators
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

    def backup(self, ctx: TrustedContext, backup_id: str) -> BackupManifest:
        self.authorize(ctx)
        raise FoundationError(
            ErrorCode.CONTRACT_VIOLATION,
            "PostgreSQL backup requires pg_dump and an isolated restore target; "
            "see the deployment guide",
        )

    def restore(self, ctx: TrustedContext, backup_id: str, restore_id: str) -> BackupManifest:
        self.authorize(ctx)
        raise FoundationError(
            ErrorCode.CONTRACT_VIOLATION,
            "Online database replacement is unavailable; use pg_restore into an isolated target",
        )
