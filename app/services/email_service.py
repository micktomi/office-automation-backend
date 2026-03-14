from __future__ import annotations

import asyncio
import base64
import logging
import re
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime, parseaddr
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.config import get_settings
from app.engine.reminder_cycle import run_reminder_cycle
from app.models.policy import Policy

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._email_cache: dict[str, dict[str, Any]] = {}

    def _load_gmail_service(self):
        token_path = Path(self.settings.google_token_file)
        if not token_path.exists():
            return None

        try:
            creds = Credentials.from_authorized_user_file(
                str(token_path), self.settings.google_scopes
            )
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")

            if not creds or not creds.valid:
                return None

            return build("gmail", "v1", credentials=creds, cache_discovery=False)
        except Exception as exc:
            logger.warning("EmailService: Gmail init failed: %s", exc)
            return None

    def _google_token_exists(self) -> bool:
        return Path(self.settings.google_token_file).exists()

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
        return {
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

    def _fetch_gmail_emails(
        self,
        limit: int,
        include_archived: bool = False,
        include_body: bool = False,
    ) -> list[dict[str, Any]]:
        service = self._load_gmail_service()
        if service is None:
            return []

        try:
            list_kwargs: dict[str, Any] = {
                "userId": "me",
                "maxResults": self._coerce_int(limit, 30),
            }
            if not include_archived:
                list_kwargs["labelIds"] = ["INBOX"]

            resp = service.users().messages().list(**list_kwargs).execute()
            refs = resp.get("messages") or []
            rows: list[dict[str, Any]] = []

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

                detail = service.users().messages().get(**get_kwargs).execute()

                record = self._gmail_to_email_record(detail)
                rows.append(record)
                self._email_cache[record["id"]] = record

            return rows
        except Exception as exc:
            logger.warning("EmailService: Gmail fetch failed: %s", exc)
            return []

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
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        gmail_rows = self._fetch_gmail_emails(
            limit=limit, include_archived=include_archived
        )
        if gmail_rows or self._google_token_exists():
            return gmail_rows

        query = db.query(Policy)
        if not include_archived:
            query = query.filter(Policy.status != "archived")

        rows = query.order_by(Policy.created_at.desc()).limit(limit).all()
        return [self._policy_to_email(row) for row in rows]

    def list_needs_reply(self, db: Session, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.list_emails(db, include_archived=False, limit=limit)
        return [
            row
            for row in rows
            if row.get("status") == "inbox" and row.get("unread", False)
        ]

    def sync_inbox(
        self, db: Session, *, days_ahead: int = 30, limit: int = 30
    ) -> dict[str, int]:
        gmail_rows = self._fetch_gmail_emails(
            limit=self._coerce_int(limit, 30), include_archived=False
        )
        if gmail_rows or self._google_token_exists():
            return {"processed": len(gmail_rows), "skipped": 0}

        result = run_reminder_cycle(
            db_session=db, days_ahead=self._coerce_int(days_ahead, 30)
        )
        return {
            "processed": len(result.eligible_for_send),
            "skipped": result.total_skipped,
        }

    def get_email_context(self, db: Session, email_id: str) -> dict[str, Any] | None:
        policy_id = self._try_parse_policy_id(email_id)
        if policy_id is not None:
            policy = db.query(Policy).filter(Policy.id == policy_id).first()
            if policy:
                return self._policy_to_email(policy)

        cached = self._email_cache.get(email_id)
        if cached:
            return cached

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
