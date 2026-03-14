from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.policy import Policy
from app.models.reminder_log import ReminderLog


class DocumentService:
    def list_documents(self, db: Session, *, limit: int = 50) -> list[dict[str, Any]]:
        policies = db.query(Policy).order_by(Policy.created_at.desc()).limit(limit).all()
        logs = db.query(ReminderLog).order_by(ReminderLog.sent_at.desc()).limit(limit).all()

        documents: list[dict[str, Any]] = [
            {
                "id": f"policy-{policy.id}",
                "title": f"Συμβόλαιο {policy.client_name}",
                "type": "policy",
                "email": policy.email,
                "status": policy.status,
                "expiry_date": policy.expiry_date.isoformat(),
                "created_at": policy.created_at.isoformat() if policy.created_at else None,
            }
            for policy in policies
        ]
        documents.extend(
            {
                "id": f"reminder-{log.id}",
                "title": f"Υπενθύμιση συμβολαίου #{log.policy_id}",
                "type": "reminder_log",
                "policy_id": log.policy_id,
                "status": log.status,
                "error_message": log.error_message,
                "created_at": log.sent_at.isoformat() if log.sent_at else None,
            }
            for log in logs
        )

        documents.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return documents[:limit]


document_service = DocumentService()
