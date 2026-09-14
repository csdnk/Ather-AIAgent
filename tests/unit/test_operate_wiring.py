from pathlib import Path
from types import SimpleNamespace

import pytest

from aether_agent_memory.bootstrap.container import build_dependencies_from_legacy
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.mocks.embedding import MockEmbeddingClient
from aether_agent_memory.persistence import InMemoryMemoryStore
from aether_agent_memory.runtime.dependencies import RuntimeProfile
from aether_agent_memory.runtime.legacy import P3RuntimeConfig
from aether_agent_memory.signal.emitter import MockSignalEmitter


@pytest.mark.parametrize(
    ("setting", "default"),
    [("AETHER_P3_STORAGE_CONTROL_URL", ""), ("AETHER_B3_SHADOW_MODE", "true")],
)
def test_compose_forwards_operate_configuration_with_safe_defaults(setting, default):
    compose = (Path(__file__).resolve().parents[2] / "compose.yaml").read_text("utf-8")
    p3 = compose.split("\n  p3:\n", 1)[1].split("\n  p3-session-worker:\n", 1)[0]
    assert f'      {setting}: "${{{setting}:-{default}}}"' in p3


@pytest.mark.parametrize("profile", [RuntimeProfile.INTEGRATION, RuntimeProfile.PRODUCTION])
async def test_composition_uses_new_scheduler_not_legacy(tmp_path, profile):
    from aether_agent_memory.adapters.operate_scheduler import OperateSchedulerAdapter
    from aether_agent_memory.adapters.storage_control_simulator import StorageControlSimulator

    store = InMemoryMemoryStore()
    manager = SimpleNamespace()
    legacy = SimpleNamespace(
        memory=SimpleNamespace(_working=manager, _episodic=manager, _semantic=manager),
        compatibility_memory_store=store,
        signal_emitter=MockSignalEmitter(),
        embedder=MockEmbeddingClient(),
    )
    config = P3RuntimeConfig(p2_endpoint="unused", data_dir=tmp_path, memory_store="sqlite")
    deps = build_dependencies_from_legacy(legacy, config=config, profile=profile)
    assert isinstance(deps.scheduler, OperateSchedulerAdapter)
    assert isinstance(deps.scheduler.controller.actuator, StorageControlSimulator) == (
        profile == RuntimeProfile.INTEGRATION
    )
    await deps.scheduler.close()


def test_legacy_candidate_mapper_no_longer_infers_tier_from_memory_type():
    from aether_agent_memory.b3.application import _tier_for_memory

    inferred = [
        _tier_for_memory(Memory(type=kind, session_id="s", agent_id="a", content="x"))
        for kind in MemoryType
    ]
    assert len(set(inferred)) == 1


async def test_b2_creation_signal_does_not_alias_importance_to_heat():
    from aether_agent_memory.b2 import MemoryEvent, MemoryEventType, MemoryService
    from aether_agent_memory.context import MockContextPackBuilder
    from aether_agent_memory.episodic import MockEpisodicMemoryManager
    from aether_agent_memory.semantic import MockSemanticMemoryManager
    from aether_agent_memory.working import MockWorkingMemoryManager

    emitter = MockSignalEmitter()
    working = MockWorkingMemoryManager()
    episodic = MockEpisodicMemoryManager(MockEmbeddingClient())
    semantic = MockSemanticMemoryManager(MockEmbeddingClient())
    service = MemoryService(
        working=working,
        episodic=episodic,
        semantic=semantic,
        emitter=emitter,
        builder=MockContextPackBuilder(working=working, episodic=episodic, semantic=semantic),
    )
    await service.ingest(
        MemoryEvent(
            event_type=MemoryEventType.USER_MEMORY,
            session_id="s",
            agent_id="a",
            content="x",
            importance=0.9,
        )
    )
    assert emitter.signals[0].heat != emitter.signals[0].importance


async def test_schedule_use_case_runs_new_domain_with_fact_and_access_traces(tmp_path):
    from aether_agent_memory.adapters.access_trace import InMemoryAccessTraceAdapter
    from aether_agent_memory.adapters.operate_journal import SQLiteActionJournal
    from aether_agent_memory.adapters.operate_scheduler import OperateSchedulerAdapter
    from aether_agent_memory.adapters.operate_telemetry import MemoryControlTelemetry
    from aether_agent_memory.adapters.storage_control_simulator import StorageControlSimulator
    from aether_agent_memory.application.services import ScheduleMemoryUseCase
    from aether_agent_memory.b3 import SchedulableObject, ScheduleRequest
    from aether_agent_memory.core.enums import StorageTier
    from aether_agent_memory.memory.retrieval.models import AccessTrace
    from aether_agent_memory.operate.controller import OperateController
    from aether_agent_memory.operate.models import ActionState, TargetRole
    from aether_agent_memory.operate.policy import PlacementPolicy
    from aether_agent_memory.runtime.dependencies import RuntimeDependencies
    from aether_agent_memory.runtime.request_context import RequestContext

    store = InMemoryMemoryStore()
    memory = Memory(
        id="m",
        type=MemoryType.SEMANTIC,
        tenant_id="t",
        user_id="u",
        agent_id="a",
        session_id="s",
        content="authoritative fact",
        importance=1,
    )
    await store.upsert(memory)
    traces = InMemoryAccessTraceAdapter()
    for i in range(10):
        await traces.record(
            AccessTrace(
                trace_id=str(i),
                request_id=str(i),
                memory_id="m",
                source="context",
                tenant_id="t",
                user_id="u",
                agent_id="a",
                session_id="s",
                score=1,
            )
        )
    sim = StorageControlSimulator()
    sim.set_capacity(PlacementPolicy().cache_target, 1000)
    adapter = OperateSchedulerAdapter(
        store,
        OperateController(sim, SQLiteActionJournal(tmp_path / "actions.db")),
        telemetry=MemoryControlTelemetry(traces, MockSignalEmitter()),
        shadow_mode=False,
    )
    use_case = ScheduleMemoryUseCase(
        RuntimeDependencies(
            embedding=None, memory_events=None, context_builder=None, scheduler=adapter
        )
    )
    context = RequestContext.from_values(tenant_id="t", user_id="u", agent_id="a", session_id="s")
    request = ScheduleRequest(
        objects=[
            SchedulableObject(object_id="m", object_type="memory", current_tier=StorageTier.L2_HDD)
        ]
    )
    result = await use_case.execute(request, context)
    assert result.actions[0].metadata["action_state"] == ActionState.SUCCEEDED
    assert result.actions[0].metadata["operate_action"]["target"]["role"] == TargetRole.CACHE
    placement = next(iter(sim.placements.values()))
    assert {target.role for target in placement.targets} == {
        TargetRole.AUTHORITATIVE,
        TargetRole.CACHE,
    }
    assert sim.submission_count == 1
    await use_case.execute(request.model_copy(update={"request_id": "next"}), context)
    assert sim.submission_count == 1
    await adapter.close()
