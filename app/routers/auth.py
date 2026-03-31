from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Response
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])


class DevLoginRequest(BaseModel):
    email: str = "admin@test-agent.app"
    password: str = "password123"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str


@router.post("/dev-login", response_model=TokenResponse)
def dev_login(_body: DevLoginRequest):
    expiry = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return TokenResponse(access_token="local-deterministic-token", expires_at=expiry)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key="session", path="/", samesite="none", secure=True)
    return {"status": "ok", "message": "Logged out successfully"}
