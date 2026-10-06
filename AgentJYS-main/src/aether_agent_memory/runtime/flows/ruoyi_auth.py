"""Opaque Ruoyi credentials and live, durable P3 authorization fences."""

from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx

from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, Principal, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_platform.auth.ruoyi import RuoyiIdentityVerifier, RuoyiUnavailableError
from aether_platform.directory import AccessDeniedError


class RuoyiAuthenticator:
    def __init__(
        self, identity: Identity, config: dict[str, Any], *, client: httpx.Client | None = None
    ):
        self.identity = identity
        self.verifier = RuoyiIdentityVerifier(config, None, client=client)
        self.policy = fingerprint(config)
        if not config.get("role_permissions"):
            raise ValueError("P3 Ruoyi requires explicit role permission ceilings")
        self.roles = {
            role: tuple(Permission(p) for p in permissions)
            for role, permissions in config["role_permissions"].items()
        }
        identity.ruoyi_revalidate = self.revalidate

    @staticmethod
    def authority(current: dict[str, Any]) -> str:
        return fingerprint(
            {
                key: current.get(key)
                for key in ("user_id", "tenant_id", "user_enabled", "tenant_enabled")
            }
            | {
                "role_codes": sorted(current.get("role_codes", [])),
                "permissions": sorted(current.get("permissions", [])),
            }
        )

    def principal(self, current: dict[str, Any], binding: dict[str, Any]) -> Principal:
        roles = current.get("role_codes", [])
        if not isinstance(roles, list) or not any(role in self.roles for role in roles):
            raise AccessDeniedError("No permitted P3 role assigned")
        is_platform = "aether_platform_admin" in roles
        if is_platform != (binding["business_tenant_id"] is None):
            raise AccessDeniedError("Platform role cannot alter tenant ownership")
        permissions = tuple(sorted({p for role in roles for p in self.roles.get(role, ())}))
        maintenance_grants = {
            Permission.DIAGNOSE: "aether:ops:read",
            Permission.RECOVER: "aether:tasks:execute",
            Permission.CONFIGURE: "aether:configuration:execute",
        }
        permissions = tuple(
            p
            for p in permissions
            if p not in maintenance_grants
            or maintenance_grants[p] in current.get("permissions", [])
            or "*:*:*" in current.get("permissions", [])
        )
        if not is_platform:
            permissions = tuple(
                p
                for p in permissions
                if p not in {Permission.DIAGNOSE, Permission.RECOVER, Permission.CONFIGURE}
            )
        return Principal(
            principal_id=binding.get("principal_id", "ry_principal_" + str(current["user_id"])),
            home_scope=Scope(
                tenant_id=binding["business_tenant_id"] or "aether_platform_operations",
                user_id=binding["business_user_id"],
                application_id=binding.get("application_id", "p3"),
                agent_id=binding.get("agent_id", "p3-agent"),
            ),
            permissions=permissions,
            auth_epoch=1,
        )

    def authenticate(
        self, identity: Identity, token: str, tenant_id: str | None = None
    ) -> Principal:
        try:
            current, binding = self.verifier.inspect(token)
            principal = self.principal(current, binding)
            if tenant_id is not None and principal.home_scope.tenant_id != tenant_id:
                raise AccessDeniedError("Tenant selector cannot expand identity scope")
            digest = hashlib.sha256(token.encode()).hexdigest()
            authority = self.authority(current)
            with identity.uow.transaction() as tx:
                old = tx.read("identities", principal.principal_id)
                if old:
                    if old.get("ruoyi_source") not in (None, self.verifier.config["issuer"]):
                        raise AccessDeniedError("Identity authority collision")
                    if old["principal"]["home_scope"] != principal.home_scope.model_dump(
                        mode="json"
                    ):
                        raise AccessDeniedError("Immutable P3 ownership mapping changed")
                    # Adoption of a legacy principal requires an explicit migration entry.
                    if (
                        not old.get("ruoyi_source")
                        and binding.get("principal_id") != principal.principal_id
                    ):
                        raise AccessDeniedError("P3 namespace collision")
                    changed = (
                        not old.get("enabled")
                        or old.get("ruoyi_policy") != self.policy
                        or old.get("ruoyi_authority") != authority
                        or old["principal"]["permissions"]
                        != [str(p) for p in principal.permissions]
                    )
                    principal = principal.model_copy(
                        update={"auth_epoch": old["principal"]["auth_epoch"] + int(changed)}
                    )
                tx.write(
                    "identities",
                    principal.principal_id,
                    {
                        "principal": principal.model_dump(mode="json"),
                        "enabled": True,
                        "digest": None,
                        "jwt_subjects": [],
                        "ruoyi_source": self.verifier.config["issuer"],
                        "ruoyi_policy": self.policy,
                        "ruoyi_authority": authority,
                        "ruoyi_user_id": str(current["user_id"]),
                        "ruoyi_tenant_id": str(current["tenant_id"]),
                        "ruoyi_roles": sorted(current.get("role_codes", [])),
                        "ruoyi_permissions": sorted(current.get("permissions", [])),
                    },
                )
                tx.write(
                    "ruoyi_credentials",
                    digest,
                    {
                        "principal_id": principal.principal_id,
                        "auth_epoch": principal.auth_epoch,
                        "expires": current["_token_exp"],
                    },
                )
            return principal
        except RuoyiUnavailableError:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "Ruoyi authority unavailable"
            ) from None
        except (AccessDeniedError, KeyError, TypeError, ValueError):
            raise FoundationError(ErrorCode.UNAUTHENTICATED, "Ruoyi identity rejected") from None

    def bind_context(self, ctx: Any, token: str) -> None:
        """Bind the server request ID to its own revocable credential; hash only."""
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.identity.uow.transaction() as tx:
            credential = tx.read("ruoyi_credentials", digest)
            if (
                not credential
                or credential["principal_id"] != ctx.principal.principal_id
                or credential["auth_epoch"] != ctx.principal.auth_epoch
            ):
                raise FoundationError(ErrorCode.UNAUTHENTICATED, "Ruoyi credential is not bound")
            grant = {**credential, "token_hash": digest}
            old = tx.read("ruoyi_request_grants", ctx.request_id)
            if old is not None and old != grant:
                raise FoundationError(ErrorCode.FORBIDDEN, "Request credential cannot change")
            tx.write("ruoyi_request_grants", ctx.request_id, grant)

    def revalidate(self, tx: Any, row: dict[str, Any], ctx: Any) -> None:
        try:
            grant = tx.read("ruoyi_request_grants", ctx.request_id)
            if (
                row.get("ruoyi_source") != self.verifier.config["issuer"]
                or row.get("ruoyi_policy") != self.policy
                or not grant
                or grant.get("principal_id") != ctx.principal.principal_id
                or grant.get("auth_epoch") != ctx.principal.auth_epoch
                or grant.get("expires", 0) <= time.time()
            ):
                raise AccessDeniedError("Ruoyi authority expired or changed")
            current = self.verifier.remote(
                "POST",
                "/admin-api/aether/identity/status",
                auth=httpx.BasicAuth(self.verifier.config["client_id"], self.verifier._secret),
                data={
                    "user_id": row["ruoyi_user_id"],
                    "tenant_id": row["ruoyi_tenant_id"],
                    "token_hash": grant["token_hash"],
                },
            )
            if (
                current.get("user_enabled") is not True
                or current.get("tenant_enabled") is not True
                or self.authority(current) != row["ruoyi_authority"]
            ):
                raise AccessDeniedError("Ruoyi authority revoked or changed")
            projected = self.principal(current, self.verifier.binding(current))
            stored = Principal.model_validate(row["principal"])
            if projected.model_copy(update={"auth_epoch": stored.auth_epoch}) != stored:
                raise AccessDeniedError("Ruoyi scope or permissions changed")
        except RuoyiUnavailableError:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "Ruoyi authority unavailable"
            ) from None
        except (AccessDeniedError, KeyError, TypeError, ValueError):
            raise FoundationError(
                ErrorCode.FORBIDDEN, "Ruoyi authority revoked or changed"
            ) from None

    def close(self) -> None:
        self.verifier.close()
