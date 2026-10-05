"""Explicit binary recovery reads; ordinary confirmed-object routes stay strict."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Path, Query, Response

from aether_agent_memory.runtime.contracts.client_inputs import ClientInputReceipt
from aether_agent_memory.runtime.contracts.client_recovery_reads import (
    ReadClientRecovery,
    RecoveredClientObject,
)
from aether_agent_memory.runtime.contracts.models import Digest, Identifier, TrustedContext
from aether_agent_memory.runtime.foundation.client_recovery_inputs import ClientRecoveryInputs
from aether_agent_memory.runtime.foundation.client_recovery_reads import ClientRecoveryReads


def response(value: RecoveredClientObject) -> Response:
    return Response(
        value.payload,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-P3-Recovery-Object": value.evidence.model_dump_json(),
        },
    )


def expectation(
    expected_owner_id: Identifier,
    expected_revision: Annotated[int, Query(ge=1)],
    expected_record_hash: Digest,
) -> ReadClientRecovery:
    return ReadClientRecovery(
        expected_owner_id=expected_owner_id,
        expected_revision=expected_revision,
        expected_record_hash=expected_record_hash,
    )


def attach(app: FastAPI, reads: ClientRecoveryReads) -> None:
    dependency = app.state.trusted_dependency
    inputs = ClientRecoveryInputs(reads)

    @app.put("/p3/client-runs/{run_id}/recoveries/{transfer_id}/inputs/{operation_id}")
    def confirm_input(
        run_id: UUID,
        transfer_id: Identifier,
        operation_id: Identifier,
        response: Response,
        expected: Annotated[ReadClientRecovery, Depends(expectation)],
        ctx: TrustedContext = dependency,
    ) -> ClientInputReceipt:
        response.headers["Cache-Control"] = "no-store"
        return inputs.confirm(ctx, run_id, transfer_id, operation_id, expected)

    @app.get("/p3/client-runs/{run_id}/recoveries/{transfer_id}/definition")
    def definition(
        run_id: UUID,
        transfer_id: Identifier,
        expected: Annotated[ReadClientRecovery, Depends(expectation)],
        ctx: TrustedContext = dependency,
    ) -> Response:
        return response(reads.read(ctx, run_id, transfer_id, expected, "definition"))

    @app.get("/p3/client-runs/{run_id}/recoveries/{transfer_id}/inputs/{operation_id}")
    def input_bytes(
        run_id: UUID,
        transfer_id: Identifier,
        operation_id: Identifier,
        expected: Annotated[ReadClientRecovery, Depends(expectation)],
        ctx: TrustedContext = dependency,
    ) -> Response:
        return response(
            reads.read(ctx, run_id, transfer_id, expected, "input", operation_id=operation_id)
        )

    @app.get("/p3/client-runs/{run_id}/recoveries/{transfer_id}/states/{sequence}")
    def state(
        run_id: UUID,
        transfer_id: Identifier,
        sequence: Annotated[int, Path(ge=1, le=1024)],
        expected: Annotated[ReadClientRecovery, Depends(expectation)],
        stream_id: Identifier | None = None,
        ctx: TrustedContext = dependency,
    ) -> Response:
        return response(
            reads.read(
                ctx, run_id, transfer_id, expected, "state", sequence=sequence, stream_id=stream_id
            )
        )
