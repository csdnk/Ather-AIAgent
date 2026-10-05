"""Seed a pre-existing intent after a real Temporal Activity obtains its fence."""

from uuid import uuid4

from aether_agent_memory.operate.contracts.models import ActionIntent, PlacementDecision
from aether_agent_memory.runtime.foundation.common import fingerprint, now


async def reserve_existing_intent(runtime, tasks, item):
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    observation = await runtime.executor.observe(ctx, item.ref, "original")
    intent = ActionIntent(
        action_id=uuid4().hex,
        decision=PlacementDecision(
            decision_id=uuid4().hex, memory=item.ref, outcome="demote",
            current_tier="hot", target_tier="warm", reason="pre-existing unsupported intent",
            policy_version="test", storage_watermark=1, access_watermark=1,
        ),
        representation_id="original", content_hash=item.content_hash,
        provider_id=runtime.executor.provider_id, provider_instance_id=runtime.executor.instance_id,
        expected_epoch=observation.epoch, provider_mode="real", created_at=now(),
    )
    key = fingerprint([item.ref.scope.model_dump(mode="json"), item.ref.memory_id])
    for task in tasks:
        runtime.operate.reserve_evaluation(ctx, task, key, intent)
    return intent
