"""Link an authenticated Budibase server response to an authenticated BFF subject.

The caller must fetch /api/global/self from the configured Budibase server using
the current user's session, and fetch JWKS from the configured trusted issuer.
Never accept either document, expected_subject, issuer or client_id from an
HTTP request body. This is the identity-link primitive, not an authorization API.
"""

from dataclasses import dataclass
from typing import Any

import jwt


class IdentityLinkError(ValueError):
    """Identity could not be proven without trusting browser-editable bindings."""


@dataclass(frozen=True)
class VerifiedBudibaseIdentity:
    budibase_user_id: str
    issuer: str
    subject: str


def verify_identity(
    actor: dict[str, Any],
    jwks: dict[str, Any],
    *,
    issuer: str,
    client_id: str,
    expected_subject: str,
) -> VerifiedBudibaseIdentity:
    """Check a fresh signed IdP proof when establishing the BFF session link.

    Expired proofs fail closed; acquiring a fresh proof is the login adapter's
    responsibility. No refresh token or business role leaves this function.
    """
    try:
        user_id = actor["_id"]
        if not isinstance(user_id, str) or not user_id or not expected_subject:
            raise ValueError("missing identity")
        if actor.get("provider") != issuer:
            raise ValueError("provider mismatch")
        token = actor["oauth2"]["accessToken"]
        header = jwt.get_unverified_header(token)
        keys = [key for key in jwt.PyJWKSet.from_dict(jwks).keys if key.key_id == header.get("kid")]
        if len(keys) != 1:
            raise ValueError("ambiguous signing key")
        claims = jwt.decode(
            token,
            key=keys[0].key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=client_id,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "azp"]},
        )
        if claims["azp"] != client_id or claims["sub"] != expected_subject:
            raise ValueError("subject mismatch")
        return VerifiedBudibaseIdentity(user_id, issuer, expected_subject)
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise IdentityLinkError("Budibase identity could not be linked") from None
