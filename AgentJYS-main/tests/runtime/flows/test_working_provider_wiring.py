from types import SimpleNamespace

import pytest
from test_flows import app as app
from test_generation_assembly import assembly_setup


def test_generation_bootstrap_requires_index_readiness_provider(app):
    _, assembly, body, _ = assembly_setup(app)
    app.model_space = app.recall.model_space = "test_space"
    app.embedding = assembly.candidates.embedding
    without_readiness = SimpleNamespace(
        load=body.load, working=body.working, final_guard=app.remember.final_guard
    )
    with pytest.raises(ValueError, match="complete B"):
        app.enable_generation_recall(
            memories=without_readiness,
            qualification=assembly.candidates.qualification,
            bodies=body,
            guards=body,
            space=assembly.candidates.spaces.resolve("test_space"),
        )
