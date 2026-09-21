from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class AgentProfile(BaseModel):
    agent_id: str
    name: str
    description: str = ""
    system_prompt: str = "You are a reference enterprise assistant."
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CreateAgentRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=500)
    system_prompt: str = Field(
        default="You are a reference enterprise assistant.",
        max_length=4000,
    )


class CreateSessionRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default="demo-tenant", min_length=1, max_length=128)
    user_id: str = Field(default="demo-user", min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)


class ChatMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: f"msg-{uuid4().hex}")
    role: MessageRole
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trace_id: str | None = None
    memory_id: str | None = None
    context_memory_refs: list[str] = Field(default_factory=list)


class P4Session(BaseModel):
    session_id: str
    agent_id: str
    tenant_id: str
    user_id: str
    messages: list[ChatMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    durable_memory: bool = False
    max_context_tokens: int = Field(default=2048, ge=128, le=16384)


class SubmitDocumentRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=2_000_000)


class TurnResult(BaseModel):
    session: P4Session
    user_message: ChatMessage
    assistant_message: ChatMessage
    context: dict[str, Any]
    memory: dict[str, Any]
    p3_calls: list[str]


class P4Health(BaseModel):
    status: str
    live: bool = True
    p3_reachable: bool
    p3_base_url: str
    p3: dict[str, Any] | None = None
    detail: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
