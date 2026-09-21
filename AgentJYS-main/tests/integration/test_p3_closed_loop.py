import pytest
from examples.p3_closed_loop import run


@pytest.mark.integration
async def test_mock_p3_closed_loop() -> None:
    result = await run(real_embedding=False)

    embedding = result["embedding"]
    context_pack = result["context_pack"]
    schedule = result["schedule"]
    assert isinstance(embedding, dict)
    assert isinstance(context_pack, dict)
    assert isinstance(schedule, dict)
    assert embedding["dimension"] == 32
    assert context_pack["memories"] == 2
    assert result["archive"] == {"episodic_memories": 1}
    assert schedule["policy_version"] == "heuristic-v1"
    assert schedule["action"] == "promote"
    assert schedule["execute_status"] == "success"
