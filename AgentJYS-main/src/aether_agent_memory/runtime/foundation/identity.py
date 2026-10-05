"""Deployment-provisioned credentials and object grants; no user/role platform."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from hashlib import sha256
from typing import Any

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
from aether_agent_memory.runtime.foundation.transactions import native
from aether_agent_memory.runtime.storage.ports import MetadataUnitOfWork

from .common import FoundationError, fingerprint, later, now


def jwt_issuer_policy_hash(config: dict[str, Any]) -> str:
    """Fence in-flight JWT verification against a changed trust policy."""
    return fingerprint(
        {
            "issuer": config.get("issuer"),
            "jwks_url": config.get("jwks_url"),
            "audience": config.get("audience"),
            "algorithms": sorted(config.get("algorithms", ["RS256"])),
            "leeway_seconds": config.get("leeway_seconds", 30),
        }
    )


class Identity:
    def __init__(self, uow: MetadataUnitOfWork, clock: Callable[[], str] = now) -> None:
        self.uow, self.clock = uow, clock

    def provision(
        self,
        principals: Sequence[tuple[str | None, Principal]],
        grants: Sequence[AuthorizationGrant] = (),
        *,
        tenants: dict[str, bool] | None = None,
        jwt_subjects: Sequence[tuple[str, str, str]] = (),
        jwt_issuers: Sequence[dict[str, object]] = (),
        configuration_revision: int | None = None,
    ) -> None:
        """Trusted deployment operation. Digests, not API secrets, are persisted.

        Full replacement revokes omitted identities. A changed identity must bump epoch;
        tombstones remain so a stale config cannot resurrect an old epoch.
        """
        principal_ids = {principal.principal_id for _, principal in principals}
        digests = [digest for digest, _ in principals if digest is not None]
        if len(principal_ids) != len(principals) or len(set(digests)) != len(digests):
            raise ValueError("duplicate principal or credential digest")
        normalized_subjects = tuple(sorted(jwt_subjects))
        if len({(issuer, subject) for issuer, subject, _ in normalized_subjects}) != len(
            normalized_subjects
        ):
            raise ValueError("duplicate JWT subject mapping")
        if any(principal_id not in principal_ids for _, _, principal_id in normalized_subjects):
            raise ValueError("JWT subject mapping references an unknown principal")
        issuer_data = tuple(
            sorted(
                (
                    {k: v for k, v in item.items() if k != "directory" or v is not None}
                    for item in jwt_issuers
                ),
                key=lambda issuer: str(issuer.get("issuer", "")),
            )
        )
        issuer_configs = {str(issuer.get("issuer", "")): issuer for issuer in issuer_data}
        if len(issuer_configs) != len(issuer_data):
            raise ValueError("duplicate JWT issuer")
        if any(issuer not in issuer_configs for issuer, _, _ in normalized_subjects):
            raise ValueError("JWT subject mapping has no configured issuer")
        issuer_policy_hashes = {
            issuer: jwt_issuer_policy_hash(config) for issuer, config in issuer_configs.items()
        }
        subjects_by_principal: dict[str, list[list[str]]] = {
            principal_id: [] for principal_id in principal_ids
        }
        for issuer, subject, principal_id in normalized_subjects:
            subjects_by_principal[principal_id].append(
                [issuer, subject, issuer_policy_hashes[issuer]]
            )
        directory_policies = {
            str(item["issuer"]): fingerprint(item) for item in issuer_data if item.get("directory")
        }
        with self.uow.transaction() as tx:
            tx.write("settings", "directory_policies", directory_policies)
            configuration = {
                "principals": [(d, p.model_dump(mode="json")) for d, p in principals],
                "grants": [g.model_dump(mode="json") for g in grants],
                "tenants": tenants,
            }
            # Keep the signature of pre-JWT deployments stable on upgrade.
            if normalized_subjects or issuer_data:
                configuration.update(jwt_subjects=normalized_subjects, jwt_issuers=issuer_data)
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
                if digest is not None and (
                    len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                ):
                    raise ValueError("credential_sha256 must be a SHA256 hex digest")
                previous = old.get(principal.principal_id)
                data = principal.model_dump(mode="json")
                subject_bindings = sorted(subjects_by_principal[principal.principal_id])
                if previous:
                    changed = (
                        not previous["enabled"]
                        or previous["principal"] != data
                        or previous.get("digest") != digest
                        or previous.get("jwt_subjects", []) != subject_bindings
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
                    {
                        "principal": data,
                        "digest": digest,
                        "jwt_subjects": subject_bindings,
                        "enabled": True,
                    },
                )
            present = {p.principal_id for _, p in principals}
            for key, value in old.items():
                source = value.get("directory_source")
                if (
                    source
                    and directory_policies.get(source) == value.get("directory_policy")
                    and (
                        tenants is None
                        or tenants.get(value["principal"]["home_scope"]["tenant_id"], False)
                    )
                ):
                    continue
                if key not in present:
                    tx.write("identities", key, {**value, "enabled": False})
            if len({g.grant_id for g in grants}) != len(grants):
                raise ValueError("duplicate grants")
            tx.write("settings", "grants", [g.model_dump(mode="json") for g in grants])
            snapshot_hash = fingerprint(configuration)
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
                stored_digest = row.get("digest")
                if (
                    row["enabled"]
                    and stored_digest is not None
                    and secrets.compare_digest(stored_digest, digest)
                ):
                    principal = Principal.model_validate(row["principal"])
                    tenants = tx.read("settings", "business_tenants")
                    if tenants is not None and not tenants.get(
                        principal.home_scope.tenant_id, False
                    ):
                        raise FoundationError(ErrorCode.FORBIDDEN, "business tenant disabled")
                    return principal
        raise FoundationError(ErrorCode.UNAUTHENTICATED, "invalid credential")

    def _check_directory(self, tx: Transaction, row: dict[str, Any]) -> None:
        source = row.get("directory_source")
        if not source:
            return
        policies = native(tx).read("settings", "directory_policies") or {}
        state = native(tx).read("directory_snapshots", source) or {}
        if policies.get(source) != row.get("directory_policy"):
            raise FoundationError(ErrorCode.FORBIDDEN, "identity directory changed")
        if state.get("valid_until", "") <= self.clock():
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "identity directory stale")

    def authenticate_subject(
        self, issuer: str, subject: str, policy_hash: str, tenant_id: str | None = None
    ) -> Principal:
        with self.uow.transaction() as tx:
            matches = []
            for _, row in tx.rows("identities"):
                if row["enabled"] and any(
                    len(binding) == 3
                    and binding[0] == issuer
                    and binding[1] == subject
                    and secrets.compare_digest(binding[2], policy_hash)
                    for binding in row.get("jwt_subjects", [])
                ):
                    self._check_directory(tx, row)
                    principal = Principal.model_validate(row["principal"])
                    if tenant_id is None or principal.home_scope.tenant_id == tenant_id:
                        matches.append(principal)
            tenants = tx.read("settings", "business_tenants")
            allowed = [
                p for p in matches if tenants is None or tenants.get(p.home_scope.tenant_id, False)
            ]
            if allowed:
                # The default is explicit in /auth/me; switching always reauthorizes membership.
                return sorted(allowed, key=lambda p: p.home_scope.tenant_id)[0]
            if matches or tenant_id:
                raise FoundationError(ErrorCode.FORBIDDEN, "organization membership unavailable")
        raise FoundationError(ErrorCode.UNAUTHENTICATED, "identity is not mapped")

    def directory_details(self, principal: Principal) -> dict[str, Any]:
        with self.uow.transaction() as tx:
            row = tx.read("identities", principal.principal_id) or {}
            if not row.get("directory_source"):
                return {}
            self._check_directory(tx, row)
            bindings = row.get("jwt_subjects", [])
            tenants = tx.read("settings", "business_tenants") or {}
            organizations = []
            for _, other in tx.rows("identities"):
                if not other.get("enabled") or other.get("jwt_subjects") != bindings:
                    continue
                self._check_directory(tx, other)
                tenant_id = other["principal"]["home_scope"]["tenant_id"]
                if tenants.get(tenant_id, False):
                    organizations.append({"tenant_id": tenant_id, **other["directory_details"]})
            return {
                "identity_source": "keycloak_organizations",
                **row["directory_details"],
                "organizations": sorted(organizations, key=lambda org: org["tenant_id"]),
            }

    def context(
        self,
        credential: str,
        *,
        timeout_seconds: float = 60,
        selector: ScopeSelector | None = None,
        operation_id: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> TrustedContext:
        principal = self.authenticate(credential)
        return self.context_for_principal(
            principal,
            timeout_seconds=timeout_seconds,
            selector=selector,
            operation_id=operation_id,
            request_id=request_id,
            trace_id=trace_id,
            span_id=span_id,
        )

    def context_for_principal(
        self,
        principal: Principal,
        *,
        timeout_seconds: float = 60,
        selector: ScopeSelector | None = None,
        operation_id: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> TrustedContext:
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
            request_id=request_id or secrets.token_hex(16),
            operation_id=operation_id or secrets.token_hex(16),
            trace_id=trace_id or secrets.token_hex(16),
            span_id=span_id or secrets.token_hex(8),
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
        self._check_directory(tx, current)
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
