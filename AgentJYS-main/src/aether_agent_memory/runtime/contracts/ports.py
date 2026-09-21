"""Interfaces only. Implementations must not infer business success from run return."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from pydantic import JsonValue

from .models import (
    DiagnosticRecord,
    EventEnvelope,
    HealthReport,
    Identifier,
    Lease,
    MaintenanceRecord,
    OperationRecord,
    PageRequest,
    Permission,
    Principal,
    RecordPage,
    RecordRef,
    RecoveryDecision,
    RecoveryRequest,
    RelatedRecords,
    RunResult,
    Scope,
    TaskRecord,
    TaskSpec,
    TrustedContext,
)


class Transaction(Protocol):
    def get(self, ref: RecordRef) -> dict[str, JsonValue] | None: ...
    def put_if_revision(
        self,
        ref: RecordRef,
        value: dict[str, JsonValue],
        expected_revision: int | None,
    ) -> int: ...
    def remove_if_revision(self, ref: RecordRef, expected_revision: int) -> None: ...
    def scan_page(self, owner: str, scope: Scope, page: PageRequest) -> RecordPage: ...


class UnitOfWork(Protocol):
    def transaction(self) -> AbstractContextManager[Transaction]: ...


class IdentityPort(Protocol):
    def authenticate(self, credential: str) -> Principal: ...
    def authorize(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        permission: Permission,
        target: RecordRef,
    ) -> None: ...
    def revalidate(self, tx: Transaction, ctx: TrustedContext) -> None: ...


class TaskHandler(Protocol):
    async def run(self, ctx: TrustedContext, task: TaskRecord) -> RunResult: ...
    async def recover(self, ctx: TrustedContext, task: TaskRecord) -> RecoveryDecision: ...


class TaskPort(Protocol):
    def register(self, kind: str, execution_class: str, handler: TaskHandler) -> None: ...
    def enqueue(self, tx: Transaction, ctx: TrustedContext, spec: TaskSpec) -> TaskRecord: ...
    def claim(self, worker_id: str, execution_class: str, now: str) -> TaskRecord | None: ...
    def renew(self, tx: Transaction, task_id: str, lease: Lease, revision: int) -> TaskRecord: ...
    def complete(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        task: TaskRecord,
        result: RecordRef,
    ) -> None: ...
    def request_recovery(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        request: RecoveryRequest,
    ) -> OperationRecord: ...


class EventPort(Protocol):
    def append(self, tx: Transaction, ctx: TrustedContext, event: EventEnvelope) -> None: ...
    def consume(
        self,
        tx: Transaction,
        consumer_id: str,
        event: EventEnvelope,
        apply: Callable[[Transaction, EventEnvelope], None],
    ) -> bool: ...


class DiagnosticsPort(Protocol):
    def record(self, tx: Transaction, record: DiagnosticRecord) -> None: ...
    def record_maintenance(self, tx: Transaction, record: MaintenanceRecord) -> None: ...
    def related(
        self,
        ctx: TrustedContext,
        subject: RecordRef,
        page: PageRequest,
    ) -> RelatedRecords: ...
    def health(self, ctx: TrustedContext) -> HealthReport: ...
    def operation(self, ctx: TrustedContext, operation_id: Identifier) -> OperationRecord: ...
    def task(self, ctx: TrustedContext, task_id: Identifier) -> TaskRecord: ...
