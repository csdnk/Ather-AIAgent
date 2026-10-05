import importlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.unit.recall.helpers import Inputs, binding, scope

from aether_agent_memory.p2.contracts import P2SearchInput, P2SearchResult
from aether_agent_memory.recall.embedding.models import SemanticEmbeddingRequest
from aether_agent_memory.recall.vector_projection.models import (
    ProjectionMetadata,
    ProjectionPayload,
)
from aether_agent_memory.runtime.contract_types import ByteRange, Scope, hash_json


def test_first_batch_field_manifest_matches_static_models():
    root = Path(__file__).resolve().parents[3]
    manifest = json.loads(
        (root / "docs/recall_contract_field_manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest) == 64
    for name, info in manifest.items():
        module = importlib.import_module(
            "aether_agent_memory." + info["module"].replace("/", ".").removesuffix(".py")
        )
        cls = getattr(module, name)
        assert set(info["fields"]) <= cls.model_fields.keys(), name
        assert all(cls.model_fields[f].is_required() for f in info["fields"]), name
        cls.model_json_schema()


def test_required_nullable_scope_and_project_roundtrip():
    data = scope().model_dump()
    data.pop("project_id")
    with pytest.raises(ValidationError):
        Scope.model_validate(data)
    configured = scope().model_copy(update={"project_id": "project"})
    assert Scope.model_validate(configured.model_dump()).project_id == "project"
    assert Scope.model_validate_json(configured.model_dump_json()).project_id == "project"



@pytest.mark.parametrize("start,end", [(True, 3), (-1, 4), (2, 2), (0, 9007199254740992), (0, "3")])
def test_byte_ranges_are_strict(start, end):
    with pytest.raises(ValidationError):
        ByteRange(start=start, end=end)


def test_jcs_range_and_binding_digest_rules():
    assert ByteRange(start=0, end=5).proto_inclusive() == (0, 4)
    assert hash_json({"x": 1}) == hash_json({"x": 1.0})
    assert hash_json({"x": None}) != hash_json({})
    request = Inputs().request()
    value = request.model_dump()
    value["usage"] = "Passage"
    with pytest.raises(ValidationError, match="reuse_digest"):
        SemanticEmbeddingRequest.model_validate(value)


def test_search_does_not_claim_completion_or_compatibility_from_empty_hits():
    with pytest.raises(ValidationError, match="completion evidence"):
        P2SearchResult(
            retrieval_space_ref="space",
            requested_k=2,
            completion="complete",
            hits=[],
            partial_reason=None,
            ranking_contract_ref="rank",
            query_binding_evidence=[],
            completion_evidence=[],
        )
    partial = P2SearchResult(
        retrieval_space_ref="space",
        requested_k=2,
        completion="partial",
        hits=[],
        partial_reason="timeout",
        ranking_contract_ref="rank",
        query_binding_evidence=[],
        completion_evidence=[],
    )
    assert partial.hits == [] and partial.completion == "partial"
    with pytest.raises(ValidationError, match="space disagree"):
        P2SearchInput(
            query_vector=[1, 2, 3],
            usage="Query",
            model_binding=binding(),
            retrieval_space_ref="wrong",
            scope=scope(),
            memory_types=["Semantic"],
            occurred_after=None,
            occurred_before=None,
            top_k=2,
        )


def test_metadata_evidence_is_not_part_of_metadata_hash():
    meta = ProjectionMetadata(
        scope=scope(), memory_type="Semantic", occurred_at=None, owner_metadata_evidence_ref="e1"
    )
    digest = hash_json(meta.model_dump(mode="json", exclude={"owner_metadata_evidence_ref"}))
    kwargs = dict(
        embedding_result_ref="embedding",
        representation_id="representation",
        content_ref="content",
        content_version="v1",
        approved_range=ByteRange(start=0, end=3),
        content_evidence_ref="content-evidence",
        metadata_hash=digest,
    )
    first = ProjectionPayload(metadata=meta, **kwargs)
    other = ProjectionPayload(metadata=meta.replaced(owner_metadata_evidence_ref="e2"), **kwargs)
    assert first.metadata_hash == other.metadata_hash
    with pytest.raises(ValidationError, match="metadata_hash"):
        ProjectionPayload(metadata=meta.replaced(memory_type="Episodic"), **kwargs)
