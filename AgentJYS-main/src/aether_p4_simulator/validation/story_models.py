"""Typed story responses. Free-form metadata stays server-side and is never forwarded."""

from typing import Annotated, Any

from pydantic import Field, RootModel, StrictBool, StrictInt

from aether_agent_memory.remember.contracts.models import MemoryRef, SourceRef

from .models import Identifier, ResponseModel


class ObjectData(RootModel[dict[str, Any]]):
    """Private adapter for P3 routes without exported response DTOs."""


class ArrayData(RootModel[list[dict[str, Any]]]):
    """Private adapter for incident lists."""


class MemoryRefs(RootModel[tuple[MemoryRef, ...]]):
    pass


class TaskData(ResponseModel):
    task_id: Identifier


class DistillResultData(ResponseModel):
    memories: tuple[MemoryRef, ...]
    candidate_count: Annotated[StrictInt, Field(ge=0)]
    zero_output: StrictBool
    awaiting_extraction_provider: tuple[Identifier, ...]


class BodyRangeData(ResponseModel):
    memory: MemoryRef
    outcome: str
    content: str | None
    start_char: int | None = None
    end_char: int | None = None
    total_chars: int | None = None
    is_complete: bool | None = None
    range_hash: str | None = None
    sources: tuple[SourceRef, ...] = ()


class SourceRangeData(ResponseModel):
    source: SourceRef
    content: str
    start_char: int
    end_char: int
    total_chars: int
    is_complete: bool
    range_hash: str | None = None
