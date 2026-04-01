from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import smtplib
import time
from datetime import datetime, timedelta, timezone
from threading import Lock

# Use a Lock for Gmail service calls as the client instance is not thread-safe
_GMAIL_CLIENT_LOCK = Lock()
_SYNC_STATE_LOCK = Lock()
LAST_SYNC: datetime | None = None
from email.message import EmailMessage
from email.utils import parsedate_to_datetime, parseaddr
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.email_message import SyncedEmail
from app.models.policy import Policy

logger = logging.getLogger(__name__)


def should_sync() -> bool:
    with _SYNC_STATE_LOCK:
        if LAST_SYNC is None:
            return True
        return datetime.now(timezone.utc) - LAST_SYNC > timedelta(seconds=60)


def _acquire_sync_slot() -> bool:
    global LAST_SYNC
    now = datetime.now(timezone.utc)
    with _SYNC_STATE_LOCK:
        if LAST_SYNC is not None and now - LAST_SYNC <= timedelta(seconds=60):
            return False
        LAST_SYNC = now
        return True


class EmailService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._email_cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip().casefold()

    def _classify_email(self, subject: str, sender: str, body: str) -> dict[str, str]:
        combined = self._normalize_text(" ".join(part for part in [subject, sender, body] if part))
        sender_email = parseaddr(sender)[1].casefold()

        noise_markers = (
            "mail delivery failed",
            "delivery failure",
            "undeliverable",
            "bounce",
            "mailer-daemon",
            "postmaster",
            "newsletter",
            "unsubscribe",
            "no-reply",
            "noreply",
            "do not reply",
            "automated message",
            "promotion",
            "marketing",
            "sale",
        )
        insurance_markers = (
            "ασφαλισ",
            "συμβολ",
            "λήξη",
            "ληξη",
            "ανανε",
            "renewal",
            "policy",
            "insurance",
            "claim",
            "coverage",
        )

        if any(marker in combined for marker in noise_markers) or any(marker in sender_email for marker in ("mailer-daemon", "postmaster", "noreply", "no-reply")):
            return {
                "classification": "irrelevant",
                "classification_label": "Άκυρο",
            }

        if any(marker in combined for marker in insurance_markers):
            return {
                "classification": "important",
                "classification_label": "Ασφαλιστήριο",
            }

        return {
            "classification": "probable",
            "classification_label": "Πελάτης",
        }

    def _ensure_email_classification(self, record: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(record)
        if normalized.get("classification") and normalized.get("classification_label"):
            return normalized

        classification = self._classify_email(
            subject=str(normalized.get("subject") or ""),
            sender=str(normalized.get("sender") or ""),
            body=str(normalized.get("body") or ""),
        )
        normalized.update(classification)
        return normalized

    def _load_gmail_service(self):
        token_path = os.getenv("GOOGLE_TOKEN_FILE", self.settings.google_token_file)
        
        if not os.path.exists(token_path):
            logger.warning("EmailService: Google token not found at %s, Gmail integration disabled", token_path)
            return None

        try:
            creds = Credentials.from_authorized_user_file(
                token_path, self.settings.google_scopes
            )
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())

            if not creds or not creds.valid:
                return None

            return build("gmail", "v1", credentials=creds, cache_discovery=False)
        except Exception as exc:
            logger.warning("EmailService: Gmail init failed: %s", exc)
            return None

    def _google_token_exists(self) -> bool:
        return Path(self.settings.google_token_file).exists()

    @staticmethod
    def _parse_received_at(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)

        if not isinstance(value, str) or not value.strip():
            return None

        cleaned = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _extract_header(headers: list[dict[str, str]], name: str) -> str:
        wanted = name.lower()
        for header in headers:
            if (header.get("name") or "").lower() == wanted:
                return header.get("value") or ""
        return ""

    @staticmethod
    def _priority_from_days(days_left: int) -> str:
        if days_left <= 7:
            return "high"
        if days_left <= 30:
            return "medium"
        return "low"

    @staticmethod
    def _priority_from_subject(subject: str) -> str:
        text = (subject or "").lower()
        if any(k in text for k in ["urgent", "asap", "επείγον", "άμεσο"]):
            return "high"
        return "medium"

    def _policy_to_email(self, policy: Policy) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date()
        days_left = (policy.expiry_date - today).days
        status = "archived" if policy.status == "archived" else "inbox"

        return {
            "id": str(policy.id),
            "policy_id": policy.id,
            "subject": f"Υπενθύμιση ανανέωσης συμβολαίου {policy.policy_number or f'#{policy.id}'}",
            "sender": f'"{policy.client_name}" <{policy.email}>',
            "body": policy.draft_notification
            or (
                f"Αγαπητέ/ή {policy.client_name},\n\n"
                f"Το ασφαλιστήριό σας λήγει στις {policy.expiry_date}. "
                "Επικοινωνήστε μαζί μας για ανανέωση."
            ),
            "priority": self._priority_from_days(max(days_left, 0)),
            "status": status,
            "unread": policy.reminder_attempts == 0,
            "received_at": policy.created_at.isoformat() if policy.created_at else None,
            "classification": "important",
            "classification_label": "Ασφαλιστήριο",
        }

    def _gmail_to_email_record(self, message: dict[str, Any]) -> dict[str, Any]:
        payload = message.get("payload") or {}
        headers = payload.get("headers") or []

        subject = self._extract_header(headers, "Subject") or "(Χωρίς θέμα)"
        sender = self._extract_header(headers, "From") or "unknown@unknown"
        date_raw = self._extract_header(headers, "Date")

        received_at = None
        internal_ms = message.get("internalDate")
        if internal_ms:
            try:
                received_at = datetime.fromtimestamp(
                    int(internal_ms) / 1000, tz=timezone.utc
                ).isoformat()
            except Exception:
                received_at = None
        elif date_raw:
            try:
                received_at = (
                    parsedate_to_datetime(date_raw).astimezone(timezone.utc).isoformat()
                )
            except Exception:
                received_at = None

        labels = message.get("labelIds") or []
        record = {
            "id": message.get("id", ""),
            "gmail_id": message.get("id"),
            "subject": subject,
            "sender": sender,
            "sender_email": parseaddr(sender)[1] or None,
            "body": self._extract_body_text(payload) or message.get("snippet") or "",
            "priority": self._priority_from_subject(subject),
            "status": "inbox" if "INBOX" in labels else "archived",
            "unread": "UNREAD" in labels,
            "received_at": received_at,
        }

        record.update(self._classify_email(subject=subject, sender=sender, body=record["body"]))
        return record

    def _fetch_gmail_emails(
        self,
        limit: int,
        include_archived: bool = False,
        include_body: bool = False,
        service: Any | None = None,
    ) -> list[dict[str, Any]]:
        service = service or self._load_gmail_service()
        if service is None:
            return []

        last_error: Exception | None = None

        for attempt in range(3):
            try:
                list_kwargs: dict[str, Any] = {
                    "userId": "me",
                    "maxResults": self._coerce_int(limit, 30),
                }
                if not include_archived:
                    list_kwargs["labelIds"] = ["INBOX"]

                with _GMAIL_CLIENT_LOCK:
                    resp = service.users().messages().list(**list_kwargs).execute()
                    refs = resp.get("messages") or []

                    if not refs:
                        return []

                    rows: list[dict[str, Any]] = []

                    def batch_callback(request_id, response, exception):
                        if exception:
                            logger.warning("EmailService: Batch fetch error: %s", exception)
                        else:
                            record = self._gmail_to_email_record(response)
                            rows.append(record)
                            self._email_cache[record["id"]] = record

                    batch = service.new_batch_http_request(callback=batch_callback)

                    for ref in refs:
                        msg_id = ref.get("id")
                        if not msg_id:
                            continue

                        get_kwargs: dict[str, Any] = {
                            "userId": "me",
                            "id": msg_id,
                            "format": "full" if include_body else "metadata",
                        }
                        if not include_body:
                            get_kwargs["metadataHeaders"] = ["Subject", "From", "Date"]

                        batch.add(service.users().messages().get(**get_kwargs))

                    batch.execute()

                rows.sort(key=lambda x: x.get("received_at") or "", reverse=True)
                return rows
            except HttpError as exc:
                last_error = exc
                status = getattr(getattr(exc, "resp", None), "status", None)
                reason = str(exc)
                if status in {429, 403} and "rateLimitExceeded" in reason and attempt < 2:
                    wait_seconds = attempt + 1
                    logger.warning(
                        "EmailService: Gmail rate limited, retrying in %s seconds: %s",
                        wait_seconds,
                        exc,
                    )
                    time.sleep(wait_seconds)
                    continue
                logger.warning("EmailService: Gmail fetch failed: %s", exc)
                return []
            except Exception as exc:
                last_error = exc
                logger.warning("EmailService: Gmail fetch failed: %s", exc)
                return []

        if last_error:
            logger.warning("EmailService: Gmail fetch exhausted retries: %s", last_error)
        return []

    def _upsert_synced_emails(self, db: Session, rows: list[dict[str, Any]]) -> None:
        bind = db.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        table = SyncedEmail.__table__
        for row in rows:
            record = self._ensure_email_classification(dict(row))
            email_id = str(record.get("gmail_id") or record.get("id") or "").strip()
            if not email_id:
                continue

            values = {
                "id": email_id,
                "gmail_id": str(record.get("gmail_id") or email_id),
                "thread_id": str(record.get("thread_id") or "") or None,
                "subject": str(record.get("subject") or "(Χωρίς θέμα)"),
                "sender": str(record.get("sender") or "unknown@unknown"),
                "sender_email": str(record.get("sender_email") or "") or None,
                "body": str(record.get("body") or ""),
                "classification": str(record.get("classification") or "probable"),
                "classification_label": str(record.get("classification_label") or "Πελάτης"),
                "priority": str(record.get("priority") or "medium"),
                "status": str(record.get("status") or "inbox"),
                "unread": bool(record.get("unread", True)),
                "received_at": self._parse_received_at(record.get("received_at")),
                "synced_at": datetime.now(timezone.utc),
            }

            if dialect_name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as dialect_insert
            elif dialect_name in {"postgresql", "postgres"}:
                from sqlalchemy.dialects.postgresql import insert as dialect_insert
            else:
                dialect_insert = None

            if dialect_insert is None:
                existing = db.query(SyncedEmail).filter(SyncedEmail.gmail_id == values["gmail_id"]).first()
                if existing is not None:
                    values["processed"] = existing.processed
                db.merge(SyncedEmail(**values))
            else:
                update_values = {key: value for key, value in values.items() if key != "id"}
                stmt = dialect_insert(table).values(**values).on_conflict_do_update(
                    index_elements=[table.c.gmail_id],
                    set_=update_values,
                )
                db.execute(stmt)

            self._email_cache[email_id] = record

        db.commit()

    def _query_synced_emails(
        self,
        db: Session,
        *,
        include_archived: bool,
        include_noise: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        query = db.query(SyncedEmail)
        if not include_archived:
            query = query.filter(SyncedEmail.status != "archived")

        rows = (
            query.order_by(SyncedEmail.received_at.desc(), SyncedEmail.synced_at.desc())
            .limit(limit)
            .all()
        )

        records = [self._ensure_email_classification(self._synced_email_to_record(row)) for row in rows]
        if not include_noise:
            records = [row for row in records if row.get("classification") != "irrelevant"]
        return records

    @staticmethod
    def _synced_email_to_record(row: SyncedEmail) -> dict[str, Any]:
        return {
            "id": row.id,
            "gmail_id": row.gmail_id or row.id,
            "thread_id": row.thread_id,
            "subject": row.subject,
            "sender": row.sender,
            "sender_email": row.sender_email,
            "body": row.body,
            "classification": row.classification,
            "classification_label": row.classification_label,
            "priority": row.priority,
            "status": row.status,
            "unread": row.unread,
            "processed": row.processed,
            "received_at": row.received_at.astimezone(timezone.utc).isoformat() if row.received_at else None,
        }

    @staticmethod
    def _decode_gmail_body(data: str | None) -> str:
        if not data:
            return ""
        try:
            padding = "=" * (-len(data) % 4)
            decoded = base64.urlsafe_b64decode(data + padding)
            return decoded.decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def _extract_body_text(self, payload: dict[str, Any]) -> str:
        mime_type = str(payload.get("mimeType") or "").lower()
        body = payload.get("body") or {}
        data = self._decode_gmail_body(body.get("data"))
        if data:
            if mime_type == "text/html":
                return re.sub(r"<[^>]+>", " ", data).strip()
            if mime_type.startswith("text/"):
                return data.strip()

        parts = payload.get("parts") or []
        plain_text_parts: list[str] = []
        html_parts: list[str] = []

        for part in parts:
            text = self._extract_body_text(part)
            if not text:
                continue
            part_mime = str(part.get("mimeType") or "").lower()
            if part_mime == "text/plain":
                plain_text_parts.append(text)
            else:
                html_parts.append(text)

        if plain_text_parts:
            return "\n".join(plain_text_parts).strip()
        if html_parts:
            return "\n".join(html_parts).strip()
        return ""

    def gmail_token_exists(self) -> bool:
        return self._google_token_exists()

    def fetch_gmail_emails(
        self,
        *,
        limit: int = 50,
        include_archived: bool = False,
        include_body: bool = False,
    ) -> list[dict[str, Any]]:
        return self._fetch_gmail_emails(
            limit=limit,
            include_archived=include_archived,
            include_body=include_body,
        )

    @staticmethod
    def _try_parse_policy_id(email_id: str) -> int | None:
        cleaned = email_id.replace("policy-", "").strip()
        if cleaned.isdigit():
            return int(cleaned)
        return None

    def list_emails(
        self,
        db: Session,
        *,
        include_archived: bool = False,
        include_noise: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        synced_rows = self._query_synced_emails(
            db,
            include_archived=include_archived,
            include_noise=include_noise,
            limit=limit,
        )
        return synced_rows

    def list_needs_reply(self, db: Session, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.list_emails(db, include_archived=False, include_noise=False, limit=limit)
        return [
            row
            for row in rows
            if row.get("status") == "inbox" and row.get("unread", False)
        ]

    def sync_inbox(
        self, db: Session, *, days_ahead: int = 30, limit: int = 30
    ) -> dict[str, Any]:
        if not self._google_token_exists():
            logger.info("EmailService: sync skipped because Gmail token is missing")
            return {"processed": 0, "skipped": 0, "status": "skip"}

        service = self._load_gmail_service()
        if service is None:
            logger.info("EmailService: sync failed because Gmail credentials are invalid or missing scopes")
            return {
                "processed": 0,
                "skipped": 0,
                "status": "error",
                "message": "Το Gmail OAuth token είναι άκυρο ή λείπουν scopes. Κάνε ξανά σύνδεση με Google.",
            }

        if not _acquire_sync_slot():
            logger.info("EmailService: sync skipped due to cooldown")
            return {"processed": 0, "skipped": 0, "status": "skip"}

        gmail_rows = self._fetch_gmail_emails(
            limit=self._coerce_int(limit, 30),
            include_archived=False,
            include_body=True,
            service=service,
        )
        if gmail_rows:
            self._upsert_synced_emails(db, gmail_rows)
            return {"processed": len(gmail_rows), "skipped": 0, "status": "ok"}
        return {"processed": 0, "skipped": 0, "status": "ok"}

    def get_email_context(self, db: Session, email_id: str) -> dict[str, Any] | None:
        synced_email = db.query(SyncedEmail).filter(SyncedEmail.id == email_id).first()
        if synced_email:
            return self._ensure_email_classification(self._synced_email_to_record(synced_email))

        policy_id = self._try_parse_policy_id(email_id)
        if policy_id is not None:
            policy = db.query(Policy).filter(Policy.id == policy_id).first()
            if policy:
                return self._policy_to_email(policy)

        cached = self._email_cache.get(email_id)
        if cached:
            return self._ensure_email_classification(cached)

        return None

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        client_name: str | None = None,
        policy_number: str | None = None,
    ) -> dict[str, Any]:
        from app.services.activity_service import log_action

        logger.info("Service: Sending Email to %s", to)

        service = self._load_gmail_service()
        if service:
            try:
                with _GMAIL_CLIENT_LOCK:
                    message = EmailMessage()
                    message.set_content(body)
                    message["To"] = to
                    message["From"] = "me"
                    message["Subject"] = subject

                    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
                    service.users().messages().send(
                        userId="me", body={"raw": encoded_message}
                    ).execute()

                log_action(
                    action_type="Αποστολή email υπενθύμισης",
                    client_name=client_name or to,
                    policy_number=policy_number,
                    channel="email",
                    status="success",
                )
                return {"status": "sent", "provider": "gmail"}
            except Exception as exc:
                logger.error("EmailService: Gmail API failed: %s", exc)
                log_action(
                    action_type="Αποστολή email υπενθύμισης",
                    client_name=client_name or to,
                    policy_number=policy_number,
                    channel="email",
                    status="failed",
                )

        try:
            msg = EmailMessage()
            msg.set_content(body)
            msg["Subject"] = subject
            msg["From"] = self.settings.from_email
            msg["To"] = to

            with smtplib.SMTP(
                self.settings.smtp_host, self.settings.smtp_port
            ) as server:
                if self.settings.smtp_user and self.settings.smtp_pass:
                    server.starttls()
                    server.login(self.settings.smtp_user, self.settings.smtp_pass)
                server.send_message(msg)

            log_action(
                action_type="Αποστολή email υπενθύμισης",
                client_name=client_name or to,
                policy_number=policy_number,
                channel="email",
                status="success",
            )
            return {"status": "sent", "provider": "smtp"}
        except Exception as exc:
            logger.error("EmailService: SMTP failed: %s", exc)
            log_action(
                action_type="Αποστολή email υπενθύμισης",
                client_name=client_name or to,
                policy_number=policy_number,
                channel="email",
                status="failed",
            )
            return {"status": "failed", "error": str(exc)}

    async def generate_smart_reply(self, sender: str, subject: str, body: str) -> str:
        try:
            from app.ai.client import AIClient

            client = AIClient(self.settings)
            return await client.generate_email_reply(sender, subject, body)
        except Exception as exc:
            logger.error("EmailService AI Error: %s", exc)
            return "Ευχαριστούμε για το μήνυμά σας. Θα επικοινωνήσουμε σύντομα."

    async def reply_email(self, db: Session, *, email_id: str) -> dict[str, Any]:
        context = self.get_email_context(db, email_id)
        if not context:
            return {
                "email_id": email_id,
                "reply": "Ευχαριστούμε για το μήνυμά σας. Θα επικοινωνήσουμε σύντομα.",
            }

        reply = await self.generate_smart_reply(
            sender=str(context.get("sender") or "unknown"),
            subject=str(context.get("subject") or "(Χωρίς θέμα)"),
            body=str(context.get("body") or ""),
        )
        return {"email_id": email_id, "reply": reply}


email_service = EmailService()


def send_reminder_email(policy: Policy) -> str | None:
    subject = f"Υπενθύμιση ανανέωσης συμβολαίου #{policy.id}"
    body = (
        f"Αγαπητέ/ή {policy.client_name},\n\n"
        f"Το ασφαλιστήριό σας λήγει στις {policy.expiry_date}. "
        "Επικοινωνήστε μαζί μας για ανανέωση."
    )

    try:
        asyncio.run(email_service.send_email(str(policy.email), subject, body))
        return None
    except RuntimeError as e:
        if "asyncio.run() cannot be called from a running event loop" in str(e):
            return "Cannot send email from within an async context"
        return str(e)
    except Exception as e:
        return str(e)
