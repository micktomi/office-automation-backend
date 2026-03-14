from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.config import get_settings
from app.engine.reminder_cycle import run_reminder_cycle
from app.models.database import get_db
from app.models.policy import Policy
from app.schemas.email import EmailRecord, EmailReplyRequest, EmailReplyResult, EmailSyncResult

router = APIRouter(prefix="/email", tags=["email"])
logger = logging.getLogger(__name__)

_EMAIL_CACHE: dict[str, EmailRecord] = {}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _priority_from_days(days_left: int) -> str:
    if days_left <= 7:
        return "high"
    if days_left <= 30:
        return "medium"
    return "low"


def _priority_from_subject(subject: str) -> str:
    text = (subject or "").lower()
    if any(k in text for k in ["urgent", "asap", "επείγον", "άμεσο"]):
        return "high"
    return "medium"


def _policy_to_email(policy: Policy) -> EmailRecord:
    today = datetime.now(timezone.utc).date()
    days_left = (policy.expiry_date - today).days
    status = "archived" if policy.status == "archived" else "inbox"

    body = (
        f"Αγαπητέ/ή {policy.client_name},\n\n"
        f"Το ασφαλιστήριό σας λήγει στις {policy.expiry_date}. "
        "Επικοινωνήστε μαζί μας για ανανέωση."
    )

    return EmailRecord(
        id=str(policy.id),
        policy_id=policy.id,
        subject=f"Υπενθύμιση ανανέωσης συμβολαίου #{policy.id}",
        sender=f'"{policy.client_name}" <{policy.email}>',
        body=body,
        priority=_priority_from_days(max(days_left, 0)),
        status=status,
        unread=policy.reminder_attempts == 0,
        received_at=policy.created_at.isoformat() if policy.created_at else None,
    )


def _extract_header(headers: list[dict[str, str]], name: str) -> str:
    wanted = name.lower()
    for header in headers:
        if (header.get("name") or "").lower() == wanted:
            return header.get("value") or ""
    return ""


def _google_token_exists() -> bool:
    settings = get_settings()
    return Path(settings.google_token_file).exists()


def _load_gmail_service():
    settings = get_settings()
    token_path = Path(settings.google_token_file)
    if not token_path.exists():
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), settings.google_scopes)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")

        if not creds or not creds.valid:
            return None

        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as exc:
        logger.warning("Gmail service init failed: %s", exc)
        return None


def _gmail_to_email_record(message: dict) -> EmailRecord:
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []

    subject = _extract_header(headers, "Subject") or "(Χωρίς θέμα)"
    sender = _extract_header(headers, "From") or "unknown@unknown"
    date_raw = _extract_header(headers, "Date")

    received_at = None
    internal_ms = message.get("internalDate")
    if internal_ms:
        try:
            received_at = datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc).isoformat()
        except Exception:
            received_at = None
    elif date_raw:
        try:
            received_at = parsedate_to_datetime(date_raw).astimezone(timezone.utc).isoformat()
        except Exception:
            received_at = None

    labels = message.get("labelIds") or []
    unread = "UNREAD" in labels
    status = "inbox" if "INBOX" in labels else "archived"

    return EmailRecord(
        id=message.get("id", ""),
        gmail_id=message.get("id"),
        subject=subject,
        sender=sender,
        body=message.get("snippet") or "",
        priority=_priority_from_subject(subject),
        status=status,
        unread=unread,
        received_at=received_at,
    )


def _fetch_gmail_emails(limit: int, include_archived: bool = False) -> list[EmailRecord]:
    service = _load_gmail_service()
    if service is None:
        return []

    try:
        max_results = _coerce_int(limit, 30)
        list_kwargs: dict[str, Any] = {
            "userId": "me",
            "maxResults": max_results,
        }
        if not include_archived:
            list_kwargs["labelIds"] = ["INBOX"]

        resp = service.users().messages().list(**list_kwargs).execute()
        refs = resp.get("messages") or []

        rows: list[EmailRecord] = []
        for ref in refs:
            msg_id = ref.get("id")
            if not msg_id:
                continue

            detail = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=["Subject", "From", "Date"],
                )
                .execute()
            )

            record = _gmail_to_email_record(detail)
            rows.append(record)
            _EMAIL_CACHE[record.id] = record

        return rows
    except Exception as exc:
        logger.warning("Gmail fetch failed: %s", exc)
        return []


def _try_parse_policy_id(email_id: str) -> int | None:
    cleaned = email_id.replace("policy-", "").strip()
    if cleaned.isdigit():
        return int(cleaned)
    return None


@router.get("/", response_model=list[EmailRecord])
def list_emails(
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    gmail_rows = _fetch_gmail_emails(limit=limit, include_archived=include_archived)
    if gmail_rows or _google_token_exists():
        return gmail_rows

    query = db.query(Policy)
    if not include_archived:
        query = query.filter(Policy.status != "archived")

    rows = query.order_by(Policy.created_at.desc()).limit(limit).all()
    return [_policy_to_email(row) for row in rows]


@router.post("/sync", response_model=EmailSyncResult)
def sync_emails(
    days_ahead: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    days_ahead_n = _coerce_int(days_ahead, 30)
    limit_n = _coerce_int(limit, 30)

    gmail_rows = _fetch_gmail_emails(limit=limit_n, include_archived=False)
    if gmail_rows or _google_token_exists():
        return EmailSyncResult(processed=len(gmail_rows), skipped=0)

    result = run_reminder_cycle(db_session=db, days_ahead=days_ahead_n)
    return EmailSyncResult(processed=len(result.eligible_for_send), skipped=result.total_skipped)


from app.services.email_service import email_service

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
                body=f"Το συμβόλαιο λήγει στις {policy.expiry_date}. Χρειάζεται ανανέωση."
            )
            return EmailReplyResult(email_id=body.email_id, reply=reply)

    cached = _EMAIL_CACHE.get(body.email_id)
    if cached:
        reply = await email_service.generate_smart_reply(
            sender=cached.sender,
            subject=cached.subject,
            body=cached.body
        )
        return EmailReplyResult(email_id=body.email_id, reply=reply)

    # Fallback if no cache found
    reply = "Ευχαριστούμε για το μήνυμά σας. Θα επικοινωνήσουμε σύντομα."
    return EmailReplyResult(email_id=body.email_id, reply=reply)


@router.post("/send")
async def send_custom_email(to: str, subject: str, body: str):
    """Mechanically sends an email via EmailService (no AI)."""
    result = await email_service.send_email(to=to, subject=subject, body=body)
    if result["status"] == "sent":
        return {"status": "ok", "provider": result.get("provider")}
    else:
        raise HTTPException(status_code=500, detail=result.get("error"))


@router.get("/ping")
def email_ping() -> dict[str, Any]:
    return {"email": "ok"}
