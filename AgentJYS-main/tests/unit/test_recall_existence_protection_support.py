"""Unit tests for RC-AUTH-15 existence protection support functions.

Validates the helper functions and models used in existence protection tests
without requiring a full integration environment.
"""

from hashlib import sha256
from pathlib import Path
from tempfile import mkdtemp
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.contracts.models import RecallRequest, Scope
from aether_agent_memory.runtime.contracts.foundation import ConfigurationSnapshot
from aether_agent_memory.runtime.contracts.models import (
    Digest,
    Memory,
    Permission,
    Principal,
    Ref,
)
from recall_existence_protection_support import (
    ExistenceProtectionCase,
    ExistenceScenario,
    assert_no_existence_leakage,
)
from recall_shared_read_support import SharedReadyMemory

pytestmark = pytest.mark.unit


# ============================================================================
# Test Data Fixtures
# ============================================================================


@pytest.fixture
def sample_principal():
    """Create a sample principal for testing."""
    return Principal(
        principal_id="test-user",
        auth_epoch=1,
        permissions=[Permission.READ, Permission.DIAGNOSE],
        home_scope=Scope(
            tenant_id="tenant1",
            application_id="app1",
            user_id="user1",
            agent_id="agent1",
            session_id="session1",
        ),
    )


@pytest.fixture
def sample_request():
    """Create a sample recall request."""
    return RecallRequest(
        query="test query",
        selection={"session_id": "session1"},
        sources="working",
        token_budget=1000,
    )


@pytest.fixture
def sample_memories():
    """Create F1 memory pair for testing."""
    return (
        SharedReadyMemory(
            memory=Memory(
                tenant_id="tenant1",
                memory_id="mem1",
                ref=Ref(memory_id="mem1", version=1),
                scope=Scope(
                    tenant_id="tenant1",
                    application_id="app1",
                    user_id="user1",
                    agent_id="agent1",
                    session_id="session1",
                ),
            ),
            text="用户喝咖啡不加糖。",
            body_hash=sha256("用户喝咖啡不加糖。".encode()).hexdigest(),
            body_generation="gen1",
            projection_generation="proj1",
            projection_state="ready",
            model_space="space1",
            sources={"working"},
            maintainer_env="MAINTAINER_TOKEN",
        ),
        SharedReadyMemory(
            memory=Memory(
                tenant_id="tenant1",
                memory_id="mem2",
                ref=Ref(memory_id="mem2", version=1),
                scope=Scope(
                    tenant_id="tenant1",
                    application_id="app1",
                    user_id="user1",
                    agent_id="agent1",
                    session_id="session1",
                ),
            ),
            text="用户喜欢乌龙茶。",
            body_hash=sha256("用户喜欢乌龙茶。".encode()).hexdigest(),
            body_generation="gen2",
            projection_generation="proj2",
            projection_state="ready",
            model_space="space1",
            sources={"working"},
            maintainer_env="MAINTAINER_TOKEN",
        ),
    )


# ============================================================================
# ExistenceScenario Tests
# ============================================================================


def test_existence_scenario_normal_empty_valid(sample_principal, sample_request):
    """Test valid normal_empty scenario construction."""
    scenario = ExistenceScenario(
        scenario_type="normal_empty",
        description="No memories in authorized scope",
        principal=sample_principal,
        credential_env="TEST_TOKEN",
        request=sample_request,
        has_matching_objects=False,
        expects_grants=False,
    )

    assert scenario.scenario_type == "normal_empty"
    assert not scenario.has_matching_objects
    assert not scenario.expects_grants


def test_existence_scenario_normal_empty_invalid_has_objects(sample_principal, sample_request):
    """Test normal_empty scenario rejects has_matching_objects=True."""
    with pytest.raises(ValidationError, match="should not have matching objects"):
        ExistenceScenario(
            scenario_type="normal_empty",
            description="Invalid",
            principal=sample_principal,
            credential_env="TEST_TOKEN",
            request=sample_request,
            has_matching_objects=True,
            expects_grants=False,
        )


def test_existence_scenario_normal_empty_invalid_expects_grants(sample_principal, sample_request):
    """Test normal_empty scenario rejects expects_grants=True."""
    with pytest.raises(ValidationError, match="should not expect grants"):
        ExistenceScenario(
            scenario_type="normal_empty",
            description="Invalid",
            principal=sample_principal,
            credential_env="TEST_TOKEN",
            request=sample_request,
            has_matching_objects=False,
            expects_grants=True,
        )


def test_existence_scenario_permission_blocked_valid(sample_principal, sample_request):
    """Test valid permission_blocked scenario construction."""
    scenario = ExistenceScenario(
        scenario_type="permission_blocked",
        description="Objects exist but no grants",
        principal=sample_principal,
        credential_env="TEST_TOKEN",
        request=sample_request,
        has_matching_objects=True,
        expects_grants=False,
    )

    assert scenario.scenario_type == "permission_blocked"
    assert scenario.has_matching_objects
    assert not scenario.expects_grants


def test_existence_scenario_permission_blocked_invalid_no_objects(sample_principal, sample_request):
    """Test permission_blocked scenario requires has_matching_objects=True."""
    with pytest.raises(ValidationError, match="requires matching objects"):
        ExistenceScenario(
            scenario_type="permission_blocked",
            description="Invalid",
            principal=sample_principal,
            credential_env="TEST_TOKEN",
            request=sample_request,
            has_matching_objects=False,
            expects_grants=False,
        )


def test_existence_scenario_permission_blocked_invalid_expects_grants(
    sample_principal, sample_request
):
    """Test permission_blocked scenario rejects expects_grants=True."""
    with pytest.raises(ValidationError, match="should have no grants"):
        ExistenceScenario(
            scenario_type="permission_blocked",
            description="Invalid",
            principal=sample_principal,
            credential_env="TEST_TOKEN",
            request=sample_request,
            has_matching_objects=True,
            expects_grants=True,
        )


# ============================================================================
# ExistenceProtectionCase Tests
# ============================================================================


def test_existence_protection_case_valid(sample_principal, sample_request, sample_memories, tmp_path):
    """Test valid existence protection case construction."""
    empty_principal = Principal(
        principal_id="empty-user",
        auth_epoch=1,
        permissions=[Permission.READ],
        home_scope=Scope(
            tenant_id="tenant1",
            application_id="app1",
            user_id="user2",
            agent_id="agent2",
            session_id="session2",
        ),
    )

    blocked_principal = Principal(
        principal_id="blocked-user",
        auth_epoch=1,
        permissions=[Permission.READ],
        home_scope=Scope(
            tenant_id="tenant1",
            application_id="app1",
            user_id="user3",
            agent_id="agent3",
            session_id="session3",
        ),
    )

    case = ExistenceProtectionCase(
        run_id=uuid4().hex,
        scenario_empty=ExistenceScenario(
            scenario_type="normal_empty",
            description="Empty",
            principal=empty_principal,
            credential_env="EMPTY_TOKEN",
            request=sample_request,
            has_matching_objects=False,
            expects_grants=False,
        ),
        scenario_blocked=ExistenceScenario(
            scenario_type="permission_blocked",
            description="Blocked",
            principal=blocked_principal,
            credential_env="BLOCKED_TOKEN",
            request=sample_request,
            has_matching_objects=True,
            expects_grants=False,
        ),
        control_principal=sample_principal,
        control_credential_env="CONTROL_TOKEN",
        control_memories=sample_memories,
        maintainer_env="MAINTAINER_TOKEN",
        configuration=ConfigurationSnapshot(
            config_hash="hash123",
            component_config={},
            runtime_config={},
        ),
        server_settings=RecallSettings(candidate_limit=10),
        source_sha="a" * 40,
        image_digest="sha256:" + "b" * 64,
        backend_binding="backend123",
        model_binding="model123",
        model_space="space1",
        evidence_directory=tmp_path / "evidence",
        q13_policy_blocked=True,
    )

    assert case.scenario_empty.scenario_type == "normal_empty"
    assert case.scenario_blocked.scenario_type == "permission_blocked"
    assert case.q13_policy_blocked


def test_existence_protection_case_invalid_same_principal(
    sample_principal, sample_request, sample_memories, tmp_path
):
    """Test case rejects same principal for both scenarios."""
    with pytest.raises(ValidationError, match="independent principals"):
        ExistenceProtectionCase(
            run_id=uuid4().hex,
            scenario_empty=ExistenceScenario(
                scenario_type="normal_empty",
                description="Empty",
                principal=sample_principal,
                credential_env="TOKEN1",
                request=sample_request,
                has_matching_objects=False,
                expects_grants=False,
            ),
            scenario_blocked=ExistenceScenario(
                scenario_type="permission_blocked",
                description="Blocked",
                principal=sample_principal,  # Same principal - invalid
                credential_env="TOKEN2",
                request=sample_request,
                has_matching_objects=True,
                expects_grants=False,
            ),
            control_principal=sample_principal,
            control_credential_env="CONTROL_TOKEN",
            control_memories=sample_memories,
            maintainer_env="MAINTAINER_TOKEN",
            configuration=ConfigurationSnapshot(
                config_hash="hash123",
                component_config={},
                runtime_config={},
            ),
            server_settings=RecallSettings(candidate_limit=10),
            source_sha="a" * 40,
            image_digest="sha256:" + "b" * 64,
            backend_binding="backend123",
            model_binding="model123",
            model_space="space1",
            evidence_directory=tmp_path / "evidence",
        )


def test_existence_protection_case_invalid_control_no_read(
    sample_principal, sample_request, sample_memories, tmp_path
):
    """Test case rejects control principal without READ permission."""
    control_no_read = Principal(
        principal_id="control",
        auth_epoch=1,
        permissions=[Permission.DIAGNOSE],  # No READ
        home_scope=sample_principal.home_scope,
    )

    empty_principal = Principal(
        principal_id="empty-user",
        auth_epoch=1,
        permissions=[Permission.READ],
        home_scope=Scope(
            tenant_id="tenant1",
            application_id="app1",
            user_id="user2",
            agent_id="agent2",
        ),
    )

    blocked_principal = Principal(
        principal_id="blocked-user",
        auth_epoch=1,
        permissions=[Permission.READ],
        home_scope=Scope(
            tenant_id="tenant1",
            application_id="app1",
            user_id="user3",
            agent_id="agent3",
        ),
    )

    with pytest.raises(ValidationError, match="must have READ permission"):
        ExistenceProtectionCase(
            run_id=uuid4().hex,
            scenario_empty=ExistenceScenario(
                scenario_type="normal_empty",
                description="Empty",
                principal=empty_principal,
                credential_env="EMPTY_TOKEN",
                request=sample_request,
                has_matching_objects=False,
                expects_grants=False,
            ),
            scenario_blocked=ExistenceScenario(
                scenario_type="permission_blocked",
                description="Blocked",
                principal=blocked_principal,
                credential_env="BLOCKED_TOKEN",
                request=sample_request,
                has_matching_objects=True,
                expects_grants=False,
            ),
            control_principal=control_no_read,
            control_credential_env="CONTROL_TOKEN",
            control_memories=sample_memories,
            maintainer_env="MAINTAINER_TOKEN",
            configuration=ConfigurationSnapshot(
                config_hash="hash123",
                component_config={},
                runtime_config={},
            ),
            server_settings=RecallSettings(candidate_limit=10),
            source_sha="a" * 40,
            image_digest="sha256:" + "b" * 64,
            backend_binding="backend123",
            model_binding="model123",
            model_space="space1",
            evidence_directory=tmp_path / "evidence",
        )


# ============================================================================
# assert_no_existence_leakage Tests
# ============================================================================


def test_assert_no_existence_leakage_clean_response(sample_principal, sample_request):
    """Test clean response passes existence leakage check."""
    scenario = ExistenceScenario(
        scenario_type="normal_empty",
        description="Test",
        principal=sample_principal,
        credential_env="TEST_TOKEN",
        request=sample_request,
        has_matching_objects=False,
        expects_grants=False,
    )

    # Mock clean response
    response = httpx.Response(
        status_code=200,
        json={"code": "EMPTY", "message": "No results found"},
    )

    secrets = ("secret1", "secret2")

    # Should not raise
    assert_no_existence_leakage(response, scenario, secrets)


def test_assert_no_existence_leakage_detects_forbidden_fields(sample_principal, sample_request):
    """Test detection of forbidden fields in response."""
    scenario = ExistenceScenario(
        scenario_type="normal_empty",
        description="Test",
        principal=sample_principal,
        credential_env="TEST_TOKEN",
        request=sample_request,
        has_matching_objects=False,
        expects_grants=False,
    )

    # Response with forbidden field
    response = httpx.Response(
        status_code=200,
        json={"code": "OK", "pack": {"items": []}},  # Forbidden: pack
    )

    secrets = ()

    with pytest.raises(AssertionError, match="existence leakage.*forbidden fields"):
        assert_no_existence_leakage(response, scenario, secrets)


def test_assert_no_existence_leakage_detects_f1_content(sample_principal, sample_request):
    """Test detection of F1 content leakage."""
    scenario = ExistenceScenario(
        scenario_type="normal_empty",
        description="Test",
        principal=sample_principal,
        credential_env="TEST_TOKEN",
        request=sample_request,
        has_matching_objects=False,
        expects_grants=False,
    )

    # Response with F1 content leak
    response = httpx.Response(
        status_code=200,
        json={"code": "OK", "message": "Found: 用户喝咖啡不加糖"},  # F1 leak
    )

    secrets = ()

    with pytest.raises(AssertionError, match="F1 content leak"):
        assert_no_existence_leakage(response, scenario, secrets)


def test_assert_no_existence_leakage_detects_secret(sample_principal, sample_request):
    """Test detection of secret leakage."""
    scenario = ExistenceScenario(
        scenario_type="normal_empty",
        description="Test",
        principal=sample_principal,
        credential_env="TEST_TOKEN",
        request=sample_request,
        has_matching_objects=False,
        expects_grants=False,
    )

    # Response with secret leak
    response = httpx.Response(
        status_code=200,
        json={"code": "OK", "token": "secret_credential_value"},
    )

    secrets = ("secret_credential_value",)

    with pytest.raises(AssertionError, match="credential leak"):
        assert_no_existence_leakage(response, scenario, secrets)


def test_assert_no_existence_leakage_detects_nested_leaks(sample_principal, sample_request):
    """Test detection of leaks in nested structures."""
    scenario = ExistenceScenario(
        scenario_type="permission_blocked",
        description="Test",
        principal=sample_principal,
        credential_env="TEST_TOKEN",
        request=sample_request,
        has_matching_objects=True,
        expects_grants=False,
    )

    # Response with nested forbidden field
    response = httpx.Response(
        status_code=200,
        json={
            "code": "OK",
            "data": {
                "status": "complete",
                "details": {"count": 5},  # Forbidden: count
            },
        },
    )

    secrets = ()

    with pytest.raises(AssertionError, match="existence leakage.*forbidden fields"):
        assert_no_existence_leakage(response, scenario, secrets)


def test_assert_no_existence_leakage_checks_lists(sample_principal, sample_request):
    """Test leakage detection in list structures."""
    scenario = ExistenceScenario(
        scenario_type="permission_blocked",
        description="Test",
        principal=sample_principal,
        credential_env="TEST_TOKEN",
        request=sample_request,
        has_matching_objects=True,
        expects_grants=False,
    )

    # Response with forbidden field in list item
    response = httpx.Response(
        status_code=200,
        json={
            "code": "OK",
            "items": [
                {"type": "info"},
                {"type": "data", "memory_id": "mem123"},  # Forbidden: memory_id
            ],
        },
    )

    secrets = ()

    with pytest.raises(AssertionError, match="existence leakage.*forbidden fields"):
        assert_no_existence_leakage(response, scenario, secrets)
