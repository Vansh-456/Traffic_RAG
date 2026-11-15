"""POST /api/chat → Server-Sent Events stream of the answer."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.api.deps import SESSION_COOKIE, is_admin_token, rate_limit, session_id
from app.config import get_settings
from app.db.models.users import Conversation, Message
from app.db.session import session_scope
from app.observability.logging import get_logger
from app.rag.orchestrator import RunConfig, RunResult, run

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])

HISTORY_TURNS = 6


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None


async def _load_history(conversation_id: uuid.UUID | None, sid: str) -> tuple[uuid.UUID, list[dict[str, str]]]:
    async with session_scope() as s:
        conv = None
        if conversation_id:
            conv = await s.get(Conversation, conversation_id)
            if conv is not None and conv.session_id != sid:
                conv = None
        if conv is None:
            conv = Conversation(id=uuid.uuid4(), session_id=sid)
            s.add(conv)
            await s.flush()
            return conv.id, []
        rows = (
            await s.execute(
                select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at.desc()).limit(HISTORY_TURNS * 2)
            )
        ).scalars().all()
        return conv.id, [{"role": m.role, "content": m.content} for m in reversed(rows)]


@router.post("/chat", dependencies=[Depends(rate_limit)])
async def chat(
    body: ChatRequest,
    request: Request,
    sid: str = Depends(session_id),
    x_admin_token: str | None = Header(default=None),
) -> EventSourceResponse:
    settings = get_settings()
    text = body.message.strip()
    if len(text) > settings.max_query_chars:
        raise HTTPException(status_code=413, detail=f"message longer than {settings.max_query_chars} characters")
    conversation_id, history = await _load_history(body.conversation_id, sid)
    user_msg_id = uuid.uuid4()
    async with session_scope() as s:
        s.add(Message(id=user_msg_id, conversation_id=conversation_id, role="user", content=text))
    assistant_id = uuid.uuid4()
    result = RunResult()
    cfg = RunConfig(is_admin=is_admin_token(x_admin_token))

    async def events() -> AsyncIterator[dict[str, str]]:
        yield {"event": "meta", "data": json.dumps({"conversation_id": str(conversation_id), "message_id": str(assistant_id)})}
        try:
            async for ev in run(text, history, cfg, result=result):
                if await request.is_disconnected():
                    log.info("client disconnected")
                    break
                yield {"event": ev["event"], "data": json.dumps(ev["data"], default=str, ensure_ascii=False)}
        except Exception as exc:
            log.exception("chat failed")
            yield {"event": "error", "data": json.dumps({"message": "Something went wrong while answering. Please try again.", "type": type(exc).__name__})}
        finally:
            if result.answer:
                try:
                    async with session_scope() as s:
                        s.add(Message(
                            id=assistant_id, conversation_id=conversation_id, role="assistant", content=result.answer,
                            language=result.language, intent=result.intent, faithfulness=result.faithfulness,
                            abstained=result.abstained, cached=result.cached, latency_ms=result.latency_ms,
                            first_token_ms=result.first_token_ms, citations=result.citations, internals=result.internals,
                        ))
                except Exception:
                    log.exception("failed to persist assistant message")

    resp = EventSourceResponse(events(), ping=15)
    # a returned Response bypasses headers set on the injected one, so re-set the cookie
    resp.set_cookie(SESSION_COOKIE, sid, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax",
                    secure=settings.app_env == "production")
    return resp


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: uuid.UUID, sid: str = Depends(session_id)) -> dict[str, object]:
    async with session_scope() as s:
        conv = await s.get(Conversation, conversation_id)
        if conv is None or conv.session_id != sid:
            raise HTTPException(404, "conversation not found")
        rows = (await s.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at))).scalars().all()
        return {"id": str(conv.id), "messages": [
            {"id": str(m.id), "role": m.role, "content": m.content, "citations": m.citations, "faithfulness": m.faithfulness, "created_at": m.created_at.isoformat()}
            for m in rows
        ]}
