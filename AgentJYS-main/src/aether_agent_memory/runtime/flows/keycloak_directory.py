"""Materialize short-lived authorization snapshots from native Keycloak Organizations.

Keycloak owns memberships/groups/roles. P3 owns the explicit organization-to-tenant
and role-to-permission policy. No member CRUD or identity database is implemented here.
"""

from __future__ import annotations

import json
import logging
import time
from hashlib import sha256
from threading import Event, RLock, Thread
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from aether_agent_memory.runtime.contracts.models import Principal, Scope
from aether_agent_memory.runtime.foundation.common import fingerprint, later
from aether_agent_memory.runtime.foundation.identity import Identity, jwt_issuer_policy_hash

from .config import JWTIssuerConfiguration


class DirectoryUnavailableError(RuntimeError):
    """Sanitized external-directory failure; never includes credentials or responses."""


class KeycloakReader:
    def __init__(self, issuer: JWTIssuerConfiguration, *, client: httpx.Client | None = None):
        assert issuer.directory is not None
        self.issuer, self.config = issuer, issuer.directory
        parsed = urlsplit(issuer.issuer)
        self.base = f"{parsed.scheme}://{parsed.netloc}/admin{parsed.path}"
        self.client = client or httpx.Client(
            timeout=self.config.timeout_seconds, follow_redirects=False
        )
        self.owns_client = client is None
        self.headers: dict[str, str] = {}
        self.deadline = 0.0
        self.requests = 0

    def close(self) -> None:
        if self.owns_client:
            self.client.close()

    def _get(
        self, path: str, params: dict[str, Any] | None = None, *, missing: bool = False
    ) -> Any:
        self.requests += 1
        if time.monotonic() >= self.deadline or self.requests > 10000:
            raise DirectoryUnavailableError("directory snapshot budget exceeded")
        response = self.client.get(self.base + path, params=params, headers=self.headers)
        if missing and response.status_code == 404:
            return None
        if response.status_code != 200 or len(response.content) > 2_097_152:
            raise DirectoryUnavailableError("directory read failed")
        return response.json()

    def _pages(self, path: str, **params: Any) -> list[dict[str, Any]]:
        result = []
        for first in range(0, 10000, 100):
            page = self._get(path, {**params, "first": first, "max": 100})
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise DirectoryUnavailableError("invalid directory page")
            result.extend(page)
            if len(page) < 100:
                return result
        raise DirectoryUnavailableError("directory pagination limit exceeded")

    def fetch(self) -> list[dict[str, Any]]:
        self.deadline = time.monotonic() + min(15, self.config.stale_after_seconds / 2)
        self.requests = 0
        secret = self.config.client_secret_file.read_text("utf-8").strip()
        if not secret:
            raise DirectoryUnavailableError("missing directory credential")
        response = self.client.post(
            self.issuer.issuer + "/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.config.client_id,
                "client_secret": secret,
            },
        )
        if response.status_code != 200:
            raise DirectoryUnavailableError("directory authentication failed")
        self.headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        clients = self._get("/clients", {"clientId": self.config.roles_client_id})
        if not isinstance(clients, list) or len(clients) != 1:
            raise DirectoryUnavailableError("role client not found")
        client_id = quote(clients[0]["id"], safe="")
        records = []
        group_roles: dict[tuple[str, str], set[str]] = {}
        for binding in self.config.organizations:
            path = "/organizations/" + quote(binding.organization_id, safe="")
            organization = self._get(path, missing=True)
            if organization is None or not organization.get("enabled", False):
                continue
            for member in self._pages(path + "/members", briefRepresentation=False):
                if not member.get("enabled", False):
                    continue
                subject = member["id"]
                roles: set[str] = set()
                groups = self._pages(path + "/members/" + quote(subject, safe="") + "/groups")
                for group in groups:
                    group_id = group["id"]
                    visited: set[str] = set()
                    while group_id:
                        if group_id in visited or len(visited) > 32:
                            raise DirectoryUnavailableError("invalid organization group hierarchy")
                        visited.add(group_id)
                        key = (binding.organization_id, group_id)
                        group_path = path + "/groups/" + quote(group_id, safe="")
                        if key not in group_roles:
                            effective = self._get(
                                group_path + "/role-mappings/clients/" + client_id + "/composite"
                            )
                            group_roles[key] = {role["name"] for role in effective}
                        roles.update(group_roles[key])
                        parent = self._get(group_path)
                        group_id = parent.get("parentId")
                approved = sorted(roles.intersection(self.config.role_permissions))
                permissions = sorted(
                    {p for role in approved for p in self.config.role_permissions[role]}
                )
                # Membership without a configured application role does not grant P3 access.
                if not permissions:
                    continue
                stable = sha256(
                    json.dumps([self.issuer.issuer, binding.organization_id, subject]).encode()
                ).hexdigest()
                user_key = (
                    "kc_" + sha256((self.issuer.issuer + "/" + subject).encode()).hexdigest()[:32]
                )
                principal = Principal(
                    principal_id="kc_" + stable[:40],
                    home_scope=Scope(
                        tenant_id=binding.tenant_id,
                        application_id=binding.application_id,
                        user_id=user_key,
                        agent_id=binding.agent_id,
                    ),
                    permissions=tuple(permissions),
                    auth_epoch=1,
                )
                records.append(
                    {
                        "principal": principal,
                        "subject": subject,
                        "details": {
                            "organization_id": binding.organization_id,
                            "organization_name": organization.get("name", binding.tenant_id),
                            "username": member.get("username", subject),
                            "roles": approved,
                            "management_url": self.base.replace("/admin/realms/", "/admin/")
                            + "/console/#/"
                            + urlsplit(self.issuer.issuer).path.rsplit("/", 1)[-1]
                            + "/organizations",
                        },
                    }
                )
        return records


def store_snapshot(
    identity: Identity,
    issuer: JWTIssuerConfiguration,
    records: list[dict[str, Any]],
    observed_at: str,
) -> bool:
    """Atomic replace; tombstones and epochs fence old in-flight work after revocation."""
    assert issuer.directory is not None
    policy = fingerprint(issuer.model_dump(mode="json"))
    jwt_policy = jwt_issuer_policy_hash(issuer.model_dump(mode="json"))
    valid_until = later(observed_at, issuer.directory.stale_after_seconds)
    if valid_until <= identity.clock():
        raise DirectoryUnavailableError("directory snapshot expired during collection")
    with identity.uow.transaction() as tx:
        policies = tx.read("settings", "directory_policies") or {}
        if policies.get(issuer.issuer) != policy:
            return False  # A configuration change superseded this in-flight HTTP read.
        existing = dict(tx.rows("identities"))
        tenants = tx.read("settings", "business_tenants") or {}
        present = set()
        for record in records:
            principal = record["principal"]
            if not tenants.get(principal.home_scope.tenant_id, False):
                continue
            key = principal.principal_id
            if key in present:
                raise DirectoryUnavailableError("duplicate directory principal")
            present.add(key)
            previous = existing.get(key)
            if previous and previous.get("directory_source") != issuer.issuer:
                raise DirectoryUnavailableError("principal namespace collision")
            if previous:
                epoch = previous["principal"]["auth_epoch"]
                old = {**previous["principal"], "auth_epoch": 1}
                changed = (
                    not previous["enabled"]
                    or old != principal.model_dump(mode="json")
                    or previous.get("directory_policy") != policy
                )
                principal = principal.model_copy(update={"auth_epoch": epoch + int(changed)})
            row = {
                "principal": principal.model_dump(mode="json"),
                "digest": None,
                "jwt_subjects": [[issuer.issuer, record["subject"], jwt_policy]],
                "enabled": True,
                "directory_source": issuer.issuer,
                "directory_policy": policy,
                "directory_details": record["details"],
            }
            if row != previous:
                tx.write("identities", key, row)
        for key, row in existing.items():
            if (
                row.get("directory_source") == issuer.issuer
                and key not in present
                and row["enabled"]
            ):
                tx.write("identities", key, {**row, "enabled": False})
        tx.write(
            "directory_snapshots",
            issuer.issuer,
            {
                "observed_at": observed_at,
                "valid_until": valid_until,
                "member_identities": len(records),
            },
        )
    return True


class KeycloakDirectory:
    """Independent I/O thread so directory polling never blocks Temporal's event loop."""

    def __init__(self, identity: Identity):
        self.identity = identity
        self.lock = RLock()
        self.wake = Event()
        self.stopped = Event()
        self.issuers: tuple[JWTIssuerConfiguration, ...] = ()
        self.due: dict[str, float] = {}
        self.thread = Thread(target=self._run, name="p3-keycloak-directory", daemon=True)
        self.thread.start()

    def configure(self, issuers: tuple[JWTIssuerConfiguration, ...]) -> None:
        with self.lock:
            self.issuers = tuple(i for i in issuers if i.directory is not None)
            self.due.clear()
        self.wake.set()

    def close(self) -> None:
        self.stopped.set()
        self.wake.set()
        self.thread.join(timeout=20)

    def _run(self) -> None:
        while not self.stopped.is_set():
            with self.lock:
                issuers = self.issuers
            for issuer in issuers:
                if self.stopped.is_set():
                    return
                assert issuer.directory is not None
                if self.due.get(issuer.issuer, 0) > time.monotonic():
                    continue
                reader = KeycloakReader(issuer)
                try:
                    observed_at = self.identity.clock()
                    records = reader.fetch()
                    if not self.stopped.is_set():
                        store_snapshot(self.identity, issuer, records, observed_at)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "keycloak_directory_unavailable: %s", type(exc).__name__
                    )
                finally:
                    reader.close()
                    self.due[issuer.issuer] = time.monotonic() + issuer.directory.refresh_seconds
            self.wake.wait(timeout=0.5)
            self.wake.clear()
