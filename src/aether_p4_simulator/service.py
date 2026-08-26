from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from aether_p4_simulator.client import P3Client, P3ClientError, new_request_id
from aether_p4_simulator.models import (
    AgentProfile,
    ChatMessage,
    CreateAgentRequest,
    CreateSessionRequest,
    MessageRole,
    P4Health,
    P4Session,
    SendMessageRequest,
    SubmitDocumentRequest,
    TurnResult,
)


class P4SimulatorService:
    """Small upstream Agent application with an in-memory P4 control plane."""

    def __init__(self, p3: P3Client) -> None:
        self._p3 = p3
        self._lock = RLock()
        self._agents: dict[str, AgentProfile] = {}
        self._sessions: dict[str, P4Session] = {}
        self.create_agent(
            CreateAgentRequest(
                agent_id="company-assistant",
                name="Company Assistant",
                description="Reference P4 Agent connected to AetherBrain P3.",
            )
        )

    def list_agents(self) -> list[AgentProfile]:
        with self._lock:
            return [agent.model_copy(deep=True) for agent in self._agents.values()]

    def create_agent(self, request: CreateAgentRequest) -> AgentProfile:
        agent = AgentProfile(**request.model_dump())
        with self._lock:
            self._agents[agent.agent_id] = agent
        return agent.model_copy(deep=True)

    def create_session(self, request: CreateSessionRequest) -> P4Session:
        with self._lock:
            if request.agent_id not in self._agents:
                raise KeyError(f"agent not found: {request.agent_id}")
            session_id = request.session_id or f"session-{uuid4().hex[:12]}"
            if session_id in self._sessions:
                raise ValueError(f"session already exists: {session_id}")
            session = P4Session(
                session_id=session_id,
                agent_id=request.agent_id,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
            )
            self._sessions[session_id] = session
            return session.model_copy(deep=True)

    def get_session(self, session_id: str) -> P4Session:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(f"session not found: {session_id}")
            return session.model_copy(deep=True)

    async def health(self) -> P4Health:
        try:
            p3_health = await self._p3.health()
        except P3ClientError as exc:
            return P4Health(
                status="degraded",
                p3_reachable=False,
                p3_base_url=self._p3.base_url,
                detail=str(exc),
            )
        runtime = p3_health.get("runtime_health")
        ready = bool(runtime.get("ready")) if isinstance(runtime, dict) else True
        return P4Health(
            status="healthy" if ready else "degraded",
            p3_reachable=True,
            p3_base_url=self._p3.base_url,
            p3=p3_health,
            detail=None if ready else "P3 is reachable but reports a degraded runtime",
        )

    async def send_message(self, session_id: str, request: SendMessageRequest) -> TurnResult:
        session = self.get_session(session_id)
        request_id = new_request_id("p4-turn")
        trace_id = new_request_id("p4-trace")
        context = await self._p3.build_context(
            tenant_id=session.tenant_id,
            user_id=session.user_id,
            agent_id=session.agent_id,
            session_id=session.session_id,
            query=request.content,
            max_tokens=request.max_context_tokens,
            request_id=request_id,
            trace_id=trace_id,
        )
        memory_refs = self._memory_refs(context)
        answer = self._reference_answer(request.content, context)
        event_type = "user_memory" if request.durable_memory else "after_turn"
        memory_content = (
            request.content
            if request.durable_memory
            else f"User: {request.content}\nAssistant: {answer}"
        )
        memory = await self._p3.write_memory(
            tenant_id=session.tenant_id,
            user_id=session.user_id,
            agent_id=session.agent_id,
            session_id=session.session_id,
            content=memory_content,
            event_type=event_type,
            source="user" if request.durable_memory else "agent",
            metadata={
                "channel": "p4-simulator",
                "durable_memory": request.durable_memory,
                "context_memory_refs": memory_refs,
            },
            request_id=new_request_id("p4-memory"),
            trace_id=trace_id,
        )
        user_message = ChatMessage(
            role=MessageRole.USER,
            content=request.content,
            trace_id=trace_id,
            memory_id=str(memory.get("id")) if request.durable_memory else None,
            context_memory_refs=memory_refs,
        )
        assistant_message = ChatMessage(
            role=MessageRole.ASSISTANT,
            content=answer,
            trace_id=trace_id,
            memory_id=str(memory.get("id")) if not request.durable_memory else None,
            context_memory_refs=memory_refs,
        )
        with self._lock:
            current = self._sessions.get(session_id)
            if current is None:
                raise KeyError(f"session not found: {session_id}")
            current.messages.extend([user_message, assistant_message])
            current.updated_at = datetime.now(UTC)
            snapshot = current.model_copy(deep=True)
        return TurnResult(
            session=snapshot,
            user_message=user_message,
            assistant_message=assistant_message,
            context=context,
            memory=memory,
            p3_calls=["POST /api/v1/context", "POST /api/v1/memory/events"],
        )

    async def submit_document(
        self,
        session_id: str,
        request: SubmitDocumentRequest,
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        request_id = new_request_id("p4-document")
        trace_id = new_request_id("p4-trace")
        return await self._p3.submit_long_text(
            tenant_id=session.tenant_id,
            user_id=session.user_id,
            agent_id=session.agent_id,
            session_id=session.session_id,
            source_id=request.source_id,
            text=request.text,
            request_id=request_id,
            trace_id=trace_id,
        )

    async def get_task(self, task_id: str) -> dict[str, Any]:
        return await self._p3.get_task(task_id)

    @staticmethod
    def _memory_refs(context: dict[str, Any]) -> list[str]:
        refs = context.get("memory_refs")
        if isinstance(refs, list):
            return [str(item) for item in refs]
        memories = context.get("memories")
        if not isinstance(memories, list):
            return []
        return [str(item["id"]) for item in memories if isinstance(item, dict) and item.get("id")]

    @staticmethod
    def _reference_answer(question: str, context: dict[str, Any]) -> str:
        memories = context.get("memories")
        valid_memories = (
            [item for item in memories if isinstance(item, dict)]
            if isinstance(memories, list)
            else []
        )
        if not valid_memories:
            return (
                "P3 did not return relevant memory for this turn. "
                f'The reference Agent received: "{question}".'
            )
        snippets = [str(item.get("content", "")).strip() for item in valid_memories[:3]]
        evidence = " | ".join(text[:120] for text in snippets if text)
        return (
            f"P3 returned {len(valid_memories)} relevant memories. "
            f"Reference context: {evidence or 'memory metadata only'}."
        )
