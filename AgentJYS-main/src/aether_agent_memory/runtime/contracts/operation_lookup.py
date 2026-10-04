"""Read-only original-job discovery; absence never authorizes a retry."""

from typing import Literal, Self

from pydantic import model_validator

from .http_evidence import HttpRequestEvidence
from .models import ContractModel, Digest, Identifier, NonEmpty, TaskState

HTTPCommandKind = Literal[
    "remember.save", "recall.execute", "remember.correct", "remember.document"
]


class OperationLookup(ContractModel):
    operation_id: Identifier
    kind: HTTPCommandKind
    state: Literal["found", "unconfirmed"]
    job_id: Identifier | None = None
    task_state: TaskState | None = None
    input_hash: Digest | None = None
    workflow_id: NonEmpty | None = None
    http_request: HttpRequestEvidence | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        values = (self.job_id, self.task_state, self.input_hash, self.workflow_id)
        if self.state == "found" and any(value is None for value in values):
            raise ValueError("found operation requires original job and durable binding")
        if self.state == "unconfirmed" and any(value is not None for value in values):
            raise ValueError("unconfirmed lookup cannot claim a job binding")
        if self.http_request is not None and (
            self.state != "found" or not self.http_request.matches_kind(self.kind)
        ):
            raise ValueError("HTTP evidence differs from its original command")
        return self
