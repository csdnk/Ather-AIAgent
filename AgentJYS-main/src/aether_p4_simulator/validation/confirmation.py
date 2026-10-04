"""Compare two independent request records without changing either record."""

from typing import Literal

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from aether_agent_memory.runtime.contracts.http_evidence import effective_http_target
from aether_agent_memory.runtime.contracts.models import ContractModel, Identifier
from aether_agent_memory.runtime.contracts.mutation_receipts import MutationLookup
from aether_agent_memory.runtime.contracts.operation_lookup import OperationLookup


class RequestConfirmation(ContractModel):
    operation_id: Identifier
    state: Literal["matched", "mismatch", "unconfirmed"]
    lookup: OperationLookup | MutationLookup | None


def compare_request(
    intent: ClientOperation, lookup: OperationLookup | MutationLookup
) -> RequestConfirmation:
    tasks: tuple[str, ...]
    if isinstance(lookup, OperationLookup):
        evidence = lookup.http_request
        tasks = () if lookup.job_id is None else (lookup.job_id,)
    else:
        evidence = lookup.receipt.http_request if lookup.receipt else None
        tasks = lookup.receipt.task_ids if lookup.receipt else ()
    state: Literal["matched", "mismatch", "unconfirmed"] = "unconfirmed"
    if intent.binding is not None and evidence is not None:
        try:
            target_matches = (
                effective_http_target(intent.path, intent.binding.target) == evidence.target
            )
        except ValueError:
            # A journal can preserve a rejected request without required query parameters.
            target_matches = False
        equal = (
            intent.operation_id == lookup.operation_id
            and intent.method == evidence.method
            and intent.path == evidence.route
            and intent.request_hash == evidence.body_hash
            and intent.binding.content_type == evidence.content_type
            and target_matches
            and (intent.job_id is None or intent.job_id in tasks)
        )
        state = "matched" if equal else "mismatch"
    return RequestConfirmation(operation_id=intent.operation_id, state=state, lookup=lookup)
