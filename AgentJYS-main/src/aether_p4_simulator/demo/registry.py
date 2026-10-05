"""P4 consumes P3's authenticated checkpoint API; no local persistence fallback."""

import re
from typing import Protocol
from uuid import UUID

from aether_agent_memory.runtime.contracts.client_definitions import ClientDefinitionBinding
from aether_agent_memory.runtime.contracts.client_runs import (
    CheckpointClientRun,
    ClientOperation,
    ClientRunRecord,
    ClientRunRegistration,
    RegisterClientRun,
)
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError

from .models import RunSnapshot


class RunRegistry(Protocol):
    def register(
        self, run: RunSnapshot, owner_id: str, definition: ClientDefinitionBinding | None = None
    ) -> ClientRunRegistration: ...
    def save_definition(self, record: ClientRunRecord, payload: bytes) -> None: ...
    def read_definition(self, record: ClientRunRecord) -> bytes: ...
    def save_state(self, record: ClientRunRecord, payload: bytes) -> ClientRunRecord: ...
    def read_state(self, record: ClientRunRecord) -> bytes: ...
    def get(self, run_id: str) -> ClientRunRecord: ...
    def checkpoint(self, record: ClientRunRecord, run: RunSnapshot) -> ClientRunRecord: ...
    def save_input(
        self, record: ClientRunRecord, intent: ClientOperation, payload: bytes
    ) -> None: ...


class P3RunRegistry:
    def __init__(self, client: P3ValidationClient):
        self.client = client

    def save_input(self, record: ClientRunRecord, intent: ClientOperation, payload: bytes) -> None:
        self.client.save_client_input(record, intent, payload)

    def save_definition(self, record: ClientRunRecord, payload: bytes) -> None:
        self.client.save_client_definition(record, payload)

    def read_definition(self, record: ClientRunRecord) -> bytes:
        return self.client.read_client_definition(record)

    def save_state(self, record: ClientRunRecord, payload: bytes) -> ClientRunRecord:
        self._managed(record)
        return self.client.save_client_state(record, payload)

    def read_state(self, record: ClientRunRecord) -> bytes:
        self._managed(record)
        return self.client.read_client_state(record)

    def register(
        self, run: RunSnapshot, owner_id: str, definition: ClientDefinitionBinding | None = None
    ) -> ClientRunRegistration:
        if definition is None:
            self._invalid()
        result = self.client.register_client_run(
            UUID(run.run_id),
            RegisterClientRun(
                scenario_id=run.scenario_id,
                owner_id=owner_id,
                snapshot=run.model_dump(mode="json"),
                definition=definition,
                scope_policy="p4_task_v1",
                state_policy="p4_state_v1",
            ),
        )
        self._verify(result.record, run.run_id)
        self._managed(result.record)
        if result.record.scenario_id != run.scenario_id or (
            result.created
            and (
                result.record.owner_id != owner_id
                or result.record.revision != 1
                or result.record.definition != definition
            )
        ):
            self._invalid()
        return result

    @staticmethod
    def _invalid() -> None:
        raise ValidationError(
            502,
            "registry_binding_mismatch",
            "P3 登记回执与原运行不一致，已停止后续业务",
            write_outcome="unconfirmed",
        )

    def _verify(self, record: ClientRunRecord, run_id: str) -> None:
        try:
            run = RunSnapshot.model_validate(record.snapshot)
        except ValueError:
            self._invalid()
            return
        if (
            str(record.run_id) != run_id
            or run.run_id != run_id
            or run.scenario_id != record.scenario_id
        ):
            self._invalid()

    def get(self, run_id: str) -> ClientRunRecord:
        try:
            record = self.client.get_client_run(UUID(run_id))
            self._verify(record, run_id)
            return record
        except ValidationError as error:
            if error.status == 404:
                raise ValidationError(
                    404, "run_not_found", "P3 没有此轮登记，不能确认先前执行结果"
                ) from None
            raise

    def _managed(self, record: ClientRunRecord) -> None:
        if (
            record.state_policy != "p4_state_v1"
            or record.scope_policy != "p4_task_v1"
            or not re.fullmatch(r"p4r_[0-9a-f]{32}", record.scope_id)
        ):
            self._invalid()

    def checkpoint(self, record: ClientRunRecord, run: RunSnapshot) -> ClientRunRecord:
        self._managed(record)
        updated = self.client.checkpoint_client_run(
            record.run_id,
            CheckpointClientRun(
                owner_id=record.owner_id,
                expected_revision=record.revision,
                snapshot=run.model_dump(mode="json"),
            ),
        )
        self._verify(updated, run.run_id)
        if (
            any(
                getattr(updated, key) != getattr(record, key)
                for key in (
                    "run_id",
                    "scenario_id",
                    "scope_id",
                    "owner_id",
                    "auth_epoch",
                    "definition",
                    "scope_policy",
                    "state_policy",
                    "execution_state",
                )
            )
            or updated.revision not in {record.revision, record.revision + 1}
            or updated.snapshot != run.model_dump(mode="json")
        ):
            self._invalid()
        return updated
