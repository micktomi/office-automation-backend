from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException
from app.schemas.messaging import MessageRequest, MessageResponse
from app.services.messaging_service import messaging_service

router = APIRouter(prefix="/messaging", tags=["messaging"])
logger = logging.getLogger(__name__)

@router.post("/send", response_model=MessageResponse)
async def send_mobile_message(body: MessageRequest):
    """Sends a message to a mobile number (WhatsApp or SMS) using the MessagingService."""
    logger.info("Router: Sending %s to %s", body.provider, body.phone)

    if body.provider == "whatsapp":
        result = await messaging_service.send_whatsapp(to=body.phone, message=body.message)
        if result["status"] == "sent":
            return MessageResponse(status="sent", message_id=result.get("id"))
        else:
            return MessageResponse(status="failed", error=result.get("error"))

    elif body.provider == "sms":
        result = await messaging_service.send_sms(to=body.phone, message=body.message)
        if result["status"] == "sent":
            return MessageResponse(status="sent", message_id=result.get("id"))
        return MessageResponse(status="failed", error=result.get("error"))

    else:
        raise HTTPException(status_code=400, detail="Invalid provider")

@router.get("/ping")
def messaging_ping():
    return {"messaging": "ok", "service": "connected"}
