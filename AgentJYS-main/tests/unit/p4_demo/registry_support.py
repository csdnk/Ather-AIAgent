"""Test-only checkpoint boundary. Real registry atomicity is tested through P3 HTTP."""

from hashlib import sha256
from uuid import UUID, uuid4

from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord, ClientRunRegistration
from aether_agent_memory.runtime.contracts.client_states import ClientExecutionState
from aether_agent_memory.runtime.foundation.common import now
from aether_p4_simulator.validation.errors import ValidationError


class UnitRegistry:
    def __init__(self):
        self.records = {}
        self.inputs = {}
        self.definitions = {}
        self.states = {}

    def save_state(self, record, payload):
        prior = self.records[str(record.run_id)]
        assert prior == record and prior.state_policy == "p4_state_v1"
        state = ClientExecutionState.from_bytes(payload)
        assert state.run_id == prior.run_id
        assert [value.model_dump(mode="json") for value in state.operations] == prior.snapshot.get(
            "operations", []
        )
        assert state.sequence == (
            1 if prior.execution_state is None else prior.execution_state.sequence + 1
        )
        assert state.parent_hash == (
            None if prior.execution_state is None else prior.execution_state.content_hash
        )
        key = (str(record.run_id), state.sequence)
        assert key not in self.states or self.states[key] == payload
        self.states[key] = payload
        updated = prior.model_copy(
            update={
                "revision": prior.revision + 1,
                "execution_state": state.binding(payload),
                "updated_at": now(),
            },
            deep=True,
        )
        self.records[str(record.run_id)] = updated
        return updated.model_copy(deep=True)

    def read_state(self, record):
        return self.states[str(record.run_id), record.execution_state.sequence]

    def save_definition(self, record, payload):
        original = self.records[str(record.run_id)]
        assert original.definition is not None and original.definition == record.definition
        assert original.owner_id == record.owner_id and original.revision == record.revision
        assert sha256(payload).hexdigest() == original.definition.content_hash
        assert len(payload) == original.definition.size_bytes
        key = str(record.run_id)
        assert key not in self.definitions or self.definitions[key] == payload
        self.definitions[key] = payload

    def read_definition(self, record):
        return self.definitions[str(record.run_id)]

    def save_input(self, record, intent, payload):
        original = self.records[str(record.run_id)]
        assert original.owner_id == record.owner_id and original.revision == record.revision
        assert original.snapshot["state"] == "running"
        assert intent.model_dump(mode="json") in original.snapshot["operations"]
        assert sha256(payload).hexdigest() == intent.request_hash
        key = (str(record.run_id), intent.operation_id)
        assert key not in self.inputs or self.inputs[key] == payload
        self.inputs[key] = payload

    def register(self, run, owner_id, definition=None):
        if run.run_id in self.records:
            prior = self.records[run.run_id]
            if prior.scenario_id != run.scenario_id:
                raise ValidationError(409, "start_conflict", "开始标识已用于另一个场景")
            return ClientRunRegistration(created=False, record=prior.model_copy(deep=True))
        if any(
            row.snapshot["state"] in {"queued", "running", "unconfirmed"}
            for row in self.records.values()
        ):
            raise ValidationError(409, "run_active", "prior run is active")
        if len(self.records) >= 10:
            raise ValidationError(429, "run_limit", "retained run capacity reached")
        record = ClientRunRecord(
            run_id=UUID(run.run_id),
            scenario_id=run.scenario_id,
            owner_id=owner_id,
            scope_id="p4r_" + uuid4().hex,
            scope_policy="p4_task_v1",
            state_policy="p4_state_v1" if definition is not None else None,
            auth_epoch=1,
            revision=1,
            updated_at=now(),
            snapshot=run.model_dump(mode="json"),
            definition=definition,
        )
        self.records[run.run_id] = record
        return ClientRunRegistration(created=True, record=record.model_copy(deep=True))

    def get(self, run_id):
        if run_id not in self.records:
            raise ValidationError(404, "run_not_found", "no registered run")
        return self.records[run_id].model_copy(deep=True)

    def checkpoint(self, record, run):
        prior = self.records[run.run_id]
        assert prior.owner_id == record.owner_id and prior.revision == record.revision
        updated = prior.model_copy(
            update={
                "revision": prior.revision + 1,
                "updated_at": now(),
                "snapshot": run.model_dump(mode="json"),
            },
            deep=True,
        )
        self.records[run.run_id] = updated
        return updated.model_copy(deep=True)
