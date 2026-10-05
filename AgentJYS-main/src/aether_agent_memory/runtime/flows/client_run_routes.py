"""Authenticated caller checkpoint API on the configured P3 transaction store."""

from typing import Any
from uuid import UUID

from fastapi import FastAPI, Response

from aether_agent_memory.runtime.contracts.client_recoveries import (
    ClientRunRecoveryLookup,
    ClientRunRecoveryResult,
    PrepareClientRecovery,
)
from aether_agent_memory.runtime.contracts.client_runs import (
    CheckpointClientRun,
    ClientRunRecord,
    ClientRunRegistration,
    RegisterClientRun,
)
from aether_agent_memory.runtime.contracts.client_transfers import (
    ClientRunTransferLookup,
    ClientRunTransferResult,
    TransferClientRun,
)
from aether_agent_memory.runtime.contracts.models import Identifier, TrustedContext
from aether_agent_memory.runtime.foundation.client_definitions import ClientDefinitions
from aether_agent_memory.runtime.foundation.client_inputs import ClientInputs, InputObjects
from aether_agent_memory.runtime.foundation.client_recoveries import ClientRecoveries
from aether_agent_memory.runtime.foundation.client_recovery_initialization import (
    ClientRecoveryInitialization,
)
from aether_agent_memory.runtime.foundation.client_recovery_reads import ClientRecoveryReads
from aether_agent_memory.runtime.foundation.client_runs import ClientRuns
from aether_agent_memory.runtime.foundation.client_states import ClientStates
from aether_agent_memory.runtime.foundation.client_transfers import ClientTransfers


def attach(app: FastAPI, foundation: Any, *, objects: InputObjects | None, max_bytes: int) -> None:
    from .client_definition_routes import attach as attach_definitions
    from .client_initialization_routes import attach as attach_initialization
    from .client_input_routes import attach as attach_inputs
    from .client_recovery_read_routes import attach as attach_recovery_reads
    from .client_state_routes import attach as attach_states

    registry = ClientRuns(foundation.uow, foundation.identity)
    transfers = ClientTransfers(registry)
    dependency = app.state.trusted_dependency
    inputs = ClientInputs(registry, objects, max_bytes)
    definitions = ClientDefinitions(registry, objects, max_bytes)
    attach_inputs(app, inputs)
    attach_definitions(app, definitions)
    states = ClientStates(registry, objects, max_bytes)
    attach_states(app, states)
    recovery_reads = ClientRecoveryReads(definitions, inputs, states)
    attach_recovery_reads(app, recovery_reads)
    attach_initialization(app, ClientRecoveryInitialization(recovery_reads))
    recoveries = ClientRecoveries(states)

    @app.post("/p3/client-runs/{run_id}/recoveries/{transfer_id}")
    def activate(
        run_id: UUID,
        transfer_id: Identifier,
        body: PrepareClientRecovery,
        response: Response,
        ctx: TrustedContext = dependency,
    ) -> ClientRunRecoveryResult:
        created, result = recoveries.activate(ctx, run_id, transfer_id, body)
        response.status_code = 201 if created else 200
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/p3/client-runs/{run_id}/recoveries/{transfer_id}")
    def lookup_recovery(
        run_id: UUID, transfer_id: Identifier, response: Response, ctx: TrustedContext = dependency
    ) -> ClientRunRecoveryLookup:
        response.headers["Cache-Control"] = "no-store"
        return recoveries.lookup(ctx, run_id, transfer_id)

    @app.post("/p3/client-runs/{run_id}/transfers/{transfer_id}")
    def transfer(
        run_id: UUID,
        transfer_id: Identifier,
        body: TransferClientRun,
        response: Response,
        ctx: TrustedContext = dependency,
    ) -> ClientRunTransferResult:
        created, result = transfers.transfer(ctx, run_id, transfer_id, body)
        response.status_code = 201 if created else 200
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/p3/client-runs/{run_id}/transfers/{transfer_id}")
    def lookup_transfer(
        run_id: UUID, transfer_id: Identifier, response: Response, ctx: TrustedContext = dependency
    ) -> ClientRunTransferLookup:
        response.headers["Cache-Control"] = "no-store"
        return transfers.lookup(ctx, run_id, transfer_id)

    @app.post("/p3/client-runs/{run_id}")
    def register(
        run_id: UUID,
        body: RegisterClientRun,
        response: Response,
        ctx: TrustedContext = dependency,
    ) -> ClientRunRegistration:
        result = registry.register(ctx, run_id, body)
        response.status_code = 201 if result.created else 200
        return result

    @app.get("/p3/client-runs/{run_id}")
    def get(run_id: UUID, ctx: TrustedContext = dependency) -> ClientRunRecord:
        return registry.get(ctx, run_id)

    @app.put("/p3/client-runs/{run_id}")
    def checkpoint(
        run_id: UUID,
        body: CheckpointClientRun,
        ctx: TrustedContext = dependency,
    ) -> ClientRunRecord:
        return registry.checkpoint(ctx, run_id, body)
