"""Immutable request inputs and durable command admission before external I/O."""

import os
import secrets
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Flow,
    Permission,
    RecordRef,
    RecoveryDecision,
    RunResult,
    TaskRecord,
    TaskSpec,
    TaskState,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.storage import SQLiteUnitOfWork

from .ledger import ExecutionLedger
from .models import WorkflowInput
from .registry import StageRegistry


class InputStore:
    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        root: Path,
        *,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.uow, self.identity, self.root, self.max_bytes = (
            uow,
            identity,
            root.resolve(),
            max_bytes,
        )
        self.root.mkdir(parents=True, exist_ok=True)

    def persist(
        self, ctx: TrustedContext, operation: str, payload: bytes, media_type: str
    ) -> RecordRef:
        if not operation or not payload or len(payload) > self.max_bytes:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "input size or operation is invalid")
        ref = RecordRef(
            owner=Flow.RUNTIME,
            object_type="command_input",
            object_id=fingerprint(
                [
                    ctx.principal.principal_id,
                    ctx.principal.home_scope.model_dump(mode="json"),
                    operation,
                ]
            ),
            scope=ctx.principal.home_scope,
        )
        digest = sha256(payload).hexdigest()
        value: dict[str, JsonValue] = {
            "operation_id": operation,
            "hash": digest,
            "bytes": len(payload),
            "media_type": media_type,
        }
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.WRITE, ref)
            previous = tx.get(ref)
            if previous is not None and previous != value:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "operation input is immutable")
        target = self.root / digest
        temporary = self.root / ("pending-" + secrets.token_hex(16))
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists() and target.read_bytes() == payload:
                temporary.unlink()
            else:
                os.replace(temporary, target)
            if os.name != "nt":
                descriptor = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            self.commit_input(ctx, ref, value)
        finally:
            temporary.unlink(missing_ok=True)
        return ref

    def commit_input(
        self, ctx: TrustedContext, ref: RecordRef, value: dict[str, JsonValue]
    ) -> None:
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.WRITE, ref)
            old = tx.get(ref)
            if old is not None and old != value:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "operation input changed concurrently")
            if old is None:
                tx.put_if_revision(ref, value, None)
            tx.before_commit.append(lambda: self.identity.revalidate(tx, ctx))

    def read(self, ctx: TrustedContext, ref: RecordRef) -> bytes:
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.READ, ref)
            row = tx.get(ref)
            if row is None:
                tx.abort(ErrorCode.NOT_FOUND, "command input missing")
        digest = row["hash"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid input reference")
        try:
            content = (self.root / digest).read_bytes()
        except OSError:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "durable input missing") from None
        if sha256(content).hexdigest() != digest or len(content) != row["bytes"]:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "durable input hash differs")
        return content


class CommandAdmission:
    def __init__(self, ledger: ExecutionLedger, inputs: InputStore) -> None:
        self.ledger, self.inputs = ledger, inputs

    def accept(self, ctx: TrustedContext, kind: str, input_ref: RecordRef) -> WorkflowInput:
        self.inputs.read(ctx, input_ref)
        with self.inputs.uow.transaction() as tx:
            content = tx.get(input_ref)
            if content is None or content["operation_id"] != ctx.operation_id:
                tx.abort(ErrorCode.CONTRACT_VIOLATION, "input belongs to another operation")
            job_id = fingerprint([input_ref.model_dump(mode="json"), kind])
            flow = Flow(kind.split(".")[0])
            subject = RecordRef(
                owner=flow, object_type="command", object_id=job_id, scope=input_ref.scope
            )
            return self.ledger.admit(
                tx,
                ctx,
                TaskSpec(
                    task_id=job_id,
                    owner_flow=flow,
                    kind=kind,
                    subject=subject,
                    input_ref=input_ref,
                    idempotency_key=job_id,
                    input_hash=fingerprint(content),
                    initiator_id=ctx.principal.principal_id,
                    initiator_auth_epoch=ctx.principal.auth_epoch,
                    deadline_at=ctx.deadline_at,
                    max_attempts=self.ledger.tasks.max_attempts,
                ),
            )

    def result(self, ctx: TrustedContext, job_id: str) -> dict[str, JsonValue] | None:
        with self.inputs.uow.transaction() as tx:
            _, task = self.ledger.tasks.load(tx, job_id)
            self.inputs.identity.authorize(tx, ctx, Permission.READ, task.subject)
            if task.state != TaskState.SUCCEEDED or task.result_ref is None:
                return None
            return tx.get(task.result_ref)


class WorkflowOnlyHandler:
    """Declare a command in the business catalog without an RF execution path."""

    async def run(self, ctx: TrustedContext, task: TaskRecord) -> RunResult:
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "command requires Temporal")

    async def recover(self, ctx: TrustedContext, task: TaskRecord) -> RecoveryDecision:
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "command requires Temporal")


def register_ingress(
    registry: StageRegistry, ledger: ExecutionLedger, inputs: InputStore, remember: Any
) -> None:
    from aether_agent_memory.remember.basic.temporal_stages import (
        CorrectionStages,
        DocumentStages,
        SaveStages,
    )

    remember.bodies.require_prepared = True
    # Commands must execute independently of long-running model activities.
    # Existing task rows retain their stored class and continue on the old queue.
    ledger.tasks.class_limits.setdefault(
        "remember_ingress", ledger.tasks.class_limits.get("remember", 2)
    )
    for kind, save, permission in (
        ("remember.save", SaveStages(inputs, remember), Permission.WRITE),
        ("remember.document", DocumentStages(inputs, remember), Permission.WRITE),
        ("remember.correct", CorrectionStages(inputs, remember), Permission.CORRECT),
    ):
        ledger.tasks.register(
            kind, "remember_ingress", WorkflowOnlyHandler(), permission=permission
        )
        registry.register(kind, "prepare", save.prepare, save.prepare, permission, "idempotent")
        registry.register(
            kind, "persist", save.persist, save.reconcile_persist, permission, "uncertain"
        )
        registry.register(kind, "commit", save.commit, save.commit, permission, "idempotent")
        registry.register(
            kind, "admit_cache", save.admit_cache, save.admit_cache, permission, "idempotent"
        )
