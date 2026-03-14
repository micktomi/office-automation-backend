from __future__ import annotations

from pydantic import BaseModel
from typing import Any

class MessageRequest(BaseModel):
    phone: str
    message: str
    provider: str = "whatsapp"  # default provider

class MessageResponse(BaseModel):
    status: str
    message_id: str | None = None
    error: str | None = None
