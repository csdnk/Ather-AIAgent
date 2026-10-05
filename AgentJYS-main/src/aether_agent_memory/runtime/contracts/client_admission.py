"""An HTTP executor claim; authoritative ownership remains in the transaction store."""

from uuid import UUID

from .models import ContractModel, Identifier, Positive, Scope


class ClientRunAdmission(ContractModel):
    run_id: UUID
    owner_id: Identifier
    revision: Positive


class ClientDocumentTarget(ContractModel):
    scope: Scope
    document_id: Identifier


class ClientOperationTargets(ContractModel):
    scopes: tuple[Scope, ...] = ()
    documents: tuple[ClientDocumentTarget, ...] = ()
