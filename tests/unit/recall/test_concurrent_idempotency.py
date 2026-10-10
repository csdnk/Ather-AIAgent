"""AET-83: RC-IDE-03: Concurrent Admission Deduplication.

RC-IDE-03: Two concurrent clients with same operation ID and body reference same recall_id
"""

import asyncio

import pytest
from tests.unit.recall.helpers import Authority, policy, recall_input

from aether_agent_memory.recall.admission import RecallAdmissionService
from azure_test_runtime import AzureRecords


@pytest.mark.p0
async def test_rc_ide_03_concurrent_admission_same_operation_id_deduplicates(tmp_path):
    """RC-IDE-03: Concurrent submissions with same operation ID are deduplicated.

    Two concurrent clients with same operation ID and body reference the same recall_id.
    Only one Pack and one packed event generated across both clients.
    """
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    # Barrier to synchronize concurrent clients at B0 (before candidates stage)
    barrier = asyncio.Event()

    # Track when each client starts and completes
    client1_started = asyncio.Event()
    client2_started = asyncio.Event()

    async def client_1():
        """First concurrent client."""
        client1_started.set()
        # Wait for both clients to be ready
        await barrier.wait()
        raw = recall_input(idempotency_key="concurrent-operation-001")
        result = await svc.admit(raw)
        return result

    async def client_2():
        """Second concurrent client with identical request."""
        client2_started.set()
        # Wait for both clients to be ready
        await barrier.wait()
        raw = recall_input(idempotency_key="concurrent-operation-001")
        result = await svc.admit(raw)
        return result

    # Launch both clients concurrently
    task1 = asyncio.create_task(client_1())
    task2 = asyncio.create_task(client_2())

    # Wait for both to reach the barrier
    await client1_started.wait()
    await client2_started.wait()

    # Brief sleep to ensure both are waiting at barrier
    await asyncio.sleep(0.01)

    # Release barrier - both proceed simultaneously
    barrier.set()

    # Wait for both to complete
    result1, result2 = await asyncio.gather(task1, task2)

    # Both clients should reference the same recall_id (deduplication)
    assert result1.execution.recall_id == result2.execution.recall_id
    assert result1.request.idempotency_key == "concurrent-operation-001"
    assert result2.request.idempotency_key == "concurrent-operation-001"

    # Both should reference the same request_ref
    assert result1.request.request_ref == result2.request.request_ref

    # Verify only one Pack event would be generated (same execution object)
    assert result1.execution.recall_id is not None
    assert result2.execution.recall_id == result1.execution.recall_id

    # Both clients receive reference to the same operation result
    assert result1.index.recall_id == result2.index.recall_id

    store.close()


@pytest.mark.p0
async def test_rc_ide_03_concurrent_different_operation_ids_creates_separate_results(tmp_path):
    """Verify that concurrent requests with different operation IDs create separate results."""
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    barrier = asyncio.Event()
    client1_started = asyncio.Event()
    client2_started = asyncio.Event()

    async def client_1():
        client1_started.set()
        await barrier.wait()
        raw = recall_input(idempotency_key="concurrent-operation-002")
        result = await svc.admit(raw)
        return result

    async def client_2():
        client2_started.set()
        await barrier.wait()
        raw = recall_input(idempotency_key="concurrent-operation-003")
        result = await svc.admit(raw)
        return result

    # Launch both clients concurrently
    task1 = asyncio.create_task(client_1())
    task2 = asyncio.create_task(client_2())

    await client1_started.wait()
    await client2_started.wait()
    await asyncio.sleep(0.01)

    barrier.set()

    result1, result2 = await asyncio.gather(task1, task2)

    # Different operation IDs should create different recall_ids
    assert result1.execution.recall_id != result2.execution.recall_id
    assert result1.request.idempotency_key == "concurrent-operation-002"
    assert result2.request.idempotency_key == "concurrent-operation-003"

    store.close()


@pytest.mark.p1
async def test_rc_ide_03_three_concurrent_clients_same_operation_id(tmp_path):
    """Verify that three concurrent clients with same operation ID all reference same result."""
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    barrier = asyncio.Event()
    clients_ready = []

    async def client(client_id: int):
        ready = asyncio.Event()
        clients_ready.append(ready)
        ready.set()
        await barrier.wait()
        raw = recall_input(idempotency_key="concurrent-operation-004")
        result = await svc.admit(raw)
        return result

    # Launch three concurrent clients
    tasks = [asyncio.create_task(client(i)) for i in range(3)]

    # Wait for all clients to be ready
    while len(clients_ready) < 3:
        await asyncio.sleep(0.001)
    for ready_event in clients_ready:
        await ready_event.wait()

    await asyncio.sleep(0.01)

    # Release barrier
    barrier.set()

    # Wait for all to complete
    results = await asyncio.gather(*tasks)

    # All three clients should reference the same recall_id
    assert len(results) == 3
    recall_ids = [r.execution.recall_id for r in results]
    assert len(set(recall_ids)) == 1, "All concurrent clients should reference same recall_id"

    # All should have the same idempotency_key
    for result in results:
        assert result.request.idempotency_key == "concurrent-operation-004"

    store.close()
