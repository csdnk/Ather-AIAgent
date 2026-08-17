import pytest

from aether_agent_memory.demo import UnifiedDemoRunner


@pytest.mark.integration
async def test_unified_flow_smoke_emits_each_critical_node() -> None:
    report = await UnifiedDemoRunner().run()

    assert [event.node for event in report.events] == [
        "P4 Gateway",
        "P2 E2 Object",
        "P3 B1",
        "P2 E1 Vector",
        "P3 B2",
        "P3 B2",
        "P3 B2",
        "P3 B3",
        "P1/P2 Executor",
        "Demo Runner",
    ]
    assert all(event.status == "success" for event in report.events)
    assert report.p2_ref is not None
    assert report.embedding_count > 0
    assert report.context_memory_count > 0
    assert report.schedule is not None
    assert report.schedule.entries[0].feedback.execute_status.value == "success"
