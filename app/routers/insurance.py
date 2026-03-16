from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.csv_adapter import parse_csv
from app.adapters.excel_adapter import parse_excel
from app.engine.renewal_logic import get_upcoming_policies
from app.models.database import get_db
from app.models.policy import Policy
from app.schemas.insurance import InsuranceAlert, InsuranceAlertAction, InsuranceScanResult
from app.services.insurance_service import insurance_service

router = APIRouter(prefix="/insurance", tags=["insurance"])


class ApproveNotificationRequest(BaseModel):
    edited_draft: str | None = None


class PolicyActionRequest(BaseModel):
    policy_id: str


def _policy_to_alert(policy: Policy) -> InsuranceAlert:
    today = datetime.now(timezone.utc).date()
    days_until_expiry = (policy.expiry_date - today).days

    if policy.status == "renewed":
        status = "approved"
    elif policy.status == "archived":
        status = "dismissed"
    else:
        status = "pending_approval"

    draft_notification = (
        policy.draft_notification
        or (
            f"Αγαπητέ/ή {policy.client_name}, το ασφαλιστήριό σας λήγει στις {policy.expiry_date}. "
            "Προτείνουμε να προχωρήσουμε σε ανανέωση εντός των επόμενων ημερών."
        )
    )

    return InsuranceAlert(
        id=str(policy.id),
        policy_id=policy.id,
        policy_holder=policy.client_name,
        policy_number=policy.policy_number or f"POL-{policy.id:05d}",
        insurer=policy.insurer or "Γενική Ασφάλιση",
        email=policy.email,
        expiry_date=policy.expiry_date.isoformat(),
        days_until_expiry=days_until_expiry,
        status=status,
        draft_notification=draft_notification,
        created_at=policy.created_at.isoformat() if policy.created_at else datetime.now(timezone.utc).isoformat(),
    )


def _parse_policy_id(value: str) -> int:
    cleaned = value.replace("policy-", "").strip()
    if not cleaned.isdigit():
        raise HTTPException(status_code=400, detail="Invalid policy id")
    return int(cleaned)


@router.post("/scan", response_model=InsuranceScanResult)
async def scan_emails_for_insurance(
    limit: int = Query(default=200, ge=1, le=500),
    days: int = Query(default=90, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    result = await insurance_service.scan_emails_for_insurance(db, limit=limit, days=days)
    return InsuranceScanResult(**result)


@router.post("/upload", summary="Upload Excel/CSV για λήξεις ασφαλιστηρίων")
async def upload_policies(
    file: UploadFile = File(...),
    warning_days: int = Query(default=90, ge=1, le=3650),
    client_name_col: str | None = Form(default=None),
    email_col: str | None = Form(default=None),
    expiry_date_col: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name is required")

    filename = file.filename.lower()
    raw = await file.read()

    manual_mapping = {
        "client_name": client_name_col,
        "email": email_col,
        "expiry_date": expiry_date_col,
    }
    has_manual_mapping = any(value for value in manual_mapping.values())

    try:
        if filename.endswith(".csv"):
            rows, invalid_rows, mapping = parse_csv(raw, mapping=manual_mapping if has_manual_mapping else None)
        elif filename.endswith((".xlsx", ".xls")):
            rows, invalid_rows, mapping = parse_excel(raw, mapping=manual_mapping if has_manual_mapping else None)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file format. Upload .xlsx/.xls/.csv")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    inserted = 0
    skipped_duplicates = 0
    today = datetime.now(timezone.utc).date()

    for row in rows:
        days_until_expiry = (row["expiry_date"] - today).days
        if days_until_expiry > warning_days:
            continue

        exists = (
            db.query(Policy)
            .filter(Policy.email == row["email"], Policy.expiry_date == row["expiry_date"])
            .first()
        )
        if exists:
            skipped_duplicates += 1
            continue

        policy = Policy(
            client_name=row["client_name"],
            email=row["email"],
            expiry_date=row["expiry_date"],
            status="active",
        )
        db.add(policy)
        inserted += 1

    db.commit()

    return {
        "imported": inserted,
        "skipped_duplicates": skipped_duplicates,
        "skipped_invalid_rows": len(invalid_rows),
        "mapping_used": mapping,
        "total_rows_in_file": len(rows) + len(invalid_rows),
    }


@router.post("/batch-sms-reminders")
async def batch_sms_reminders(days: int = 10, db: Session = Depends(get_db)):
    """
    Sends bulk SMS to all policies expiring in 'days' days.
    """
    result = await insurance_service.batch_send_sms(db, days=days)
    return result


@router.get("/alerts", response_model=list[InsuranceAlert])
def list_insurance_alerts(
    status: str | None = Query(default=None, description="pending_approval | approved | dismissed"),
    days: int = Query(default=90, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    upcoming = get_upcoming_policies(db, days=days)

    base: list[Policy] = list(upcoming)
    approved = db.query(Policy).filter(Policy.status == "renewed").all()
    dismissed = db.query(Policy).filter(Policy.status == "archived").all()

    seen_ids = {p.id for p in base}
    for policy in approved + dismissed:
        if policy.id not in seen_ids:
            base.append(policy)

    alerts = [_policy_to_alert(p) for p in base]

    if status:
        alerts = [a for a in alerts if a.status == status]

    alerts.sort(key=lambda a: (a.days_until_expiry if a.days_until_expiry is not None else 99999))
    return alerts


@router.post("/alerts/{alert_id}/approve", response_model=InsuranceAlertAction)
def approve_insurance_notification(
    alert_id: str,
    body: ApproveNotificationRequest,
    db: Session = Depends(get_db),
):
    policy_id = _parse_policy_id(alert_id)
    policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    policy.status = "renewed"
    db.commit()

    message = "Alert approved"
    if body.edited_draft:
        message = "Alert approved with edited draft"

    return InsuranceAlertAction(alert_id=str(policy_id), new_status="approved", message=message)


@router.post("/alerts/{alert_id}/dismiss", response_model=InsuranceAlertAction)
def dismiss_insurance_alert(alert_id: str, db: Session = Depends(get_db)):
    policy_id = _parse_policy_id(alert_id)
    policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    policy.status = "archived"
    db.commit()

    return InsuranceAlertAction(alert_id=str(policy_id), new_status="dismissed", message="Alert dismissed")


@router.post("/approve", response_model=InsuranceAlertAction)
def approve_insurance_by_body(body: PolicyActionRequest, db: Session = Depends(get_db)):
    return approve_insurance_notification(alert_id=body.policy_id, body=ApproveNotificationRequest(), db=db)


@router.post("/dismiss", response_model=InsuranceAlertAction)
def dismiss_insurance_by_body(body: PolicyActionRequest, db: Session = Depends(get_db)):
    return dismiss_insurance_alert(alert_id=body.policy_id, db=db)


@router.get("/ping")
def insurance_ping() -> dict[str, Any]:
    return {"insurance": "ok"}
