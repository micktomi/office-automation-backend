from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.policy import Policy
from app.schemas.email import EmailRecord, EmailReplyRequest, EmailReplyResult, EmailSyncResult
from app.services.email_service import email_service

router = APIRouter(prefix="/email", tags=["email"])
logger = logging.getLogger(__name__)


def _try_parse_policy_id(email_id: str) -> int | None:
    cleaned = email_id.replace("policy-", "").strip()
    if cleaned.isdigit():
        return int(cleaned)
    return None


@router.get("/", response_model=list[EmailRecord])
def list_emails(
    include_archived: bool = Query(default=False),
    include_noise: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return email_service.list_emails(
        db,
        include_archived=include_archived,
        include_noise=include_noise,
        limit=limit,
    )


@router.post("/sync", response_model=EmailSyncResult)
def sync_emails(
    days_ahead: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    result = email_service.sync_inbox(
        db,
        days_ahead=days_ahead,
        limit=limit,
    )
    return EmailSyncResult(**result)


@router.post("/reply", response_model=EmailReplyResult)
async def draft_reply(body: EmailReplyRequest, db: Session = Depends(get_db)):
    """Generates a draft reply using AI (smart) or templates (fallback)."""
    policy_id = _try_parse_policy_id(body.email_id)

    if policy_id is not None:
        policy = db.query(Policy).filter(Policy.id == policy_id).first()
        if policy:
            reply = await email_service.generate_smart_reply(
                sender=policy.client_name,
                subject=f"Ανανέωση συμβολαίου #{policy.id}",
                body=f"Το συμβόλαιο λήγει στις {policy.expiry_date}. Χρειάζεται ανανέωση.",
            )
            return EmailReplyResult(email_id=body.email_id, reply=reply)

    cached = email_service.get_email_context(db, body.email_id)
    if cached:
        reply = await email_service.generate_smart_reply(
            sender=str(cached.get("sender") or "unknown"),
            subject=str(cached.get("subject") or "(Χωρίς θέμα)"),
            body=str(cached.get("body") or ""),
        )
        return EmailReplyResult(email_id=body.email_id, reply=reply)

    reply = "Ευχαριστούμε για το μήνυμά σας. Θα επικοινωνήσουμε σύντομα."
    return EmailReplyResult(email_id=body.email_id, reply=reply)


@router.post("/send")
async def send_custom_email(to: str, subject: str, body: str):
    """Mechanically sends an email via EmailService (no AI)."""
    result = await email_service.send_email(to=to, subject=subject, body=body)
    if result["status"] == "sent":
        return {"status": "ok", "provider": result.get("provider")}
    raise HTTPException(status_code=500, detail=result.get("error"))


@router.get("/ping")
def email_ping() -> dict[str, str]:
    return {"email": "ok"}
