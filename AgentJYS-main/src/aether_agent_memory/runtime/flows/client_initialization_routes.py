"""Explicit confirmation under recovery ownership, before local execution may resume."""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Request, Response

from aether_agent_memory.runtime.contracts.client_definitions import ClientDefinitionReceipt
from aether_agent_memory.runtime.contracts.client_recovery_reads import ReadClientRecovery
from aether_agent_memory.runtime.contracts.models import ErrorCode, Identifier, TrustedContext
from aether_agent_memory.runtime.foundation.client_recovery_initialization import (
    ClientRecoveryInitialization,
)
from aether_agent_memory.runtime.foundation.common import FoundationError

from .client_recovery_read_routes import expectation


def attach(app: FastAPI, initialization: ClientRecoveryInitialization) -> None:
    dependency = app.state.trusted_dependency

    @app.put("/p3/client-runs/{run_id}/recoveries/{transfer_id}/definition")
    async def confirm(
        run_id: UUID,
        transfer_id: Identifier,
        request: Request,
        response: Response,
        expected: Annotated[ReadClientRecovery, Depends(expectation)],
        ctx: TrustedContext = dependency,
    ) -> ClientDefinitionReceipt:
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > initialization.definitions.max_bytes:
                raise FoundationError(
                    ErrorCode.INVALID_ARGUMENT, "caller definition exceeds ingress limit"
                )
            payload.extend(chunk)
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(
            initialization.confirm,
            ctx,
            run_id,
            transfer_id,
            expected,
            bytes(payload) if payload else None,
        )
