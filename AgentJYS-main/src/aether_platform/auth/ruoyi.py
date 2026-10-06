"""Live Ruoyi authority over explicitly migrated, immutable business ownership.

Opaque tokens are never interpreted as JWTs or saved to the business database.
The first call validates the OAuth token; the second reads current directory state.
No identity or role cache is used. The local mapping grants no role or permission.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import urlsplit

import httpx

from aether_platform.directory import AccessDeniedError, Actor, Directory


class RuoyiUnavailableError(RuntimeError):
    """Identity authority could not be verified; callers must fail closed."""


@dataclass(frozen=True)
class RuoyiActor(Actor):
    permissions: tuple[str, ...] = ()


def validate_ruoyi_config(config: dict[str, Any]) -> dict[str, Any]:
    values = config.get("ruoyi", config)
    url = urlsplit(values["base_url"])
    trusted = values.get("trusted_http_host")
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
        or (
            url.scheme == "http"
            and url.hostname not in {"localhost", "127.0.0.1", "::1"}
            and not (trusted == url.hostname and trusted.endswith(".svc.cluster.local"))
        )
    ):
        raise ValueError("Ruoyi requires HTTPS, loopback, or an explicitly trusted cluster service")
    if (
        not values.get("issuer")
        or not values.get("client_id")
        or not Path(values["client_secret_file"]).is_absolute()
        or not values.get("allowed_client_ids")
    ):
        raise ValueError("Ruoyi requires explicit issuer, client secret file and client allowlist")
    mappings = values.get("mappings", [])
    pairs = [(str(m["ruoyi_user_id"]), str(m["ruoyi_tenant_id"])) for m in mappings]
    users = [m["business_user_id"] for m in mappings]
    if (
        (not pairs and values.get("auto_provision") is not True)
        or len(pairs) != len(set(pairs))
        or len(users) != len(set(users))
    ):
        raise ValueError("Ruoyi requires unique explicit immutable identity mappings")
    tenants: dict[str, str] = {}
    for mapping in [*mappings, *values.get("tenant_mappings", [])]:
        tenant = mapping.get("business_tenant_id")
        if tenant is None:
            continue
        native = str(mapping["ruoyi_tenant_id"])
        if native in tenants and tenants[native] != tenant:
            raise ValueError("Native tenant has conflicting business tenant mappings")
        if tenant in tenants.values() and tenants.get(native) != tenant:
            raise ValueError("Business tenant cannot merge independent native tenants")
        tenants[native] = tenant
    return values


class RuoyiIdentityVerifier:
    def __init__(
        self, config: dict[str, Any], directory: Any, *, client: httpx.Client | None = None
    ):
        self.config = validate_ruoyi_config(config)
        self.directory = directory
        self.http = client or httpx.Client(timeout=5, trust_env=False, follow_redirects=False)
        self._owns_client = client is None
        self._secret = Path(self.config["client_secret_file"]).read_text().strip()
        if not self._secret:
            raise ValueError("Ruoyi client secret file is empty")
        self._bindings = {
            (str(m["ruoyi_user_id"]), str(m["ruoyi_tenant_id"])): dict(m)
            for m in self.config.get("mappings", [])
        }
        self._tenants = {
            str(m["ruoyi_tenant_id"]): m["business_tenant_id"]
            for m in [*self.config.get("mappings", []), *self.config.get("tenant_mappings", [])]
            if m.get("business_tenant_id") is not None
        }

    def remote(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.http.request(
                method, self.config["base_url"].rstrip("/") + path, follow_redirects=False, **kwargs
            )
            if response.status_code in {400, 401, 403}:
                raise AccessDeniedError("Ruoyi identity rejected")
            if response.status_code != 200 or len(response.content) > 1_048_576:
                raise RuoyiUnavailableError("Ruoyi authority unavailable")
            result = response.json()
            if not isinstance(result, dict) or result.get("code") != 0:
                raise AccessDeniedError("Ruoyi identity rejected")
            if not isinstance(result.get("data"), dict):
                raise RuoyiUnavailableError("Ruoyi authority response invalid")
            return result["data"]
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, AccessDeniedError):
                raise
            raise RuoyiUnavailableError("Ruoyi authority unavailable") from None

    def inspect(self, token: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 4096
            or any(c.isspace() for c in token)
        ):
            raise AccessDeniedError("Invalid bearer credential")
        checked = self.remote(
            "POST",
            "/admin-api/system/oauth2/check-token",
            auth=httpx.BasicAuth(self.config["client_id"], self._secret),
            data={"token": token},
        )
        if (
            checked.get("user_type") != 2
            or checked.get("client_id") not in self.config["allowed_client_ids"]
            or not isinstance(checked.get("exp"), (int, float))
            or checked["exp"] <= time.time()
        ):
            raise AccessDeniedError("Ruoyi token expired or outside trust policy")
        current = self.remote(
            "GET", "/admin-api/aether/identity/self", headers={"Authorization": "Bearer " + token}
        )
        pair = (str(checked.get("user_id")), str(checked.get("tenant_id")))
        if (
            pair != (str(current.get("user_id")), str(current.get("tenant_id")))
            or current.get("user_enabled") is not True
            or current.get("tenant_enabled") is not True
        ):
            raise AccessDeniedError("Ruoyi account or tenant is unavailable")
        binding = self.binding(current)
        return {**current, "_token_exp": checked["exp"]}, binding

    def binding(self, current: dict[str, Any]) -> dict[str, Any]:
        pair = (str(current.get("user_id")), str(current.get("tenant_id")))
        binding = self._bindings.get(pair)
        if binding is None and self.config.get("auto_provision") is True:
            if any(not value.isdecimal() or len(value) > 20 for value in pair):
                raise AccessDeniedError("Invalid native identity identifier")
            platform = "aether_platform_admin" in current.get("role_codes", [])
            binding = {
                "ruoyi_user_id": pair[0],
                "ruoyi_tenant_id": pair[1],
                "business_user_id": "ry_user_" + pair[0],
                "business_tenant_id": None
                if platform
                else self._tenants.get(pair[1], "ry_tenant_" + pair[1]),
            }
        if binding is None:
            raise AccessDeniedError("Ruoyi identity has no approved business mapping")
        return binding

    def verify_ruoyi_actor(self, token: str) -> RuoyiActor:
        current, binding = self.inspect(token)
        roles = current.get("role_codes", [])
        if not isinstance(roles, list):
            raise AccessDeniedError("Ruoyi roles invalid")
        role = next(
            (
                name
                for name in ("platform_admin", "tenant_admin", "user")
                if "aether_" + name in roles
            ),
            None,
        )
        if role is None:
            raise AccessDeniedError("No Aether role assigned")
        if hasattr(self.directory, "ensure_ruoyi_user"):
            self.directory.ensure_ruoyi_user(self.config["issuer"], binding, current)
        row = self.directory.mapped_user(binding["business_user_id"])
        if not row or row["tenant_id"] != binding["business_tenant_id"]:
            raise AccessDeniedError("Business ownership mapping changed")
        if (role == "platform_admin") != (row["tenant_id"] is None):
            raise AccessDeniedError("Platform role cannot change mapped tenant ownership")
        permissions = current.get("permissions", [])
        if not isinstance(permissions, list) or any(not isinstance(p, str) for p in permissions):
            raise AccessDeniedError("Ruoyi permissions invalid")
        return RuoyiActor(
            row["id"],
            self.config["issuer"],
            hashlib.sha256(token.encode()).hexdigest(),
            row["tenant_id"],
            role,
            row["version"],
            tuple(sorted(set(permissions))),
        )

    def close(self) -> None:
        if self._owns_client:
            self.http.close()


class RuoyiDirectory(Directory):
    """Database ownership reads plus per-session live authorization for chat work."""

    def __init__(self, dsn: str):
        super().__init__(dsn)
        self.verifier: RuoyiIdentityVerifier | None = None
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = RLock()

    def mapped_user(self, user_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT id,tenant_id,version FROM users WHERE id=%s", (user_id,)
            ).fetchone()

    def ensure_ruoyi_user(
        self, authority: str, binding: dict[str, Any], current: dict[str, Any]
    ) -> None:
        """Bind numeric authority IDs once; never adopt a same-name user or move old data."""
        user_id, tenant_id = binding["business_user_id"], binding["business_tenant_id"]
        with self.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(190102026)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ruoyi_identity_bindings ("
                "issuer text NOT NULL, native_user text NOT NULL, native_tenant text NOT NULL, "
                "business_user text NOT NULL UNIQUE REFERENCES users(id), business_tenant text, "
                "PRIMARY KEY(issuer,native_user,native_tenant))"
            )
            existing = conn.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
            native_subject = str(binding["ruoyi_user_id"])
            if existing is None:
                if not user_id.startswith("ry_user_"):
                    raise AccessDeniedError("Legacy mapping target does not exist")
                if tenant_id is not None:
                    conn.execute(
                        "INSERT INTO tenants(id,name,enabled) VALUES (%s,%s,true) "
                        "ON CONFLICT(id) DO NOTHING",
                        (tenant_id, tenant_id),
                    )
                conn.execute(
                    "INSERT INTO users(id,issuer,subject,username,display_name,"
                    "tenant_id,role,enabled) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,true)",
                    (
                        user_id,
                        authority,
                        native_subject,
                        user_id,
                        current.get("display_name") or user_id,
                        tenant_id,
                        "platform_admin" if tenant_id is None else "user",
                    ),
                )
            elif existing["tenant_id"] != tenant_id:
                raise AccessDeniedError("Immutable business tenant changed")
            elif user_id.startswith("ry_user_") and (existing["issuer"], existing["subject"]) != (
                authority,
                native_subject,
            ):
                raise AccessDeniedError("Native business identity namespace collision")
            conn.execute(
                "INSERT INTO ruoyi_identity_bindings(issuer,native_user,native_tenant,"
                "business_user,business_tenant) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (authority, native_subject, str(binding["ruoyi_tenant_id"]), user_id, tenant_id),
            )
            mapped = conn.execute(
                "SELECT business_user,business_tenant FROM ruoyi_identity_bindings "
                "WHERE issuer=%s AND native_user=%s AND native_tenant=%s",
                (authority, native_subject, str(binding["ruoyi_tenant_id"])),
            ).fetchone()
            if not mapped or (mapped["business_user"], mapped["business_tenant"]) != (
                user_id,
                tenant_id,
            ):
                raise AccessDeniedError("Immutable identity mapping changed")

    def register(self, token: str, expires: float) -> Actor:
        assert self.verifier is not None
        actor = self.verifier.verify_ruoyi_actor(token)
        with self._lock:
            self._tokens = {k: v for k, v in self._tokens.items() if v[1] > time.time()}
            if len(self._tokens) >= 1000:
                raise AccessDeniedError("Session capacity reached")
            self._tokens[actor.subject] = (token, expires)
        return actor

    def forget(self, subject: str) -> None:
        with self._lock:
            self._tokens.pop(subject, None)

    def authenticate(self, issuer: str, subject: str) -> Actor:
        assert self.verifier is not None
        with self._lock:
            lease = self._tokens.get(subject)
        if issuer != self.verifier.config["issuer"] or not lease or lease[1] <= time.time():
            raise AccessDeniedError("Ruoyi session unavailable")
        return self.verifier.verify_ruoyi_actor(lease[0])
