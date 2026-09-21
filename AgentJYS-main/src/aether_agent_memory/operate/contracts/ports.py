"""These are internal service/provider interfaces, not user scheduling endpoints."""

from typing import Protocol

from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import EventEnvelope, TrustedContext
from aether_agent_memory.runtime.contracts.ports import Transaction

from .models import (
    ActionIntent,
    ActionRecord,
    ExecutionFeedback,
    PlacementDecision,
    PlacementObservation,
    ReadProof,
    ResourceSnapshot,
    SchedulingInput,
)


class SchedulingPort(Protocol):
    def consume(self, tx: Transaction, event: EventEnvelope) -> None: ...
    def decide(self, ctx: TrustedContext, inputs: SchedulingInput) -> PlacementDecision: ...
    async def execute(self, ctx: TrustedContext, intent: ActionIntent) -> ActionRecord: ...
    async def reconcile(self, ctx: TrustedContext, action_id: str) -> ActionRecord: ...


class ExecutorPort(Protocol):
    async def submit(self, ctx: TrustedContext, intent: ActionIntent) -> ExecutionFeedback: ...
    async def query(self, ctx: TrustedContext, action_id: str) -> ExecutionFeedback: ...
    async def observe(
        self,
        ctx: TrustedContext,
        memory: MemoryRef,
        representation_id: str,
    ) -> PlacementObservation: ...
    async def verify_read(self, ctx: TrustedContext, intent: ActionIntent) -> ReadProof: ...
    async def resources(self, ctx: TrustedContext) -> ResourceSnapshot: ...
