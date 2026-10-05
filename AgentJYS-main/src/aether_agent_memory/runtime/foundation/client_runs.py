"""Atomic caller registration over the existing transaction/identity contracts."""

from collections.abc import Callable
from uuid import UUID

from pydantic import JsonValue

from aether_agent_memory.runtime.contracts.client_ownership import ClientOwnerEpoch, ClientOwnership
from aether_agent_memory.runtime.contracts.client_runs import (
    CheckpointClientRun,
    ClientOperation,
    ClientRunRecord,
    ClientRunRegistration,
    RegisterClientRun,
)
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Permission,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import IdentityPort, Transaction, UnitOfWork

from .client_scopes import register_namespace
from .common import FoundationError, fingerprint, now

TERMINAL = {"passed", "failed", "blocked", "unconfirmed"}


class ClientRuns:
    def __init__(self, uow: UnitOfWork, identity: IdentityPort, *, clock: Callable[[], str] = now):
        self.uow, self.identity, self.clock = uow, identity, clock

    @staticmethod
    def ref(ctx: TrustedContext, run_id: UUID | None) -> RecordRef:
        return RecordRef(
            owner="runtime",
            object_type="client_run" if run_id else "client_run_index",
            object_id=fingerprint([ctx.principal.principal_id, str(run_id) if run_id else "index"]),
            scope=ctx.principal.home_scope,
        )

    def _read(self, tx: Transaction, ctx: TrustedContext, run_id: UUID) -> ClientRunRecord:
        ref = self.ref(ctx, run_id)
        self.identity.authorize(tx, ctx, Permission.DIAGNOSE, ref)
        raw = tx.get(ref)
        if raw is None:
            raise FoundationError(ErrorCode.NOT_FOUND, "caller run not registered")
        record = ClientRunRecord.model_validate(raw)
        if record.auth_epoch != ctx.principal.auth_epoch:
            raise FoundationError(ErrorCode.FORBIDDEN, "caller run identity epoch changed")
        return record

    @staticmethod
    def _snapshot_matches(run_id: UUID, scenario: str, snapshot: dict[str, JsonValue]) -> None:
        if snapshot.get("run_id") != str(run_id) or snapshot.get("scenario_id") != scenario:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "caller snapshot binding changed")

    def get(self, ctx: TrustedContext, run_id: UUID) -> ClientRunRecord:
        with self.uow.transaction() as tx:
            return self._read(tx, ctx, run_id)

    @staticmethod
    def _progress(prior: ClientRunRecord, snapshot: dict[str, JsonValue]) -> None:
        before = prior.snapshot.get("operations", [])
        after = snapshot.get("operations", [])
        assert isinstance(before, list) and isinstance(after, list)
        old = {item.operation_id: item for item in map(ClientOperation.model_validate, before)}
        new = {item.operation_id: item for item in map(ClientOperation.model_validate, after)}
        if list(new)[: len(old)] != list(old):
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "original caller operation order cannot change"
            )
        if prior.snapshot["state"] == "running" and snapshot["state"] == "queued":
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "caller progress cannot rewind")
        if snapshot["state"] in {"passed", "failed", "blocked"} and any(
            item.phase == "prepared" for item in new.values()
        ):
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "unconfirmed operation cannot release caller slot"
            )
        if any(new[key].phase != "prepared" for key in new.keys() - old.keys()):
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "operation receipt requires a committed intent"
            )
        if any(new[key].binding is None for key in new.keys() - old.keys()):
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "new operation requires concrete request binding"
            )
        if snapshot["state"] in {"passed", "failed", "blocked"} and any(
            item.binding is None for item in new.values()
        ):
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "legacy unbound operations require reconciliation"
            )
        if not old.keys() <= new.keys():
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "caller operation journal cannot shrink"
            )
        for key, original in old.items():
            current = new[key]
            if (
                not current.same_request(original)
                or (original.phase == "observed" and current != original)
                or (original.binding is None and current != original)
            ):
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "original operation receipt changed"
                )

    def register(
        self, ctx: TrustedContext, run_id: UUID, spec: RegisterClientRun
    ) -> ClientRunRegistration:
        self._snapshot_matches(run_id, spec.scenario_id, spec.snapshot)
        if spec.snapshot.get("state") != "queued":
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "new caller runs must be queued")
        if spec.snapshot.get("operations"):
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "new caller runs cannot have operations"
            )
        with self.uow.transaction() as tx:
            ref, index_ref = self.ref(ctx, run_id), self.ref(ctx, None)
            self.identity.authorize(tx, ctx, Permission.DIAGNOSE, ref)
            if tx.get(ref) is not None:
                record = self._read(tx, ctx, run_id)
                if (
                    record.scenario_id != spec.scenario_id
                    or record.scope_policy != spec.scope_policy
                    or record.state_policy != spec.state_policy
                ):
                    raise FoundationError(ErrorCode.IDEMPOTENCY_CONFLICT, "caller scenario changed")
                return ClientRunRegistration(created=False, record=record)
            index = tx.get(index_ref)
            if index and index["active"] is not None:
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "caller has active or unknown work"
                )
            count = int(index["count"]) if index else 0  # type: ignore[arg-type]
            if count >= 10:
                raise FoundationError(
                    ErrorCode.CAPACITY_EXCEEDED, "caller run retention limit reached"
                )
            prior_revision = int(index["revision"]) if index else None  # type: ignore[arg-type]
            record = ClientRunRecord(
                run_id=run_id,
                scenario_id=spec.scenario_id,
                owner_id=spec.owner_id,
                scope_id=("p4r_" if spec.scope_policy else "demo_")
                + fingerprint([ref.model_dump(mode="json"), str(run_id)])[:32],
                auth_epoch=ctx.principal.auth_epoch,
                revision=1,
                updated_at=self.clock(),
                snapshot=spec.snapshot,
                definition=spec.definition,
                scope_policy=spec.scope_policy,
                state_policy=spec.state_policy,
                ownership=ClientOwnership(
                    epochs=(ClientOwnerEpoch(owner_id=spec.owner_id, first_revision=1),)
                )
                if spec.state_policy is not None
                else None,
            )
            register_namespace(tx, ref, record)
            tx.put_if_revision(ref, record.model_dump(mode="json"), None)
            tx.put_if_revision(
                index_ref,
                {
                    "revision": (prior_revision or 0) + 1,
                    "count": count + 1,
                    "active": str(run_id),
                },
                prior_revision,
            )
            return ClientRunRegistration(created=True, record=record)

    def checkpoint(
        self, ctx: TrustedContext, run_id: UUID, spec: CheckpointClientRun
    ) -> ClientRunRecord:
        with self.uow.transaction() as tx:
            prior = self._read(tx, ctx, run_id)
            self._snapshot_matches(run_id, prior.scenario_id, spec.snapshot)
            if prior.owner_id != spec.owner_id:
                raise FoundationError(ErrorCode.VERSION_CONFLICT, "caller run owner changed")
            # A response lost after commit can be reconciled without a new revision.
            # Unchanged legacy records acknowledge history without upgrading evidence.
            if prior.snapshot == spec.snapshot:
                return prior
            if prior.recovery_held:
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT,
                    "caller requires explicit recovery before checkpointing",
                )
            self._progress(prior, spec.snapshot)
            if prior.definition is not None and (
                spec.snapshot["state"] == "running" or spec.snapshot.get("operations")
            ):
                from .client_definitions import ClientDefinitions

                ClientDefinitions.require_ready(tx, ctx, prior)
            if prior.revision != spec.expected_revision or prior.snapshot["state"] in TERMINAL:
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "caller checkpoint changed or closed"
                )
            index_ref = self.ref(ctx, None)
            index = tx.get(index_ref)
            if not index or index["active"] != str(run_id):
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "caller no longer owns active slot"
                )
            from .client_admission import ClientAdmissions

            ClientAdmissions.bind_new(tx, ctx, prior, spec.snapshot)
            record = prior.model_copy(
                update={
                    "snapshot": spec.snapshot,
                    "revision": prior.revision + 1,
                    "updated_at": self.clock(),
                }
            )
            tx.put_if_revision(
                self.ref(ctx, run_id), record.model_dump(mode="json"), prior.revision
            )
            if spec.snapshot["state"] in {"passed", "failed", "blocked"}:
                revision = int(index["revision"])  # type: ignore[arg-type]
                tx.put_if_revision(
                    index_ref, {**index, "revision": revision + 1, "active": None}, revision
                )
            return record
