from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any

from sqlalchemy.orm import Session

from app.ai.client import AIClient
from app.config import get_settings
from app.engine.renewal_logic import process_successful_send
from app.engine.reminder_cycle import run_reminder_cycle
from app.engine.renewal_logic import get_upcoming_policies
from app.models.reminder_log import ReminderLog
from app.models.policy import Policy
from app.services.email_service import email_service

logger = logging.getLogger(__name__)


class InsuranceService:
    def __init__(self) -> None:
        self.settings = get_settings()

    @staticmethod
    def _policy_to_alert(policy: Policy) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date()
        days_until_expiry = (policy.expiry_date - today).days

        if policy.status == "renewed":
            status = "approved"
        elif policy.status == "archived":
            status = "dismissed"
        else:
            status = "pending_approval"

        return {
            "id": str(policy.id),
            "policy_id": policy.id,
            "policy_holder": policy.client_name,
            "policy_number": policy.policy_number or f"POL-{policy.id:05d}",
            "insurer": policy.insurer or "Γενική Ασφάλιση",
            "email": policy.email,
            "expiry_date": policy.expiry_date.isoformat(),
            "days_until_expiry": days_until_expiry,
            "status": status,
            "draft_notification": policy.draft_notification
            or (
                f"Αγαπητέ/ή {policy.client_name}, το ασφαλιστήριό σας λήγει στις {policy.expiry_date}. "
                "Προτείνουμε να προχωρήσουμε σε ανανέωση εντός των επόμενων ημερών."
            ),
            "created_at": policy.created_at.isoformat() if policy.created_at else datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _parse_policy_id(value: str) -> int:
        cleaned = value.replace("policy-", "").strip()
        if not cleaned.isdigit():
            raise ValueError("Invalid policy id")
        return int(cleaned)

    @staticmethod
    def _parse_iso_date(value: Any) -> date | None:
        if not isinstance(value, str) or not value.strip():
            return None

        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None

    @staticmethod
    def _parse_received_at(value: Any) -> datetime | None:
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
    def _build_draft_notification(
        policy_holder: str | None,
        policy_number: str | None,
        expiry_date: date,
    ) -> str:
        holder = policy_holder or "ο πελάτης"
        policy_label = f" με αριθμό {policy_number}" if policy_number else ""
        return (
            f"Σας ενημερώνουμε ότι το ασφαλιστήριο συμβόλαιο{policy_label} για {holder} "
            f"λήγει στις {expiry_date.isoformat()}.\n\n"
            "Παρακαλούμε επικοινωνήστε μαζί μας ώστε να προχωρήσουμε έγκαιρα στις "
            "απαραίτητες ενέργειες για την ανανέωσή του."
        )

    @staticmethod
    def _extract_date_from_text(text: str) -> date | None:
        keyword_patterns = [
            r"(?:λήγ(?:ει|ει στις|ει την)?|λήξη|expiry(?:\s+date)?|expires(?:\s+on)?|renewal(?:\s+date| due)?)\D{0,20}(\d{4}-\d{2}-\d{2})",
            r"(?:λήγ(?:ει|ει στις|ει την)?|λήξη|expiry(?:\s+date)?|expires(?:\s+on)?|renewal(?:\s+date| due)?)\D{0,20}(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
        ]
        for pattern in keyword_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            parsed = InsuranceService._parse_loose_date(match.group(1))
            if parsed is not None:
                return parsed

        for candidate in re.findall(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})\b", text):
            parsed = InsuranceService._parse_loose_date(candidate)
            if parsed is not None:
                return parsed

        return None

    @staticmethod
    def _parse_loose_date(value: str) -> date | None:
        cleaned = value.strip()
        formats = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y")
        for fmt in formats:
            try:
                return datetime.strptime(cleaned, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _extract_policy_number(text: str) -> str | None:
        patterns = [
            r"(?:policy(?:\s+number)?|policy\s*no|αρ(?:ιθμός)?\s*συμβολαίου|συμβόλαιο)\s*[:#\-]?\s*([A-ZΑ-Ω0-9][A-ZΑ-Ω0-9\-/]{3,})",
            r"\b([A-Z]{1,4}-\d{3,}-[A-Z0-9]{1,6})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    @staticmethod
    def _guess_insurer(sender_name: str, sender_email: str) -> str | None:
        if sender_name:
            return sender_name
        if not sender_email:
            return None
        domain = sender_email.split("@")[-1].split(".")[0]
        return domain.replace("-", " ").replace("_", " ").title() if domain else None

    def _fallback_extract_insurance(self, sender: str, subject: str, body: str) -> dict[str, Any]:
        sender_name, sender_email = parseaddr(sender)
        combined = "\n".join(part for part in [subject, body] if part).strip()
        normalized = combined.lower()

        insurance_keywords = (
            "ασφαλισ",
            "συμβολ",
            "ανανέω",
            "ανανεω",
            "λήξη",
            "ληγει",
            "insurance",
            "policy",
            "renewal",
            "expiry",
        )
        if not any(keyword in normalized for keyword in insurance_keywords):
            return {
                "is_insurance": False,
                "policy_holder": None,
                "policy_number": None,
                "insurer": None,
                "expiry_date": None,
                "draft_notification_greek": None,
            }

        expiry_date = self._extract_date_from_text(combined)
        policy_number = self._extract_policy_number(combined)
        policy_holder = sender_name or sender_email or None

        return {
            "is_insurance": expiry_date is not None or policy_number is not None,
            "policy_holder": policy_holder,
            "policy_number": policy_number,
            "insurer": self._guess_insurer(sender_name, sender_email),
            "expiry_date": expiry_date.isoformat() if expiry_date else None,
            "draft_notification_greek": (
                self._build_draft_notification(policy_holder, policy_number, expiry_date)
                if expiry_date
                else None
            ),
        }

    async def scan_emails_for_insurance(self, db: Session, *, limit: int = 200, days: int = 90) -> dict[str, int]:
        if not email_service.gmail_token_exists():
            logger.info("Insurance scan: Gmail not configured, falling back to reminder cycle")
            result = run_reminder_cycle(db_session=db, days_ahead=days)
            return {
                "scanned": min(result.upcoming_count + result.overdue_count, limit),
                "alerts_created": min(len(result.eligible_for_send), limit),
                "already_processed": result.total_skipped,
            }

        gmail_rows = email_service.fetch_gmail_emails(
            limit=limit,
            include_archived=False,
            include_body=True,
        )
        if not gmail_rows:
            return {"scanned": 0, "alerts_created": 0, "already_processed": 0}

        ai_client = AIClient(self.settings)
        created_count = 0
        already_processed = 0
        today = datetime.now(timezone.utc).date()
        latest_expiry = today + timedelta(days=days)

        for row in gmail_rows:
            sender = str(row.get("sender") or "")
            subject = str(row.get("subject") or "")
            body = str(row.get("body") or "")
            extracted = await ai_client.extract_insurance_info(sender, subject, body)
            fallback = self._fallback_extract_insurance(sender, subject, body)

            if not extracted.get("is_insurance") and fallback.get("is_insurance"):
                extracted = fallback
            else:
                for key, value in fallback.items():
                    if extracted.get(key) in (None, "", False) and value not in (None, "", False):
                        extracted[key] = value

            if not extracted.get("is_insurance"):
                continue

            expiry_date = self._parse_iso_date(extracted.get("expiry_date"))
            if expiry_date is None or expiry_date < today or expiry_date > latest_expiry:
                continue

            sender_name, sender_email = parseaddr(sender)
            policy_holder = str(
                extracted.get("policy_holder")
                or sender_name
                or sender_email
                or "Άγνωστος πελάτης"
            ).strip()
            contact_email = sender_email or str(row.get("sender_email") or "").strip() or "unknown@unknown"
            source_email_id = str(row.get("gmail_id") or row.get("id") or "").strip() or None
            policy_number = str(extracted.get("policy_number") or "").strip() or None
            insurer = str(extracted.get("insurer") or "").strip() or None
            draft_notification = (
                str(extracted.get("draft_notification_greek") or "").strip() or None
            )

            existing_query = db.query(Policy)
            if source_email_id:
                existing = existing_query.filter(Policy.source_email_id == source_email_id).first()
            else:
                existing = (
                    existing_query.filter(
                        Policy.client_name == policy_holder,
                        Policy.email == contact_email,
                        Policy.expiry_date == expiry_date,
                    ).first()
                )
            if existing:
                already_processed += 1
                continue

            created_at = self._parse_received_at(row.get("received_at")) or datetime.now(timezone.utc)
            policy = Policy(
                client_name=policy_holder,
                email=contact_email,
                policy_number=policy_number,
                insurer=insurer,
                draft_notification=draft_notification,
                source_email_id=source_email_id,
                expiry_date=expiry_date,
                status="active",
            )
            policy.created_at = created_at
            db.add(policy)
            created_count += 1

        db.commit()
        return {
            "scanned": len(gmail_rows),
            "alerts_created": created_count,
            "already_processed": already_processed,
        }

    def list_alerts(self, db: Session, *, status: str | None = None, days: int = 90) -> list[dict[str, Any]]:
        upcoming = get_upcoming_policies(db, days=days)
        base: list[Policy] = list(upcoming)
        
        # If no status is provided, or specifically 'approved'/'dismissed' is requested, 
        # then we fetch those. Otherwise, we stick to upcoming (pending).
        if status in ("approved", "dismissed", None):
            approved = db.query(Policy).filter(Policy.status == "renewed").all()
            dismissed = db.query(Policy).filter(Policy.status == "archived").all()

            seen_ids = {policy.id for policy in base}
            for policy in approved + dismissed:
                if policy.id not in seen_ids:
                    base.append(policy)

        alerts = [self._policy_to_alert(policy) for policy in base]
        
        # Final strict filtering based on the computed alert status
        if status:
            alerts = [alert for alert in alerts if alert["status"] == status]
        else:
            # Default behavior: if no status requested, only show what's NOT already dealt with
            alerts = [alert for alert in alerts if alert["status"] == "pending_approval"]

        alerts.sort(key=lambda alert: alert.get("days_until_expiry") if alert.get("days_until_expiry") is not None else 99999)
        return alerts

    def approve_alert(self, db: Session, *, alert_id: str, edited_draft: str | None = None) -> dict[str, Any]:
        policy_id = self._parse_policy_id(alert_id)
        policy = db.query(Policy).filter(Policy.id == policy_id).first()
        if not policy:
            raise ValueError("Policy not found")

        policy.status = "renewed"
        db.commit()
        return {
            "alert_id": str(policy_id),
            "new_status": "approved",
            "message": "Alert approved with edited draft" if edited_draft else "Alert approved",
        }

    def dismiss_alert(self, db: Session, *, alert_id: str) -> dict[str, Any]:
        policy_id = self._parse_policy_id(alert_id)
        policy = db.query(Policy).filter(Policy.id == policy_id).first()
        if not policy:
            raise ValueError("Policy not found")

        policy.status = "archived"
        db.commit()
        return {"alert_id": str(policy_id), "new_status": "dismissed", "message": "Alert dismissed"}

    async def notify_alert(self, db: Session, *, alert_id: str, custom_message: str | None = None) -> dict[str, Any]:
        policy_id = self._parse_policy_id(alert_id)
        policy = db.query(Policy).filter(Policy.id == policy_id).first()
        if not policy:
            raise ValueError("Policy not found")
        if not policy.email:
            raise ValueError("Policy has no email")

        subject = f"Υπενθύμιση ανανέωσης ασφαλιστηρίου {policy.policy_number or f'#{policy.id}'}"
        body = custom_message or self._policy_to_alert(policy)["draft_notification"]
        result = await email_service.send_email(
            policy.email,
            subject,
            body,
            client_name=policy.client_name,
            policy_number=policy.policy_number,
        )

        if result.get("status") != "sent":
            db.add(
                ReminderLog(
                    policy_id=policy.id,
                    status="failed",
                    error_message=str(result.get("error") or "Notification send failed"),
                )
            )
            db.commit()
            raise ValueError(str(result.get("error") or "Notification send failed"))

        process_successful_send(policy)
        # Ensure the status is set to something that will filter it out of pending_approval
        if policy.status == "active":
            policy.status = "reminder_sent"
            
        db.add(ReminderLog(policy_id=policy.id, status="sent", error_message=None))
        db.commit()

        return {
            "alert_id": str(policy_id),
            "new_status": policy.status,
            "message": "Notification sent",
            "email": policy.email,
            "provider": result.get("provider"),
        }


insurance_service = InsuranceService()
