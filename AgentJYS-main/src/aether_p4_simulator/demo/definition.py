"""Versioned fixed input data; never execute code loaded from stored documents."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

import rfc8785
from pydantic import Field, StrictInt, StrictStr, model_validator

from aether_agent_memory.runtime.contracts.client_definitions import ClientDefinitionBinding
from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    Digest,
    NonEmpty,
    Positive,
    Timestamp,
)
from aether_agent_memory.runtime.foundation.common import now
from aether_p4_simulator.validation.errors import ValidationError

from . import scenarios
from .models import ScenarioID


def implementation_hash() -> str:
    """Bind execution and wire-model behavior; normalize checkout line endings."""
    source = Path(__file__).resolve().parents[2]
    names = (
        "aether_p4_simulator",
        "aether_agent_memory/remember/contracts",
        "aether_agent_memory/recall/contracts",
        "aether_agent_memory/runtime/contracts",
    )
    manifest = {
        path.relative_to(source).as_posix(): sha256(
            path.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for name in names
        for path in sorted((source / name).rglob("*.py"))
    }
    if not manifest:
        raise ValidationError(503, "definition_executor_missing", "执行实现清单无法读取")
    return sha256(
        rfc8785.dumps(
            {
                "sources": manifest,
                "packages": {name: version(name) for name in ("pydantic", "httpx", "rfc8785")},
            }
        )
    ).hexdigest()


class FrozenTurn(ContractModel):
    action: Literal["remember", "recall"]
    user_text: NonEmpty
    expected_terms: tuple[str, ...]
    target: Literal["rules", "loan", "noise"]


class FrozenInput(ContractModel):
    name: str
    value: StrictStr | StrictInt


class DefinitionInputsV1(ContractModel):
    """Required values are checked together before any step can have effects."""

    library_quote: str
    library_noise: str
    library_loan_query: str
    weather_backup_query: str
    weather_park_query: str
    preference_correction: str
    correction_reason: str
    lifecycle_reason: str
    retention_reason: str
    retention_hours: Positive = Field(ge=24, le=87600)
    reflection_reason: str
    reflection_min_episodes: Positive = Field(ge=2, le=8)
    reflection_period_hours: Positive = Field(le=720)
    booking_delete_reason: str
    booking_query: str
    second_source: str
    third_source: str
    source_delete_reason: str
    deleted_source_range_end: Positive
    forgotten_query: str
    source_version: str
    basic_token_budget: Positive
    story_token_budget: Positive


class RunDefinition(ContractModel):
    version: Literal[1]
    run_id: UUID
    scenario_id: ScenarioID
    executor_hash: Digest
    texts: tuple[NonEmpty, ...] = Field(min_length=1, max_length=32)
    event_times: tuple[Timestamp, ...]
    library: tuple[FrozenTurn, ...]
    rules: NonEmpty
    inputs: tuple[FrozenInput, ...]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len(self.texts) != len(self.event_times) or len({v.name for v in self.inputs}) != len(
            self.inputs
        ):
            raise ValueError("definition steps, event times or input names differ")
        if (
            self.scenario_id == "library-basic"
            and tuple(turn.user_text for turn in self.library) != self.texts
        ):
            raise ValueError("basic dialogue differs from frozen steps")
        if self.scenario_id != "library-basic" and self.library:
            raise ValueError("fixed story cannot include unrelated library turns")
        DefinitionInputsV1.model_validate({item.name: item.value for item in self.inputs})
        return self

    @classmethod
    def capture(cls, run_id: UUID, scenario_id: ScenarioID) -> Self:
        library = (
            tuple(FrozenTurn.model_validate(asdict(turn)) for turn in scenarios.LIBRARY)
            if scenario_id == "library-basic"
            else ()
        )
        texts = (
            tuple(turn.user_text for turn in library)
            if library
            else tuple(scenarios.STORY_TEXTS[scenario_id])
        )
        anchor = datetime.fromisoformat(now().replace("Z", "+00:00"))
        return cls(
            version=1,
            run_id=run_id,
            scenario_id=scenario_id,
            executor_hash=implementation_hash(),
            texts=texts,
            library=library,
            rules=scenarios.RULES,
            event_times=tuple(
                (anchor + timedelta(milliseconds=index))
                .astimezone(UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
                for index in range(len(texts))
            ),
            inputs=tuple(
                FrozenInput(name=key, value=value)
                for key, value in sorted(scenarios.STORY_INPUTS.items())
            ),
        )

    def encode(self) -> bytes:
        return rfc8785.dumps(self.model_dump(mode="json"))

    def binding(self) -> ClientDefinitionBinding:
        payload = self.encode()
        return ClientDefinitionBinding(
            format_id="p4_fixed_story_v1",
            content_hash=sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )

    def require_execution(self, record: ClientRunRecord) -> None:
        if (
            self.run_id != record.run_id
            or self.scenario_id != record.scenario_id
            or self.binding() != record.definition
        ):
            raise ValidationError(409, "definition_binding_changed", "原运行定义与登记绑定不同")
        if self.executor_hash != implementation_hash():
            raise ValidationError(
                409, "definition_executor_changed", "原运行需要对应执行版本或明确迁移，停止执行"
            )

    def text(self, name: str) -> str:
        value = next((item.value for item in self.inputs if item.name == name), None)
        if not isinstance(value, str):
            raise ValidationError(409, "definition_input_missing", "原运行缺少文本输入：" + name)
        return value

    def number(self, name: str) -> int:
        value = next((item.value for item in self.inputs if item.name == name), None)
        if type(value) is not int:
            raise ValidationError(409, "definition_input_missing", "原运行缺少数值输入：" + name)
        return value
