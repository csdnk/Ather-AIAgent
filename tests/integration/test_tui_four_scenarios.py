import pytest

from examples.minimal_loop.tui_app import P3DataflowApp


@pytest.mark.integration
async def test_tui_completes_four_documented_application_scenarios() -> None:
    app = P3DataflowApp(step_delay=0)

    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause(0.2)
        for _ in range(4):
            await pilot.press("enter")
            await pilot.pause(0.2)
        await pilot.press("enter")

    assert app.completed_scenarios == [
        "document_upload",
        "user_query",
        "memory_maintenance",
        "object_update",
    ]

    v3 = app.env.object_versions["object-flood-manual-v3"]
    v4 = app.env.object_versions["object-flood-manual-v4"]
    assert v3.status == "superseded"
    assert v4.status == "active"
    assert not any(
        record.object_id == "object-flood-manual-v3"
        for record in app.env.vector_sink.records
    )
    assert app.filtered_object_ids == ["object-flood-manual-v3"]

    manual_memories = await app.env.semantic.query(tags=["manual"])
    assert all(
        memory.state == "superseded"
        for memory in manual_memories
        if memory.object_id == "object-flood-manual-v3"
    )
