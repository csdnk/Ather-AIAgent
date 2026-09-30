import pytest
from pydantic import ValidationError
from tests.unit.recall.helpers import binding, scope

from aether_agent_memory.p2.contracts import P2SearchInput


def search(**scope_changes):
    return P2SearchInput(
        query_vector=[1, 2, 3],
        usage="Query",
        model_binding=binding(),
        retrieval_space_ref="space-v1",
        scope=scope(**scope_changes),
        memory_types=["Working"],
        occurred_after=None,
        occurred_before=None,
        top_k=2,
    )


def test_working_vector_search_preserves_session_and_model_binding():
    request = search()
    assert request.memory_types == ["Working"]
    assert request.scope.session_id == "session"
    assert request.retrieval_space_ref == request.model_binding.retrieval_space_ref


def test_working_vector_search_accepts_authorized_task_scope():
    request = search(session_id=None, task_id="task")
    assert request.scope.task_id == "task"


def test_working_vector_search_requires_current_session_or_task():
    with pytest.raises(ValidationError, match="session or task"):
        search(session_id=None, task_id=None)
