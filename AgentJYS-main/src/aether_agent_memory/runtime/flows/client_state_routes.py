"""Bounded, authenticated original execution-state bytes and fenced head writes."""

import asyncio
from hashlib import sha256
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, Path, Request, Response

from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_agent_memory.runtime.contracts.models import ErrorCode, Identifier, TrustedContext
from aether_agent_memory.runtime.foundation.client_states import ClientStates
from aether_agent_memory.runtime.foundation.common import FoundationError


def attach(app: FastAPI, states: ClientStates) -> None:
    dependency = app.state.trusted_dependency

    @app.put("/p3/client-runs/{run_id}/states/{sequence}")
    async def prepare(
        run_id: UUID,
        sequence: Annotated[int, Path(ge=1, le=1024)],
        request: Request,
        response: Response,
        owner: Annotated[Identifier, Header(alias="X-P3-Run-Owner")],
        revision: Annotated[int, Header(alias="X-P3-Run-Revision", ge=1)],
        ctx: TrustedContext = dependency,
    ) -> ClientRunRecord:
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > states.max_bytes:
                raise FoundationError(
                    ErrorCode.INVALID_ARGUMENT, "execution state exceeds ingress limit"
                )
            payload.extend(chunk)
        result = await asyncio.to_thread(
            states.prepare, ctx, run_id, sequence, owner, revision, bytes(payload)
        )
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/p3/client-runs/{run_id}/states/{sequence}")
    def read(
        run_id: UUID,
        sequence: Annotated[int, Path(ge=1, le=1024)],
        stream_id: Identifier | None = None,
        ctx: TrustedContext = dependency,
    ) -> Response:
        payload = states.read(ctx, run_id, sequence, stream_id)
        return Response(
            payload,
            media_type="application/json",
            headers={
                "X-P3-State-Hash": sha256(payload).hexdigest(),
                "Cache-Control": "no-store",
            },
        )
