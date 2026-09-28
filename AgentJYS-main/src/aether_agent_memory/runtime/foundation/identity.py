"""Deployment-provisioned credentials and object grants; no user/role platform."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from hashlib import sha256

from aether_agent_memory.runtime.contracts.models import (
    AuthorizationGrant,
    ErrorCode,
    Permission,
    Principal,
    RecordRef,
    ScopeSelector,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction

from .common import FoundationError, fingerprint, later, now
from .storage import SQLiteUnitOfWork, native


class Identity:
    def __init__(self, uow: SQLiteUnitOfWork, clock: Callable[[], str] = now) -> None:
        self.uow, self.clock = uow, clock

    def provision(
        self,
        principals: Sequence[tuple[str, Principal]],
        grants: Sequence[AuthorizationGrant] = (),
        *,
        tenants: dict[str, bool] | None = None,
        configuration_revision: int | None = None,
    ) -> None:
        """Trusted deployment operation. Digests, not API secrets, are persisted.

        Full replacement revokes omitted identities. A changed identity must bump epoch;
        tombstones remain so a stale config cannot resurrect an old epoch.
        """
        if len({p.principal_id for _, p in principals}) != len(principals) or len(
            {k for k, _ in principals}
        ) != len(principals):
            raise ValueError("duplicate principal or credential digest")
        with self.uow.transaction() as tx:
            configuration = {
                "principals": [(d, p.model_dump(mode="json")) for d, p in principals],
                "grants": [g.model_dump(mode="json") for g in grants],
                "tenants": tenants,
            }
            if configuration_revision is not None:
                previous_config = tx.read("settings", "identity_revision")
                signature = fingerprint(configuration)
                if previous_config and (
                    configuration_revision < previous_config["revision"]
                    or (
                        configuration_revision == previous_config["revision"]
                        and signature != previous_config["signature"]
                    )
                ):
                    raise ValueError(
                        "identity configuration must advance; old grants cannot be restored"
                    )
                tx.write(
                    "settings",
                    "identity_revision",
                    {"revision": configuration_revision, "signature": signature},
                )
            if tenants is not None:
                if any(p.home_scope.tenant_id not in tenants for _, p in principals):
                    raise ValueError("identity has no registered business tenant")
                # Disabled tenants fence every in-flight principal epoch permanently.
                for _, principal in principals:
                    if not tenants[principal.home_scope.tenant_id]:
                        previous = tx.read("tenant_epoch_fences", principal.principal_id) or 0
                        tx.write(
                            "tenant_epoch_fences",
                            principal.principal_id,
                            max(previous, principal.auth_epoch),
                        )
                    else:
                        fence = tx.read("tenant_epoch_fences", principal.principal_id) or 0
                        if principal.auth_epoch <= fence:
                            raise ValueError(
                                "tenant reactivation requires fresh principal auth_epoch"
                            )
                tx.write("settings", "business_tenants", tenants)
            old = dict(tx.rows("identities"))
            for digest, principal in principals:
                if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("credential_sha256 must be a SHA256 hex digest")
                previous = old.get(principal.principal_id)
                data = principal.model_dump(mode="json")
                if previous:
                    changed = (
                        not previous["enabled"]
                        or previous["principal"] != data
                        or previous["digest"] != digest
                    )
                    if principal.auth_epoch < previous["principal"]["auth_epoch"] or (
                        changed and principal.auth_epoch <= previous["principal"]["auth_epoch"]
                    ):
                        raise ValueError(
                            "identity changes require a strictly increasing auth_epoch"
                        )
                tx.write(
                    "identities",
                    principal.principal_id,
                    {"principal": data, "digest": digest, "enabled": True},
                )
            present = {p.principal_id for _, p in principals}
            for key, value in old.items():
                if key not in present:
                    tx.write("identities", key, {**value, "enabled": False})
            if len({g.grant_id for g in grants}) != len(grants):
                raise ValueError("duplicate grants")
            tx.write("settings", "grants", [g.model_dump(mode="json") for g in grants])
            snapshot_hash = fingerprint(
                {
                    "identities": [(digest, p.model_dump(mode="json")) for digest, p in principals],
                    "grants": [g.model_dump(mode="json") for g in grants],
                    "tenants": tenants,
                }
            )
            if tx.read("settings", "identity_config_hash") != snapshot_hash:
                tx.write(
                    "configuration_history",
                    secrets.token_hex(16),
                    {
                        "actor": "deployment_operator",
                        "occurred_at": self.clock(),
                        "config_hash": snapshot_hash,
                        "principal_ids": sorted(present),
                        "grant_ids": sorted(g.grant_id for g in grants),
                        "tenants": tenants,
                    },
                )
                tx.write("settings", "identity_config_hash", snapshot_hash)

    def authenticate(self, credential: str) -> Principal:
        digest = sha256(credential.encode()).hexdigest()
        with self.uow.transaction() as tx:
            for _, row in tx.rows("identities"):
                if row["enabled"] and secrets.compare_digest(row["digest"], digest):
                    principal = Principal.model_validate(row["principal"])
                    tenants = tx.read("settings", "business_tenants")
                    if tenants is not None and not tenants.get(
                        principal.home_scope.tenant_id, False
                    ):
                        raise FoundationError(ErrorCode.FORBIDDEN, "business tenant disabled")
                    return principal
        raise FoundationError(ErrorCode.UNAUTHENTICATED, "invalid credential")

    def context(
        self,
        credential: str,
        *,
        timeout_seconds: float = 60,
        selector: ScopeSelector | None = None,
        operation_id: str | None = None,
    ) -> TrustedContext:
        principal = self.authenticate(credential)
        if not 0 < timeout_seconds <= 3600:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "deadline must be within one hour")
        if selector is not None:
            for key, value in selector.model_dump(exclude_none=True).items():
                if getattr(principal.home_scope, key) != value:
                    raise FoundationError(
                        ErrorCode.FORBIDDEN, "scope selector cannot expand identity scope"
                    )
        return TrustedContext(
            principal=principal,
            request_id=secrets.token_hex(16),
            operation_id=operation_id or secrets.token_hex(16),
            trace_id=secrets.token_hex(16),
            span_id=secrets.token_hex(8),
            deadline_at=later(self.clock(), timeout_seconds),
        )

    def revalidate(self, tx: Transaction, ctx: TrustedContext) -> None:
        sql = native(tx)
        current = sql.read("identities", ctx.principal.principal_id)
        tenants = sql.read("settings", "business_tenants")
        if (
            not current
            or not current["enabled"]
            or current["principal"] != ctx.principal.model_dump(mode="json")
            or (tenants is not None and not tenants.get(ctx.principal.home_scope.tenant_id, False))
        ):
            sql.abort(ErrorCode.FORBIDDEN, "principal revoked or changed")
        if self.clock() >= ctx.deadline_at:
            sql.abort(ErrorCode.DEADLINE_EXCEEDED, "request deadline expired")

    def authorize(
        self, tx: Transaction, ctx: TrustedContext, permission: Permission, target: RecordRef
    ) -> None:
        if not self.permits(tx, ctx, permission, target):
            native(tx).abort(ErrorCode.FORBIDDEN, "no permission for this object")

    def discoverable(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        target: RecordRef,
        selection: ScopeSelector,
    ) -> bool:
        """Home resources plus explicitly granted resources, intersected with the request."""
        if any(
            getattr(target.scope, key) != value
            for key, value in selection.model_dump(exclude_none=True).items()
        ):
            return False
        # Memory grants cover the logical memory; a version is still checked by B.
        logical = target.model_copy(update={"version": None})
        return self.permits(tx, ctx, Permission.READ, logical)

    def permits(
        self, tx: Transaction, ctx: TrustedContext, permission: Permission, target: RecordRef
    ) -> bool:
        """Filter one candidate without aborting the read transaction; identity stays current."""
        self.revalidate(tx, ctx)
        sql = native(tx)
        principal = ctx.principal
        home, scope = principal.home_scope, target.scope
        if permission not in principal.permissions or home.tenant_id != scope.tenant_id:
            return False
        own = all(
            getattr(home, key) == getattr(scope, key)
            for key in ("application_id", "user_id", "agent_id")
        )
        restricted = all(
            getattr(home, key) is None or getattr(home, key) == getattr(scope, key)
            for key in ("session_id", "task_id")
        )
        if own and restricted:
            return True
        for raw in sql.read("settings", "grants") or []:
            grant = AuthorizationGrant.model_validate(raw)
            if (
                grant.grantee_id == principal.principal_id
                and (
                    grant.resource == target
                    or (
                        grant.resource.version is None
                        and grant.resource == target.model_copy(update={"version": None})
                    )
                )
                and permission in grant.permissions
                and (grant.expires_at is None or self.clock() < grant.expires_at)
            ):
                return True
        return False
