"""Persistent caller namespaces; authority comes from registration, never an ID prefix."""

import re
from typing import Literal

from aether_agent_memory.runtime.contracts.client_admission import ClientOperationTargets
from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    ErrorCode,
    Identifier,
    RecordRef,
    Scope,
)
from aether_agent_memory.runtime.contracts.ports import Transaction

from .common import FoundationError

NAMESPACE = r"p4r_[0-9a-f]{32}"


class ClientRunNamespace(ContractModel):
    run_ref: RecordRef
    scope_id: Identifier
    policy: Literal["p4_task_v1"] = "p4_task_v1"


def root_scope(scope: Scope) -> Scope:
    return scope.model_copy(update={"task_id": None, "session_id": None})


def namespace_ref(scope: Scope, scope_id: str) -> RecordRef:
    return RecordRef(
        owner="runtime",
        object_type="client_run_namespace",
        object_id=scope_id,
        scope=root_scope(scope),
    )


def register_namespace(tx: Transaction, run_ref: RecordRef, run: ClientRunRecord) -> None:
    if run.scope_policy is None:
        return
    if run_ref.scope != root_scope(run_ref.scope):
        raise FoundationError(ErrorCode.FORBIDDEN, "managed run requires an unbound home scope")
    value = ClientRunNamespace(run_ref=run_ref, scope_id=run.scope_id)
    ref = namespace_ref(run_ref.scope, run.scope_id)
    if tx.get(ref) is not None:
        raise FoundationError(ErrorCode.VERSION_CONFLICT, "caller namespace already reserved")
    tx.put_if_revision(ref, value.model_dump(mode="json"), None)


def _namespace(value: str | None, suffix: str = "") -> str | None:
    match = re.fullmatch(f"({NAMESPACE}){suffix}", value or "")
    return None if match is None else match[1]


def require_targets(
    tx: Transaction,
    targets: ClientOperationTargets | None,
    run: ClientRunRecord | None,
    run_ref: RecordRef | None,
) -> None:
    managed = run is not None and run.scope_policy == "p4_task_v1"
    if targets is None or (not targets.scopes and not targets.documents):
        if managed:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "managed operation target missing")
        return

    def check(scope: Scope, scope_id: str | None, valid_shape: bool) -> None:
        if scope_id is None:
            if managed:
                raise FoundationError(ErrorCode.FORBIDDEN, "target outside caller namespace")
            return
        raw = tx.get(namespace_ref(scope, scope_id))
        if raw is None or not managed or run_ref is None or run is None:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "target requires registered caller")
        reservation = ClientRunNamespace.model_validate(raw)
        if (
            not valid_shape
            or run.scope_id != scope_id
            or reservation.scope_id != scope_id
            or reservation.run_ref != run_ref
            or root_scope(scope) != root_scope(run_ref.scope)
        ):
            raise FoundationError(ErrorCode.FORBIDDEN, "target differs from original caller scope")

    for scope in targets.scopes:
        task = _namespace(scope.task_id)
        session = _namespace(scope.session_id, "_session")
        scope_id = task or session
        check(
            scope,
            scope_id,
            scope.task_id == scope_id
            and scope.session_id
            in {
                None,
                f"{scope_id}_session",
            },
        )
    for document in targets.documents:
        scope_id = _namespace(document.document_id, r"(?:_[A-Za-z0-9_-]+)?")
        check(document.scope, scope_id, document.scope == root_scope(document.scope))
