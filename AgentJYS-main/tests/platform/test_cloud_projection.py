from aether_platform.projection import build_projection


def test_projection_retains_subjects_scopes_and_revocation():
    users = [
        dict(id="u-a", subject="idp-1", tenant_id="tenant-a", role="user", enabled=True, version=3),
        dict(
            id="u-off", subject="idp-2", tenant_id="tenant-a", role="user", enabled=False, version=4
        ),
    ]
    value = build_projection(
        [dict(id="tenant-a", enabled=True)], users, "https://test/identity/realms/aether-lab"
    )
    assert len(value["identities"]) == 1
    principal = value["identities"][0]["principal"]
    assert principal["home_scope"]["tenant_id"] == "tenant-a"
    assert principal["auth_epoch"] == 3
    assert value["jwt_issuers"][0]["subject_mappings"] == [
        {"subject": "idp-1", "principal_id": "u-a"}
    ]
    assert value["jwt_issuers"][0]["audience"] == "platform-bff"
