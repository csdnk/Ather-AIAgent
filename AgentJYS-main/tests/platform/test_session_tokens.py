import pytest

from aether_platform.auth.session import SessionTokens
from aether_platform.p3 import P3Error


def fixture():
    state = {"active": True, "now": 100, "version": 1, "refreshes": 0}
    session = {
        "subject": "alice",
        "version": 1,
        "expires": 1000,
        "access_expires": 110,
        "access_token": "old",
        "refresh_token": "refresh",
    }

    def refresh(token):
        state["refreshes"] += 1
        return {"access_token": "new", "refresh_token": "rotated", "expires_in": 300}

    proof = SessionTokens(
        session,
        active=lambda: state["active"],
        refresh=refresh,
        introspect=lambda token: {"active": True, "sub": "alice", "client_id": "platform-bff"},
        version=lambda: state["version"],
        clock=lambda: state["now"],
    )
    return state, session, proof


def test_refresh_rotates_without_extending_absolute_session():
    state, session, proof = fixture()
    assert proof.get() == proof.get() == "new"
    assert state["refreshes"] == 1
    assert session["refresh_token"] == "rotated"
    assert session["expires"] == 1000


@pytest.mark.parametrize("change", [{"active": False}, {"now": 1001}, {"version": 2}])
def test_logout_expiry_or_account_change_rejects_background_token(change):
    state, _, proof = fixture()
    state.update(change)
    with pytest.raises(P3Error, match="AUTHENTICATION_REQUIRED"):
        proof.get()


def test_subject_or_client_substitution_rejected():
    for claims in (
        {"active": True, "sub": "bob", "client_id": "platform-bff"},
        {"active": True, "sub": "alice", "client_id": "other"},
        {"active": False},
    ):
        _, _, proof = fixture()
        proof.introspect = lambda token, claims=claims: claims
        with pytest.raises(P3Error, match="AUTHENTICATION_REQUIRED"):
            proof.get()


def test_logout_during_refresh_cannot_resurrect_session():
    state, _, proof = fixture()
    original = proof.refresh

    def revoked(token):
        state["active"] = False
        return original(token)

    proof.refresh = revoked
    with pytest.raises(P3Error, match="AUTHENTICATION_REQUIRED"):
        proof.get()
