"""Same-origin authenticated chat routes for the local acceptance UI."""

from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from aether_platform.chat import Conversations
from aether_platform.chat_documents import Attachment
from aether_platform.directory import AccessDeniedError
from aether_platform.operations.quotas import QuotaExceededError


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=12000)
    turn_id: str = Field(min_length=1, max_length=64)
    attachments: list[Attachment] = Field(default_factory=list, max_length=3)


class Title(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)


def install_chat(
    app: FastAPI,
    chat: Conversations,
    current: Any,
    csrf: Any,
    settings: dict[str, Any],
    p3_settings: dict[str, Any] | None = None,
) -> None:
    chat.migrate()

    @app.get("/chat-api/conversations")
    def conversations(request: Request) -> dict[str, Any]:
        return {"conversations": chat.list(current(request)[2])}

    @app.post("/chat-api/conversations", status_code=201)
    def create(request: Request) -> dict[str, Any]:
        _, session, actor = current(request)
        csrf(request, session)
        return chat.create(actor)

    @app.get("/chat-api/conversations/{conversation_id}")
    def messages(request: Request, conversation_id: str, tasks: BackgroundTasks) -> dict[str, Any]:
        _, session, actor = current(request)
        result = chat.messages(actor, conversation_id)
        if (
            p3_settings
            and p3_settings.get("base_url")
            and any(
                row.get("memory_status") in {"save_queued", "saving"} for row in result["messages"]
            )
        ):
            tasks.add_task(
                chat.save_pending, actor, conversation_id, p3_settings, session["token_provider"]
            )
        return result

    @app.post("/chat-api/conversations/{conversation_id}/messages", status_code=202)
    def send(
        request: Request, conversation_id: str, body: Message, tasks: BackgroundTasks
    ) -> dict[str, Any]:
        _, session, actor = current(request)
        csrf(request, session)
        try:
            turn = chat.begin(
                actor,
                conversation_id,
                body.turn_id,
                body.content,
                attachments=[item.model_dump() for item in body.attachments],
            )
        except AccessDeniedError:
            raise
        except QuotaExceededError as exc:
            raise HTTPException(429, str(exc)) from None
        except ValueError:
            raise HTTPException(409, "消息正在回复，或消息编号已被使用。") from None
        if turn["started"]:
            tasks.add_task(
                chat.generate,
                actor,
                conversation_id,
                body.turn_id,
                settings,
                turn["attempt"],
                p3_settings,
                session["token_provider"],
            )
        return turn

    @app.patch("/chat-api/conversations/{conversation_id}")
    def rename(request: Request, conversation_id: str, body: Title) -> dict[str, bool]:
        _, session, actor = current(request)
        csrf(request, session)
        try:
            chat.rename(actor, conversation_id, body.title)
        except AccessDeniedError:
            raise
        except ValueError:
            raise HTTPException(422, "请输入有效的对话名称。") from None
        return {"updated": True}

    @app.delete("/chat-api/conversations/{conversation_id}")
    def archive(request: Request, conversation_id: str) -> dict[str, bool]:
        _, session, actor = current(request)
        csrf(request, session)
        chat.archive(actor, conversation_id)
        return {"archived": True}
