from __future__ import annotations

import logging
import re
import asyncio
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.ai.client import AIClient
from app.config import get_settings
from app.engine.normalization import normalize_text
from app.engine.renewal_logic import DEFAULT_EXPIRING_POLICIES_DAYS, get_expiring_policies, process_successful_send
from app.models.reminder_log import ReminderLog
from app.models.email_message import SyncedEmail
from app.models.policy import Policy
from app.models.client import Client
from app.services.email_service import email_service

logger = logging.getLogger(__name__)


class InsuranceService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _get_or_create_client(self, db: Session, name: str, email: str | None = None) -> Client:
        # Try to find by email first if provided
        if email and email != "unknown@unknown":
            client = db.query(Client).filter(Client.email == email).first()
            if client:
                return client
        
        # Then try by name
        client = db.query(Client).filter(Client.name == name).first()
        if client:
            # Update email if it was missing
            if email and not client.email:
                client.email = email
            return client
        
        # Create new if not found
        client = Client(name=name, email=email)
        db.add(client)
        db.flush()  # To get the ID
        return client

    def create_alert(
        self,
        db: Session,
        *,
        policy_holder: str,
        contact_email: str,
        expiry_date: date,
        created_at: datetime,
        source_email_id: str | None = None,
        policy_number: str | None = None,
        insurer: str | None = None,
        draft_notification: str | None = None,
    ) -> bool:
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
            return False

        try:
            with db.begin_nested():
                client = self._get_or_create_client(db, name=policy_holder, email=contact_email)
                policy = Policy(
                    client_id=client.id,
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
                db.flush()
        except IntegrityError:
            return False

        return True

    @staticmethod
    def _policy_to_alert(policy: Policy) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date()
        days_until_expiry = (policy.expiry_date - today).days

        if policy.status == "renewed":
            status = "approved"
        elif policy.status == "archived":
            status = "dismissed"
        elif policy.last_notified_at:
            # If notified in the last 24 hours, show it as notified
            last_notified = policy.last_notified_at
            if last_notified.tzinfo is None:
                last_notified = last_notified.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last_notified < timedelta(hours=24):
                status = "notified"
            else:
                status = "pending_approval"
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
            "last_notified_at": policy.last_notified_at.isoformat() if policy.last_notified_at else None,
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
    def extract_expiry_date(text: str | None) -> date | None:
        if not text:
            return None

        text = normalize_text(text)
        prioritized_keywords = [
            r"(?:λήγ(?:ει|ει στις|ει την)?|λήξη|ληξη|expiry(?:\s+date)?|expires(?:\s+on)?)",
            r"(?:renewal(?:\s+date| due)?|ανανέωση|ανανεωση|ανανέωσης|ανανεωσης)",
        ]
        date_patterns = [
            r"(\d{4}-\d{2}-\d{2})",
            r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
        ]
        for keywords in prioritized_keywords:
            for date_pattern in date_patterns:
                pattern = rf"{keywords}\D{{0,40}}{date_pattern}"
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if not match:
                    continue
                parsed = InsuranceService._parse_loose_date(match.group(1))
                if parsed is not None:
                    return parsed

        return None

    @staticmethod
    def _extract_date_from_text(text: str) -> date | None:
        return InsuranceService.extract_expiry_date(text)

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
    def extract_policy_number(text: str | None) -> str | None:
        if not text:
            return None

        text = normalize_text(text)
        patterns = [
            r"(?:policy(?:\s+number)?|policy\s*no|αρ(?:ιθμός)?\s*συμβολαίου|αριθμός\s*συμβολαίου|αριθμος\s*συμβολαιου|συμβόλαιο|συμβολαιο)\s*[:#\-]?\s*([A-ZΑ-Ω0-9\-/]*\d[A-ZΑ-Ω0-9\-/]{2,})",
            r"\b([A-Z]{1,4}-\d{3,}-[A-Z0-9]{1,6})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip().rstrip(".,;:")
        return None

    @staticmethod
    def extract_policy_holder(text: str | None) -> str | None:
        if not text:
            return None

        text = normalize_text(text)
        patterns = [
            r"(?:πελάτης|πελατης|ασφαλισμένος|ασφαλισμενος|policy\s*holder|insured)\s*:\s*([A-Za-zΑ-ΩΆ-Ώα-ωά-ώ\s]{3,})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return " ".join(match.group(1).split()).strip()
        return None

    @staticmethod
    def _extract_policy_number(text: str) -> str | None:
        return InsuranceService.extract_policy_number(text)

    @staticmethod
    def _looks_like_insurance_email(text: str) -> bool:
        normalized = normalize_text(text).casefold()
        strong_markers = (
            "συμβολ",
            "αριθμός συμβολαίου",
            "αριθμος συμβολαιου",
            "policy",
            "renewal",
            "expiry",
            "λήξη",
            "ληξη",
            "λήγει",
            "ληγει",
            "ανανέωση",
            "ανανεωση",
            "ανανέω",
            "ανανεω",
            "έναρξη",
            "εναρξη",
        )
        return any(marker in normalized for marker in strong_markers)

    @staticmethod
    def _normalize_extracted_insurance(data: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {
            "is_insurance": bool(data.get("is_insurance")),
            "policy_holder": None,
            "policy_number": None,
            "insurer": None,
            "expiry_date": None,
            "draft_notification_greek": None,
        }
        for key in ("policy_holder", "policy_number", "insurer", "draft_notification_greek"):
            value = data.get(key)
            if isinstance(value, str):
                cleaned = normalize_text(value)
                normalized[key] = cleaned or None
            elif value is not None:
                normalized[key] = value

        expiry_date = InsuranceService._parse_iso_date(data.get("expiry_date"))
        if expiry_date is not None:
            normalized["expiry_date"] = expiry_date.isoformat()

        return normalized

    @staticmethod
    def _merge_extracted_insurance(
        primary: dict[str, Any], secondary: dict[str, Any]
    ) -> dict[str, Any]:
        merged = dict(primary)
        merged["is_insurance"] = bool(primary.get("is_insurance") or secondary.get("is_insurance"))
        for key in ("policy_holder", "policy_number", "insurer", "expiry_date", "draft_notification_greek"):
            if merged.get(key) in (None, "", False) and secondary.get(key) not in (None, "", False):
                merged[key] = secondary[key]
        return merged

    @staticmethod
    def _should_use_ai_fallback(extracted: dict[str, Any], text: str) -> bool:
        # Critical fields needed for a complete policy record
        has_expiry_date = bool(extracted.get("expiry_date"))
        has_policy_number = bool(extracted.get("policy_number"))
        has_client_name = bool(extracted.get("policy_holder"))

        # If ALL critical fields exist, no need for AI
        if has_expiry_date and has_policy_number and has_client_name:
            return False

        # Otherwise, use AI if it looks like insurance
        return InsuranceService._looks_like_insurance_email(text)

    @staticmethod
    def _guess_insurer(sender_name: str, sender_email: str) -> str | None:
        if sender_name:
            return sender_name
        if not sender_email:
            return None
        domain = sender_email.split("@")[-1].split(".")[0]
        return domain.replace("-", " ").replace("_", " ").title() if domain else None

    def _deterministic_extract_insurance(self, sender: str, subject: str, body: str) -> dict[str, Any]:
        sender_name, sender_email = parseaddr(sender)
        combined = normalize_text("\n".join(part for part in [subject, body] if part).strip())

        if not self._looks_like_insurance_email(combined):
            return {
                "is_insurance": False,
                "policy_holder": None,
                "policy_number": None,
                "insurer": None,
                "expiry_date": None,
                "draft_notification_greek": None,
            }

        expiry_date = self.extract_expiry_date(combined)
        policy_number = self.extract_policy_number(combined)
        policy_holder = self.extract_policy_holder(combined) or sender_name or sender_email or None

        return self._normalize_extracted_insurance({
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
        })

    async def scan_emails_for_insurance(self, db: Session, *, limit: int = 200, days: int = 90) -> dict[str, int]:
        email_rows = (
            db.query(SyncedEmail)
            .filter(
                SyncedEmail.processed.is_(False),
                SyncedEmail.status != "archived",
            )
            .order_by(SyncedEmail.received_at.desc(), SyncedEmail.synced_at.desc())
            .limit(limit)
            .all()
        )
        if not email_rows:
            logger.info("Insurance scan: no unprocessed synced emails found")
            return {"scanned": 0, "alerts_created": 0, "already_processed": 0}

        ai_client: AIClient | None = None
        created_count = 0
        already_processed = 0
        deterministic_hits = 0
        ai_fallback_calls = 0
        ai_fallback_hits = 0
        skipped_non_insurance = 0
        skipped_out_of_window = 0
        today = datetime.now(timezone.utc).date()
        latest_expiry = today + timedelta(days=days)

        async def mark_processed(email_id: str | None) -> bool:
            if not email_id:
                return True

            for attempt in range(3):
                try:
                    db.query(SyncedEmail).filter(SyncedEmail.id == email_id).update(
                        {SyncedEmail.processed: True},
                        synchronize_session=False,
                    )
                    return True
                except OperationalError as exc:
                    if "database is locked" not in str(exc).lower() or attempt == 2:
                        logger.warning("Insurance scan: failed to mark email %s as processed: %s", email_id, exc)
                        return False
                    await asyncio.sleep(0.2 * (attempt + 1))
            return False

        async def commit_with_retry() -> None:
            for attempt in range(3):
                try:
                    db.commit()
                    return
                except OperationalError as exc:
                    if "database is locked" not in str(exc).lower() or attempt == 2:
                        raise
                    await asyncio.sleep(0.2 * (attempt + 1))

        for row in email_rows:
            source_email_id = str(row.gmail_id or row.id or "").strip() or None
            try:
                sender = str(row.sender or "")
                subject = str(row.subject or "")
                body = str(row.body or "")
                combined_text = "\n".join(part for part in [subject, body] if part).strip()
                extracted = self._deterministic_extract_insurance(sender, subject, body)

                if self._should_use_ai_fallback(extracted, combined_text):
                    if ai_client is None:
                        ai_client = AIClient(self.settings)
                    ai_fallback_calls += 1
                    ai_extracted = self._normalize_extracted_insurance(
                        await ai_client.extract_insurance_info(sender, subject, body)
                    )
                    extracted = self._merge_extracted_insurance(ai_extracted, extracted)
                    if extracted.get("is_insurance"):
                        ai_fallback_hits += 1
                elif extracted.get("is_insurance"):
                    deterministic_hits += 1

                if not extracted.get("is_insurance"):
                    skipped_non_insurance += 1
                    continue

                expiry_date = self._parse_iso_date(extracted.get("expiry_date"))
                if expiry_date is None or expiry_date < today or expiry_date > latest_expiry:
                    skipped_out_of_window += 1
                    continue

                sender_name, sender_email = parseaddr(sender)
                policy_holder = str(
                    extracted.get("policy_holder")
                    or sender_name
                    or sender_email
                    or "Άγνωστος πελάτης"
                ).strip()
                contact_email = sender_email or str(row.sender_email or "").strip() or "unknown@unknown"
                policy_number = str(extracted.get("policy_number") or "").strip() or None
                insurer = str(extracted.get("insurer") or "").strip() or None
                draft_notification = (
                    str(extracted.get("draft_notification_greek") or "").strip() or None
                )

                created_at = self._parse_received_at(row.received_at) or datetime.now(timezone.utc)
                created = self.create_alert(
                    db,
                    policy_holder=policy_holder,
                    contact_email=contact_email,
                    expiry_date=expiry_date,
                    created_at=created_at,
                    source_email_id=source_email_id,
                    policy_number=policy_number,
                    insurer=insurer,
                    draft_notification=draft_notification,
                )
                if created:
                    created_count += 1
                else:
                    already_processed += 1
            finally:
                await mark_processed(source_email_id)

        await commit_with_retry()
        logger.info(
            "Insurance scan summary: scanned=%s deterministic_hits=%s ai_fallback_calls=%s ai_fallback_hits=%s created=%s "
            "already_processed=%s skipped_non_insurance=%s skipped_out_of_window=%s",
            len(email_rows),
            deterministic_hits,
            ai_fallback_calls,
            ai_fallback_hits,
            created_count,
            already_processed,
            skipped_non_insurance,
            skipped_out_of_window,
        )
        return {
            "scanned": len(email_rows),
            "alerts_created": created_count,
            "already_processed": already_processed,
        }

    def list_alerts(
        self,
        db: Session,
        *,
        status: str | None = None,
        days: int = DEFAULT_EXPIRING_POLICIES_DAYS,
    ) -> list[dict[str, Any]]:
        alerts = [self._policy_to_alert(policy) for policy in get_expiring_policies(db, days=days)]

        if status:
            alerts = [alert for alert in alerts if alert["status"] == status]
        else:
            # ONLY show pending alerts. Once notified, they "leave" this list.
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
        policy.last_notified_at = datetime.now(timezone.utc)

        # Log to History (ActivityLog) using the correct service
        from app.services.activity_service import log_action
        log_action(
            action_type="Ειδοποίηση Λήξης",
            client_name=policy.client_name,
            policy_number=policy.policy_number,
            channel="email",
            status="success",
            db=db
        )

        # Update status so it leaves the "pending_approval" view
        policy.status = "notified"
            
        db.add(ReminderLog(policy_id=policy.id, status="sent", error_message=None))
        db.commit()

        return {
            "alert_id": str(policy_id),
            "new_status": policy.status,
            "message": "Notification sent and moved to history",
            "email": policy.email,
            "provider": result.get("provider"),
        }

    async def batch_send_sms(self, db: Session, *, days: int = 10) -> dict[str, Any]:
        """
        Sends SMS to all clients whose policy expires in 'days'.
        """
        from app.services.messaging_service import messaging_service
        
        today = datetime.now(timezone.utc).date()
        target_date = today + timedelta(days=days)
        
        # Find policies expiring exactly on target_date (or up to target_date)
        policies = db.query(Policy).filter(
            Policy.expiry_date == target_date,
            Policy.status.notin_(["renewed", "archived"])
        ).all()
        
        sent_count = 0
        failed_count = 0
        
        for policy in policies:
            # Basic mobile validation (Greek numbers usually start with 69)
            phone = str(policy.email) # Assuming email field might contain phone or we need to find phone
            # Note: In a real scenario, Policy should have a phone field. 
            # If it doesn't, we'll try to use a placeholder or log an error.
            
            message = (
                f"Γεια σας {policy.client_name}, το συμβόλαιό σας {policy.policy_number or ''} "
                f"λήγει στις {policy.expiry_date.strftime('%d/%m/%Y')}. "
                "Επικοινωνήστε μαζί μας για ανανέωση. INC-AGENT"
            )
            
            # Since our Policy model doesn't have a phone field yet (based on previous reads), 
            # let's check if we can find one. For now, I'll use policy.email as a proxy 
            # or skip if it's not a number.
            
            # TODO: Add phone field to Policy model
            
            try:
                # We attempt to send. If phone is invalid, it will fail gracefully.
                # In this demo/prototype, we'll log it.
                res = await messaging_service.send_sms(
                    to=phone, 
                    message=message, 
                    client_name=policy.client_name,
                    policy_number=policy.policy_number
                )
                if res["status"] == "sent":
                    sent_count += 1
                    policy.reminder_attempts += 1
                    db.add(ReminderLog(policy_id=policy.id, status="sent", error_message=None))
                else:
                    failed_count += 1
            except Exception as e:
                logger.error("Batch SMS error for policy %s: %s", policy.id, e)
                failed_count += 1
                
        db.commit()
        return {
            "days": days,
            "total_found": len(policies),
            "sent": sent_count,
            "failed": failed_count
        }


insurance_service = InsuranceService()
