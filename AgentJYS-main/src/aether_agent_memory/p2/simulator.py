"""Stateful, explicitly simulated vector-provider contract for local integration.

Not registered by production bootstrap. Visibility is advanced explicitly so an
accepted write cannot accidentally pass an index-readiness test.
"""

from __future__ import annotations

from uuid import uuid4

from aether_agent_memory.p2.contracts import (
    P2CallContext,
    P2DeleteInput,
    P2Evidence,
    P2MutationResult,
    P2OperationQueryInput,
    P2ProjectionPayload,
    P2ProjectionTarget,
    P2ResponseContext,
    P2TargetQueryInput,
    P2TargetState,
    P2UpsertInput,
)
from aether_agent_memory.runtime.capability_store import AtomicRecordStore, RecordTransaction
from aether_agent_memory.runtime.contract_types import (
    Boolean,
    ContractModel,
    Hash,
    Identifier,
    ProjectionOperationKind,
    hash_json,
    utcnow,
)


class _Target(ContractModel):
    target: P2ProjectionTarget
    payload: P2ProjectionPayload | None
    fingerprint: Hash | None
    queryable: Boolean
    retired: Boolean


class _Operation(ContractModel):
    kind: ProjectionOperationKind
    target: P2ProjectionTarget
    fingerprint: Hash
    provider_key: Identifier
    operation_ref: Identifier
    payload: P2ProjectionPayload | None


class StatefulVectorSimulator:
    def __init__(self, store: AtomicRecordStore) -> None:
        self.store = store
        self.lose_next_upsert_response = False

    @staticmethod
    def _key(target: P2ProjectionTarget) -> str:
        return hash_json(target.model_dump(mode="json", exclude={"physical_target_ref"})).value

    @staticmethod
    def _check(context: P2CallContext, target: P2ProjectionTarget) -> None:
        if context.tenant_id != target.tenant_id or context.provider_ref != target.provider_ref:
            raise ValueError("simulator target/context mismatch")
        if utcnow() >= context.deadline_at:
            raise TimeoutError("simulator request deadline exceeded")

    @staticmethod
    def _response(context: P2CallContext) -> P2ResponseContext:
        return P2ResponseContext(
            request_ref=context.request_ref,
            provider_ref=context.provider_ref,
            contract_ref=context.contract_ref,
            provider_request_ref=uuid4().hex,
            observed_at=utcnow(),
            error=None,
        )

    def _observe(
        self,
        tx: RecordTransaction,
        context: P2CallContext,
        target: P2ProjectionTarget,
        include_payload: bool = True,
    ) -> P2TargetState:
        value = tx.get("sim-vector-target", target.tenant_id, self._key(target))
        record = _Target.model_validate_json(value) if value is not None else None
        if (
            record is not None
            and target.physical_target_ref is not None
            and target.physical_target_ref != record.target.physical_target_ref
        ):
            raise ValueError("simulator physical target mismatch")
        present = record is not None and record.payload is not None and not record.retired
        evidence = [
            P2Evidence(
                kind="simulated_target_observation",
                contract_ref=context.contract_ref,
                provider_evidence_ref="sim-target-" + self._key(target),
                observed_at=utcnow(),
            )
        ]
        return P2TargetState(
            target=record.target if record else target,
            object_present=present,
            stored_request_fingerprint=record.fingerprint if present and record else None,
            stored_payload=record.payload if present and include_payload and record else None,
            durable=present,
            index_queryable=bool(present and record and record.queryable),
            delete_confirmed=True if record and record.retired else None,
            late_write_barrier_confirmed=True if record and record.retired else None,
            binding_evidence=evidence if present else [],
            completion_evidence=evidence,
            observed_at=utcnow(),
        )

    def _result(
        self, tx: RecordTransaction, context: P2CallContext, op: _Operation
    ) -> P2MutationResult:
        state = self._observe(tx, context, op.target)
        completed = state.delete_confirmed if op.kind == "delete" else state.index_queryable
        return P2MutationResult(
            operation_kind=op.kind,
            target=state.target,
            provider_idempotency_key=op.provider_key,
            request_fingerprint=op.fingerprint,
            provider_operation_ref=op.operation_ref,
            operation_status="completed" if completed else "accepted",
            raw_status="simulated-completed" if completed else "simulated-accepted",
            target_state=state,
            resubmit_assessment=None,
            evidence=state.completion_evidence,
        )

    def _mutate(
        self,
        context: P2CallContext,
        request: P2UpsertInput | P2DeleteInput,
        kind: ProjectionOperationKind,
    ) -> P2MutationResult:
        self._check(context, request.target)
        if request.precondition_ref is not None:
            raise ValueError("simulator has no registered interpretation for this precondition")
        target, tenant = request.target, request.target.tenant_id
        key = self._key(target)
        opkey = hash_json([target.provider_ref, request.provider_idempotency_key]).value
        with self.store.transaction() as tx:
            old_json = tx.get("sim-vector-operation", tenant, opkey)
            if old_json is not None:
                old = _Operation.model_validate_json(old_json)
                payload = request.payload if isinstance(request, P2UpsertInput) else None
                if (
                    old.kind != kind
                    or old.fingerprint != request.request_fingerprint
                    or self._key(old.target) != key
                    or old.payload != payload
                ):
                    raise ValueError("PROJECTION_IDEMPOTENCY_CONFLICT")
                return self._result(tx, context, old)
            prior_json = tx.get("sim-vector-target", tenant, key)
            prior = _Target.model_validate_json(prior_json) if prior_json is not None else None
            if kind == "upsert" and prior is not None and prior.retired:
                raise ValueError("PROJECTION_TARGET_RETIRED")
            if target.physical_target_ref not in (None, "sim-vector-" + key):
                raise ValueError("simulator physical target mismatch")
            target = target.replaced(physical_target_ref="sim-vector-" + key)
            payload = request.payload if isinstance(request, P2UpsertInput) else None
            if (
                kind == "upsert"
                and prior is not None
                and (prior.fingerprint != request.request_fingerprint or prior.payload != payload)
            ):
                raise ValueError("PROJECTION_IDEMPOTENCY_CONFLICT")
            record = _Target(
                target=target,
                payload=payload,
                fingerprint=request.request_fingerprint if payload is not None else None,
                queryable=bool(prior and prior.queryable and kind == "upsert"),
                retired=kind == "delete",
            )
            op = _Operation(
                kind=kind,
                target=target,
                fingerprint=request.request_fingerprint,
                provider_key=request.provider_idempotency_key,
                operation_ref=uuid4().hex,
                payload=payload,
            )
            tx.put("sim-vector-target", tenant, key, record.model_dump_json())
            tx.put("sim-vector-operation", tenant, opkey, op.model_dump_json())
            return self._result(tx, context, op)

    async def upsert(
        self, context: P2CallContext, request: P2UpsertInput
    ) -> tuple[P2ResponseContext, P2MutationResult]:
        result = self._mutate(context, request, "upsert")
        if self.lose_next_upsert_response:
            self.lose_next_upsert_response = False
            raise TimeoutError("simulated response loss after durable write")
        return self._response(context), result

    async def delete_projection(
        self, context: P2CallContext, request: P2DeleteInput
    ) -> tuple[P2ResponseContext, P2MutationResult]:
        return self._response(context), self._mutate(context, request, "delete")

    async def query_operation(
        self, context: P2CallContext, request: P2OperationQueryInput
    ) -> tuple[P2ResponseContext, P2MutationResult]:
        self._check(context, request.target)
        key = hash_json([request.target.provider_ref, request.provider_idempotency_key]).value
        with self.store.transaction() as tx:
            value = tx.get("sim-vector-operation", request.target.tenant_id, key)
            if value is None:
                result = P2MutationResult(
                    operation_kind=request.operation_kind,
                    target=request.target,
                    provider_idempotency_key=request.provider_idempotency_key,
                    request_fingerprint=None,
                    provider_operation_ref=None,
                    operation_status="not_found",
                    raw_status="simulated-not-found",
                    target_state=None,
                    resubmit_assessment=None,
                    evidence=[],
                )
            else:
                op = _Operation.model_validate_json(value)
                if (
                    op.kind != request.operation_kind
                    or self._key(op.target) != self._key(request.target)
                    or op.fingerprint != request.request_fingerprint
                    or request.provider_operation_ref not in (None, op.operation_ref)
                ):
                    raise ValueError("simulator operation binding mismatch")
                result = self._result(tx, context, op)
        return self._response(context), result

    async def get_projection(
        self, context: P2CallContext, request: P2TargetQueryInput
    ) -> tuple[P2ResponseContext, P2TargetState]:
        self._check(context, request.target)
        with self.store.transaction() as tx:
            result = self._observe(tx, context, request.target, request.include_payload)
        return self._response(context), result

    def make_index_queryable(self, target: P2ProjectionTarget) -> None:
        """Test control: advance the simulated asynchronous indexing operation."""
        with self.store.transaction() as tx:
            key = self._key(target)
            value = tx.get("sim-vector-target", target.tenant_id, key)
            if value is None:
                raise ValueError("unknown simulated target")
            record = _Target.model_validate_json(value)
            if record.retired:
                raise ValueError("cannot index a retired target")
            tx.put(
                "sim-vector-target",
                target.tenant_id,
                key,
                record.replaced(queryable=True).model_dump_json(),
            )
