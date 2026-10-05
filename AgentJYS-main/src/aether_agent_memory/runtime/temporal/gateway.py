"""Temporal RPC transport; callers must leave business transactions before awaiting it."""

import asyncio
from datetime import timedelta
from typing import Any

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode, TLSConfig

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.tasks import TERMINAL
from aether_agent_memory.runtime.foundation.transactions import _active

from .config import TemporalConfiguration
from .ledger import ExecutionLedger
from .models import ControlIntent, ExecutionStatus, StartIntent, WorkflowBinding


async def connect_client(config: TemporalConfiguration) -> Client:
    tls: TLSConfig | bool = False
    if config.tls_ca_file or config.tls_cert_file:
        tls = TLSConfig(
            server_root_ca_cert=config.tls_ca_file.read_bytes() if config.tls_ca_file else None,
            client_cert=config.tls_cert_file.read_bytes() if config.tls_cert_file else None,
            client_private_key=config.tls_key_file.read_bytes() if config.tls_key_file else None,
        )
    return await asyncio.wait_for(
        Client.connect(
            config.endpoint,
            namespace=config.namespace,
            data_converter=pydantic_data_converter,
            tls=tls,
        ),
        timeout=config.connect_timeout_seconds,
    )


class TemporalGateway:
    def __init__(self, client: Client, ledger: ExecutionLedger) -> None:
        self.client, self.ledger = client, ledger
        if client.namespace != ledger.config.namespace:
            raise ValueError("Temporal client namespace differs from deployment")
        self.timeout = timedelta(seconds=ledger.config.connect_timeout_seconds)

    @staticmethod
    def outside_transaction() -> None:
        if _active.get():
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "RPC inside business transaction")

    async def start(self, intent: StartIntent) -> WorkflowBinding:
        from .workflows import workflow_name

        self.outside_transaction()

        def load_start() -> tuple[Any, WorkflowBinding | None]:
            with self.ledger.tasks.uow.transaction() as tx:
                row = self.ledger.verify_job(tx, intent.job)
                task_row, task = self.ledger.tasks.load(tx, intent.job.job_id)
                if task.state in TERMINAL:
                    tx.abort(ErrorCode.CONTRACT_VIOLATION, "terminal business work cannot restart")
                if (
                    intent.task_queue
                    != f"{self.ledger.config.task_queue_prefix}.{task_row['class']}"
                ):
                    tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "workflow task queue changed")
                expected = (
                    WorkflowBinding.model_validate(row["binding"]) if row["binding"] else None
                )
            return row, expected

        row, expected = await asyncio.to_thread(load_start)
        workflow_id = row["workflow_id"]
        run_id = expected.current_run_id if expected else row.get("observed_run_id")
        if not run_id:
            try:
                handle = await self.client.start_workflow(
                    workflow_name(intent.job.kind),
                    intent.job,
                    id=workflow_id,
                    task_queue=intent.task_queue,
                    memo={"p3_binding": intent.job.model_dump(mode="json")},
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                    rpc_timeout=self.timeout,
                )
                run_id = handle.first_execution_run_id
            except WorkflowAlreadyStartedError as exc:
                run_id = exc.run_id
        try:
            description = await self.client.get_workflow_handle(
                workflow_id, run_id=run_id
            ).describe(rpc_timeout=self.timeout)
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                raise FoundationError(
                    ErrorCode.NOT_FOUND, "bound execution history missing"
                ) from None
            raise
        memo = await description.memo_value("p3_binding", None)
        first = description.raw_info.first_run_id or description.run_id
        if (
            memo != intent.job.model_dump(mode="json")
            or description.task_queue != intent.task_queue
            or description.workflow_type != workflow_name(intent.job.kind)
            or (
                expected
                and (
                    expected.first_run_id != first or expected.current_run_id != description.run_id
                )
            )
        ):
            raise FoundationError(
                ErrorCode.IDEMPOTENCY_CONFLICT, "existing workflow binding differs"
            )
        return WorkflowBinding(
            namespace=self.client.namespace,
            workflow_id=workflow_id,
            first_run_id=first,
            current_run_id=description.run_id,
            input_hash=intent.job.input_hash,
            plan_version=intent.job.plan_version,
        )

    async def send_control(self, intent: ControlIntent) -> None:
        from .controls import authorize_delivery

        self.outside_transaction()

        def load_control() -> tuple[bool, WorkflowBinding]:
            with self.ledger.tasks.uow.transaction() as tx:
                control = tx.read("temporal_control_intents", intent.control_id)
                periodic = bool(control and control.get("target") == "periodic")
                if control and "context" in control:
                    if control["intent"] != intent.model_dump(mode="json"):
                        tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "control changed")
                    authorize_delivery(self.ledger, tx, control)
                row = tx.read(
                    "temporal_periodic_binding" if periodic else "temporal_bindings", intent.job_id
                )
                if not row or not row["binding"]:
                    raise ConnectionError("start acknowledgement pending")
                binding = WorkflowBinding.model_validate(row["binding"])
                if control and "binding" in control:
                    original = WorkflowBinding.model_validate(control["binding"])
                    if (
                        original.model_copy(update={"current_run_id": binding.current_run_id})
                        != binding
                    ):
                        tx.abort(ErrorCode.VERSION_CONFLICT, "control original chain changed")
            return periodic, binding

        periodic, binding = await asyncio.to_thread(load_control)
        status = await self.describe_periodic(binding) if periodic else await self.describe(binding)
        if status.state != "running":
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "closed workflow cannot accept control"
            )
        try:
            await self.client.get_workflow_handle(
                binding.workflow_id,
                first_execution_run_id=binding.first_run_id,
            ).signal("control", intent, rpc_timeout=self.timeout)
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                raise FoundationError(ErrorCode.NOT_FOUND, "original workflow is closed") from None
            raise

    async def describe(self, binding: WorkflowBinding) -> ExecutionStatus:
        self.outside_transaction()
        if binding.namespace != self.client.namespace:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "execution namespace differs")
        try:
            description = await self.client.get_workflow_handle(
                binding.workflow_id,
                run_id=binding.current_run_id,
            ).describe(rpc_timeout=self.timeout)
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return ExecutionStatus(state="not_found", run_id=binding.current_run_id)
            raise
        if (description.raw_info.first_run_id or description.run_id) != binding.first_run_id:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "execution chain differs")
        if description.status == WorkflowExecutionStatus.CONTINUED_AS_NEW:
            original = description
            description = await self.client.get_workflow_handle(binding.workflow_id).describe(
                rpc_timeout=self.timeout
            )
            if (
                (description.raw_info.first_run_id or description.run_id) != binding.first_run_id
                or description.workflow_type != original.workflow_type
                or description.task_queue != original.task_queue
                or await description.memo() != await original.memo()
            ):
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "continued execution binding differs"
                )
        return self.execution_status(description.status, description.run_id)

    async def describe_periodic(self, binding: WorkflowBinding) -> ExecutionStatus:
        """Resolve the live successor without requiring an expired ancestor's history."""
        self.outside_transaction()
        if (
            binding.namespace != self.client.namespace
            or binding.workflow_id != f"p3/{self.ledger.config.deployment_id}/periodic"
            or binding.plan_version != "1"
        ):
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "periodic binding differs")
        try:
            description = await self.client.get_workflow_handle(binding.workflow_id).describe(
                rpc_timeout=self.timeout
            )
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return ExecutionStatus(state="not_found")
            raise
        if (
            (description.raw_info.first_run_id or description.run_id) != binding.first_run_id
            or description.workflow_type != "P3PeriodicWorkflow"
            or description.task_queue != f"{self.ledger.config.task_queue_prefix}.periodic"
            or await description.memo_value("p3_periodic", None) != binding.input_hash
        ):
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "periodic successor binding differs")
        return self.execution_status(description.status, description.run_id)

    @staticmethod
    def execution_status(status: WorkflowExecutionStatus | None, run_id: str) -> ExecutionStatus:
        states = {
            WorkflowExecutionStatus.RUNNING: "running",
            WorkflowExecutionStatus.COMPLETED: "completed",
            WorkflowExecutionStatus.FAILED: "failed",
            WorkflowExecutionStatus.CANCELED: "cancelled",
            WorkflowExecutionStatus.TERMINATED: "terminated",
            WorkflowExecutionStatus.TIMED_OUT: "timed_out",
        }
        if status is None or status not in states:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "execution status requires reconciliation"
            )
        return ExecutionStatus.model_validate({"state": states[status], "run_id": run_id})
