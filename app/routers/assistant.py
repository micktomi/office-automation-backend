from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.ai.orchestrator import handle_chat_message

router = APIRouter(prefix="/assistant", tags=["assistant"])


class ChatRequest(BaseModel):
    message: str
    context: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    response: str
    action_performed: str | None = None
    data: Any | None = None


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, db: Session = Depends(get_db)):
    natural_response, action_performed, data = await handle_chat_message(body.message, db, body.context)

    return ChatResponse(
        response=natural_response,
        action_performed=action_performed,
        data=data,
    )
