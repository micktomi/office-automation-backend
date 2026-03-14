from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai.tools import call_tool
from app.models.database import get_db

router = APIRouter(prefix="/agent", tags=["agent"])


class ActionRequest(BaseModel):
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ActionResponse(BaseModel):
    response: str
    action_performed: str
    data: Any | None = None


def _as_dict(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_as_dict(v) for v in value]
    return value


async def execute_action(action: str, payload: dict[str, Any], db: Session) -> ActionResponse:
    result = await call_tool(action, payload, db)
    if result["status"] != "success":
        message = result.get("message", "Action failed")
        status_code = 400 if "not supported" in message else 422
        raise HTTPException(status_code=status_code, detail=message)

    return ActionResponse(
        response=f"Action {action} completed",
        action_performed=action,
        data=_as_dict(result.get("data")),
    )


@router.post("/action", response_model=ActionResponse)
async def action_dispatcher(body: ActionRequest, db: Session = Depends(get_db)):
    return await execute_action(body.action, body.payload or {}, db)



@router.get("/ping")
def agent_ping() -> dict[str, Any]:
    return {
        "agent": "ok",
        "mode": "deterministic",
        "time": datetime.now(timezone.utc).isoformat(),
    }
