"""Atomic Recall admission and current-authority result access."""

import asyncio

from aether_agent_memory.recall.basic.service import Recall
from aether_agent_memory.recall.contracts.models import ContextPack, RecallRequest
from aether_agent_memory.runtime.contracts.client_admission import ClientOperationTargets
from aether_agent_memory.runtime.contracts.http_evidence import HttpRequestEvidence
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Flow,
    Permission,
    TaskSpec,
    TaskState,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.tasks import TERMINAL

from .ingress import WorkflowOnlyHandler
from .ledger import ExecutionLedger
from .models import WorkflowInput
from .registry import StageRegistry


def recall_binding(recall: Recall) -> str:
    return fingerprint(
        [
            type(recall).__name__,
            recall.policy_version,
            recall.settings.model_dump(mode="json"),
            recall.model_space,
            recall.tokenizer.identifier,
            getattr(recall.reranker, "identifier", None),
        ]
    )


class RecallAdmission:
    def __init__(self, ledger: ExecutionLedger, recall: Recall) -> None:
        self.ledger, self.recall = ledger, recall
        if "recall.execute" not in ledger.tasks.handlers:
            ledger.tasks.register(
                "recall.execute", "recall", WorkflowOnlyHandler(), permission=Permission.READ
            )

    def accept(
        self,
        ctx: TrustedContext,
        request: RecallRequest,
        *,
        http_request: HttpRequestEvidence | None = None,
    ) -> WorkflowInput:
        with self.recall.uow.transaction() as tx:
            record, previous = self.recall.accept_in(tx, ctx, request)
            subject = self.recall.ref(record)
            input_ref = subject.model_copy(update={"object_type": "recall_input"})
            value = tx.get(input_ref)
            if value is None:
                value = {
                    "recall_id": record.recall_id,
                    "request": request.model_dump(mode="json"),
                    "deadline_at": record.deadline_at,
                    "binding": recall_binding(self.recall),
                }
                tx.put_if_revision(input_ref, value, None)
            return self.ledger.admit(
                tx,
                ctx,
                TaskSpec(
                    task_id=record.recall_id,
                    owner_flow=Flow.RECALL,
                    kind="recall.execute",
                    subject=subject,
                    input_ref=input_ref,
                    idempotency_key=record.recall_id,
                    input_hash=fingerprint(value),
                    initiator_id=ctx.principal.principal_id,
                    initiator_auth_epoch=ctx.principal.auth_epoch,
                    deadline_at=record.deadline_at,
                    max_attempts=self.ledger.tasks.max_attempts,
                ),
                http_request=http_request,
                client_targets=ClientOperationTargets(scopes=(record.scope,)),
            )

    def read_result(self, ctx: TrustedContext, job_id: str) -> ContextPack:
        with self.recall.uow.transaction() as tx:
            _, task = self.ledger.tasks.load(tx, job_id)
            self.recall.identity.authorize(tx, ctx, Permission.READ, task.subject)
            if task.kind != "recall.execute":
                tx.abort(ErrorCode.INVALID_ARGUMENT, "not a Recall job")
            if task.state != TaskState.SUCCEEDED:
                tx.abort(
                    ErrorCode.EXECUTION_INTERRUPTED
                    if task.state in TERMINAL
                    else ErrorCode.REQUEST_IN_PROGRESS,
                    "Recall has no committed result",
                )
            input_value = tx.get(task.input_ref)
            if input_value is None:
                tx.abort(ErrorCode.CONTRACT_VIOLATION, "Recall input missing")
            if fingerprint(input_value) != task.input_hash:
                tx.abort(ErrorCode.VERSION_CONFLICT, "Recall task input changed")
            recall_id = str(input_value["recall_id"])
        return self.recall.result(ctx, recall_id)

    async def wait_result(self, ctx: TrustedContext, job_id: str) -> ContextPack:
        while True:
            try:
                return self.read_result(ctx, job_id)
            except FoundationError as exc:
                if exc.code != ErrorCode.REQUEST_IN_PROGRESS:
                    raise
            await asyncio.sleep(0.05)


def register_recall(registry: StageRegistry, recall: Recall) -> None:
    from aether_agent_memory.recall.basic.generation import GenerationRecall
    from aether_agent_memory.recall.basic.temporal_stages import GenerationStages, RecallStages

    stages = (
        GenerationStages(recall) if isinstance(recall, GenerationRecall) else RecallStages(recall)
    )
    registry.register(
        "recall.execute",
        "prepare",
        stages.prepare,
        stages.prepare,
        Permission.READ,
        "read",
        timeout_seconds=300,
    )
    registry.register(
        "recall.execute",
        "candidates",
        stages.candidates,
        stages.candidates,
        Permission.READ,
        "read",
        timeout_seconds=300,
    )
    registry.register(
        "recall.execute",
        "assemble",
        stages.assemble,
        stages.assemble,
        Permission.READ,
        "read",
        timeout_seconds=300,
    )
    registry.register(
        "recall.execute",
        "commit",
        stages.commit,
        stages.commit,
        Permission.READ,
        "idempotent",
        timeout_seconds=300,
    )
