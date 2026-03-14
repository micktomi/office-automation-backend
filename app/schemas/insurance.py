from __future__ import annotations

from pydantic import BaseModel


class InsuranceAlert(BaseModel):
    id: str
    policy_id: int
    policy_holder: str | None = None
    policy_number: str | None = None
    insurer: str | None = None
    email: str | None = None
    expiry_date: str | None = None
    days_until_expiry: int | None = None
    status: str = "pending_approval"
    draft_notification: str | None = None
    created_at: str


class InsuranceScanResult(BaseModel):
    scanned: int
    alerts_created: int
    already_processed: int


class InsuranceAlertAction(BaseModel):
    alert_id: str
    new_status: str
    message: str | None = None
