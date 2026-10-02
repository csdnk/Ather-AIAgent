"""JWT verification backed by trusted static mappings or Keycloak directory identities."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import RLock
from typing import Any

import httpx
import jwt

from aether_agent_memory.runtime.contracts.models import ErrorCode, Principal
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.identity import Identity, jwt_issuer_policy_hash

from .config import JWTIssuerConfiguration

_MAX_TOKEN_BYTES = 65_536
_MAX_JWKS_BYTES = 1_048_576
_MAX_JWKS_KEYS = 128
_UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS = 5.0
_PRIVATE_JWK_FIELDS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth", "k"})


@dataclass(frozen=True)
class _KeySet:
    keys: dict[str, tuple[Any, str | None]]
    expires_at: float
    refreshed_at: float
    policy_hash: str


class JWTDependencyUnavailableError(RuntimeError):
    """The configured issuer's key service could not be used safely."""


class JWTAuthenticator:
    """Validate external JWTs, then map (issuer, subject) to a P3 Principal.

    Claims such as tenant, role, and permission are deliberately ignored. P3's
    identity configuration remains the authority for scope and permissions.
    """

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client or httpx.Client(follow_redirects=False)
        self._owns_client = client is None
        self._clock = clock
        self._lock = RLock()
        self._issuer_locks: dict[str, RLock] = {}
        self._issuers: dict[str, JWTIssuerConfiguration] = {}
        self._policies: dict[str, str] = {}
        self._cache: dict[str, _KeySet] = {}

    def configure(self, issuers: Sequence[JWTIssuerConfiguration]) -> None:
        by_issuer = {issuer.issuer: issuer for issuer in issuers}
        if len(by_issuer) != len(issuers):
            raise ValueError("duplicate JWT issuer")
        policies = {
            name: jwt_issuer_policy_hash(issuer.model_dump(mode="json"))
            for name, issuer in by_issuer.items()
        }
        with self._lock:
            prior_issuers = self._issuers
            prior_cache = self._cache
            self._issuers = by_issuer
            self._policies = policies
            self._cache = {
                name: entry
                for name, entry in prior_cache.items()
                if prior_issuers.get(name) == by_issuer.get(name)
            }
            for name in by_issuer:
                self._issuer_locks.setdefault(name, RLock())

    def authenticate(
        self, identity: Identity, token: str, tenant_id: str | None = None
    ) -> Principal:
        if not token or len(token.encode("utf-8", errors="ignore")) > _MAX_TOKEN_BYTES:
            raise self._unauthenticated()
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(
                token,
                options={
                    "verify_signature": False,
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_aud": False,
                    "verify_iss": False,
                    "verify_sub": False,
                },
            )
        except (jwt.InvalidTokenError, TypeError, ValueError):
            raise self._unauthenticated() from None

        issuer = unverified.get("iss")
        subject = unverified.get("sub")
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if (
            not isinstance(issuer, str)
            or not isinstance(subject, str)
            or not subject
            or not isinstance(algorithm, str)
            or not isinstance(key_id, str)
            or not key_id
            or len(key_id) > 512
        ):
            raise self._unauthenticated()
        with self._lock:
            configuration = self._issuers.get(issuer)
            policy_hash = self._policies.get(issuer)
        if (
            configuration is None
            or policy_hash is None
            or algorithm not in configuration.algorithms
        ):
            raise self._unauthenticated()

        try:
            signing_key, jwk_algorithm = self._signing_key(configuration, key_id, algorithm)
        except JWTDependencyUnavailableError:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "identity provider unavailable"
            ) from None
        except (jwt.InvalidTokenError, jwt.InvalidKeyError):
            raise self._unauthenticated() from None
        if jwk_algorithm is not None and jwk_algorithm != algorithm:
            raise self._unauthenticated()

        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=list(configuration.algorithms),
                audience=configuration.audience,
                issuer=configuration.issuer,
                leeway=configuration.leeway_seconds,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except (jwt.InvalidTokenError, jwt.InvalidKeyError, TypeError, ValueError):
            raise self._unauthenticated() from None
        verified_subject = claims.get("sub")
        if not isinstance(verified_subject, str) or verified_subject != subject:
            raise self._unauthenticated()
        return identity.authenticate_subject(
            configuration.issuer, verified_subject, policy_hash, tenant_id
        )

    def _signing_key(
        self, configuration: JWTIssuerConfiguration, key_id: str, algorithm: str
    ) -> tuple[Any, str | None]:
        issuer = configuration.issuer
        with self._lock:
            issuer_lock = self._issuer_locks.setdefault(issuer, RLock())
        with issuer_lock:
            self._ensure_current(configuration)
            current_time = self._clock()
            with self._lock:
                cached = self._cache.get(issuer)
            refreshed = False
            if cached is None or cached.expires_at <= current_time:
                cached = self._refresh(configuration)
                refreshed = True
            selected = cached.keys.get(key_id)
            if (
                selected is None
                and not refreshed
                and current_time - cached.refreshed_at >= _UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS
            ):
                cached = self._refresh(configuration)
                selected = cached.keys.get(key_id)
            if selected is None:
                raise jwt.InvalidKeyError("unknown signing key")
            signing_key, jwk_algorithm = selected
            if jwk_algorithm is not None and jwk_algorithm != algorithm:
                raise jwt.InvalidKeyError("signing key algorithm mismatch")
            return signing_key, jwk_algorithm

    def _ensure_current(self, configuration: JWTIssuerConfiguration) -> None:
        with self._lock:
            if self._issuers.get(configuration.issuer) != configuration:
                raise jwt.InvalidKeyError("issuer configuration changed")

    def _refresh(self, configuration: JWTIssuerConfiguration) -> _KeySet:
        try:
            keys = self._download_keys(configuration)
        except JWTDependencyUnavailableError:
            raise
        policy_hash = jwt_issuer_policy_hash(configuration.model_dump(mode="json"))
        refreshed_at = self._clock()
        entry = _KeySet(
            keys=keys,
            expires_at=refreshed_at + configuration.jwks_cache_seconds,
            refreshed_at=refreshed_at,
            policy_hash=policy_hash,
        )
        with self._lock:
            if (
                self._issuers.get(configuration.issuer) != configuration
                or self._policies.get(configuration.issuer) != policy_hash
            ):
                raise jwt.InvalidKeyError("issuer configuration changed")
            self._cache[configuration.issuer] = entry
        return entry

    def _download_keys(
        self, configuration: JWTIssuerConfiguration
    ) -> dict[str, tuple[Any, str | None]]:
        try:
            with self._client.stream(
                "GET",
                str(configuration.jwks_url),
                timeout=configuration.jwks_timeout_seconds,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise JWTDependencyUnavailableError(
                        "JWKS endpoint returned a non-success status"
                    )
                content_length = response.headers.get("content-length")
                if content_length is not None and int(content_length) > _MAX_JWKS_BYTES:
                    raise JWTDependencyUnavailableError("JWKS response is too large")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > _MAX_JWKS_BYTES:
                        raise JWTDependencyUnavailableError("JWKS response is too large")
                    chunks.append(chunk)
            document = json.loads(b"".join(chunks))
        except JWTDependencyUnavailableError:
            raise
        except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise JWTDependencyUnavailableError(type(exc).__name__) from None
        if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
            raise JWTDependencyUnavailableError("JWKS document is invalid")
        raw_keys = document["keys"]
        if not raw_keys or len(raw_keys) > _MAX_JWKS_KEYS:
            raise JWTDependencyUnavailableError("JWKS signing key count is invalid")

        keys: dict[str, tuple[Any, str | None]] = {}
        for raw in raw_keys:
            if not isinstance(raw, dict):
                raise JWTDependencyUnavailableError("JWKS key is invalid")
            if _PRIVATE_JWK_FIELDS.intersection(raw):
                raise JWTDependencyUnavailableError("JWKS must contain public keys only")
            if raw.get("use") not in (None, "sig"):
                continue
            key_ops = raw.get("key_ops")
            if key_ops is not None and (not isinstance(key_ops, list) or "verify" not in key_ops):
                continue
            key_id = raw.get("kid")
            if not isinstance(key_id, str) or not key_id:
                continue
            if key_id in keys:
                raise JWTDependencyUnavailableError("JWKS contains duplicate key identifiers")
            algorithm = raw.get("alg")
            if algorithm is not None and not isinstance(algorithm, str):
                raise JWTDependencyUnavailableError("JWKS key algorithm is invalid")
            try:
                key = jwt.PyJWK.from_dict(raw).key
            except (jwt.PyJWTError, KeyError, TypeError, ValueError):
                raise JWTDependencyUnavailableError("JWKS signing key is invalid") from None
            keys[key_id] = (key, algorithm)
        if not keys:
            raise JWTDependencyUnavailableError("JWKS has no usable signing keys")
        return keys

    @staticmethod
    def _unauthenticated() -> FoundationError:
        return FoundationError(ErrorCode.UNAUTHENTICATED, "invalid bearer credential")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
