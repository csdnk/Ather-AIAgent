"""Cryptographic identity linkage must not trust editable email/role bindings."""

import importlib
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture
def proof():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    public.update(kid="lab-key", use="sig", alg="RS256")
    claims = {
        "iss": "https://id.example/realms/aether",
        "sub": "alice-subject",
        "aud": "budibase",
        "azp": "budibase",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
    }

    def make(**changes):
        token = jwt.encode(
            {**claims, **changes}, key, algorithm="RS256", headers={"kid": "lab-key"}
        )
        return {
            "_id": "immutable-bb-user",
            "provider": claims["iss"],
            "email": "editable@example.invalid",
            "roles": {"pretend": "platform_admin"},
            "oauth2": {"accessToken": token},
        }

    return make, {"keys": [public]}


def verifier():
    module = Path(__file__).resolve().parents[2] / "src/aether_platform/auth/budibase.py"
    assert module.exists(), "Verified Budibase identity link is not implemented"
    return importlib.import_module("aether_platform.auth.budibase")


def verify(module, actor, keys, subject="alice-subject"):
    return module.verify_identity(
        actor,
        keys,
        issuer="https://id.example/realms/aether",
        client_id="budibase",
        expected_subject=subject,
    )


def test_signed_subject_is_used_and_editable_roles_are_not_returned(proof):
    make, keys = proof
    result = verify(verifier(), make(), keys)
    assert result.subject == "alice-subject"
    assert result.budibase_user_id == "immutable-bb-user"
    assert not hasattr(result, "roles")
    assert not hasattr(result, "access_token")
    assert not hasattr(result, "email")


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "other-client"},
        {"azp": "other-client"},
        {"iss": "https://evil.example"},
        {"exp": 1},
        {"sub": "bob-subject"},
    ],
)
def test_wrong_or_expired_identity_proof_is_rejected(proof, changes):
    make, keys = proof
    module = verifier()
    with pytest.raises(module.IdentityLinkError):
        verify(module, make(**changes), keys)


def test_cookie_identity_cannot_link_to_another_bff_subject(proof):
    make, keys = proof
    module = verifier()
    with pytest.raises(module.IdentityLinkError):
        verify(module, make(), keys, subject="bob-subject")


def test_provider_mismatch_and_unsigned_actor_fail_closed(proof):
    make, keys = proof
    module = verifier()
    for actor in ({**make(), "provider": "https://evil.example"}, {**make(), "oauth2": {}}):
        with pytest.raises(module.IdentityLinkError):
            verify(module, actor, keys)


def test_untrusted_signing_key_and_ambiguous_key_id_are_rejected(proof):
    make, keys = proof
    module = verifier()
    alien = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(alien.public_key(), as_dict=True)
    public.update(kid="lab-key", use="sig", alg="RS256")
    for invalid_keys in ({"keys": [public]}, {"keys": [*keys["keys"], public]}):
        with pytest.raises(module.IdentityLinkError):
            verify(module, make(), invalid_keys)
