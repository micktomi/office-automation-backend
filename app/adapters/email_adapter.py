from __future__ import annotations

from app.models.policy import Policy
from app.services.email_service import send_reminder_email


def send_policy_reminder(policy: Policy) -> dict:
    error = send_reminder_email(policy)
    if error:
        return {"status": "failed", "error": error}
    return {"status": "sent", "error": None}
