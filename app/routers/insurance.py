from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.csv_adapter import parse_csv
from app.adapters.excel_adapter import parse_excel
from app.adapters.pdf_adapter import parse_pdf
from app.engine.renewal_logic import DEFAULT_EXPIRING_POLICIES_DAYS
from app.models.database import get_db
from app.models.policy import Policy
from app.schemas.insurance import InsuranceAlert, InsuranceAlertAction, InsuranceScanResult
from app.services.insurance_service import insurance_service

router = APIRouter(prefix="/insurance", tags=["insurance"])


class ApproveNotificationRequest(BaseModel):
    edited_draft: str | None = None


class PolicyActionRequest(BaseModel):
    policy_id: str


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

        # Link to client
        client = insurance_service._get_or_create_client(
            db, name=row["client_name"], email=row["email"]
        )

        policy = Policy(
            client_id=client.id,
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


@router.post("/upload-pdf", summary="Upload PDF για ανίχνευση ασφαλιστηρίων")
async def upload_pdf_policy(
    file: UploadFile = File(...),
    warning_days: int = Query(default=90, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    """
    Scans PDF for insurance policy details using regex + AI fallback.
    Reuses the email scanning extraction pipeline.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are supported")

    raw = await file.read()

    # Step 1: Extract text from PDF
    try:
        text, metadata = parse_pdf(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not text or len(text.strip()) < 10:
        raise HTTPException(
            status_code=422,
            detail="Το PDF δεν περιέχει αναγνώσιμο κείμενο"
        )

    # Step 2: Reuse email extraction pipeline - Deterministic regex extraction
    extracted = insurance_service._deterministic_extract_insurance(
        sender="",  # Not applicable for PDF
        subject=metadata.get("title") or "",
        body=text
    )

    # Step 3: AI fallback if needed (missing critical fields)
    if insurance_service._should_use_ai_fallback(extracted, text):
        from app.ai.client import AIClient
        from app.config import settings

        ai_client = AIClient(settings)
        ai_extracted = insurance_service._normalize_extracted_insurance(
            await ai_client.extract_insurance_info(
                sender="PDF Upload",
                subject=metadata.get("title") or file.filename,
                body=text
            )
        )
        extracted = insurance_service._merge_extracted_insurance(ai_extracted, extracted)

    # Step 4: Validate extracted data
    if not extracted.get("expiry_date"):
        raise HTTPException(
            status_code=422,
            detail="Δεν βρέθηκε ημερομηνία λήξης στο PDF. Παρακαλώ ελέγξτε το αρχείο."
        )

    # Step 5: Parse expiry date
    expiry_date = insurance_service._parse_iso_date(extracted.get("expiry_date"))
    if not expiry_date:
        raise HTTPException(
            status_code=422,
            detail=f"Μη έγκυρη ημερομηνία λήξης: {extracted.get('expiry_date')}"
        )

    # Step 6: Check age
    today = datetime.now(timezone.utc).date()
    days_until_expiry = (expiry_date - today).days

    if days_until_expiry > warning_days:
        return {
            "message": "Το ασφαλιστήριο δεν λήγει σύντομα",
            "expiry_date": expiry_date.isoformat(),
            "days_until_expiry": days_until_expiry,
            "warning_threshold": warning_days,
            "extracted": {
                k: (v.isoformat() if hasattr(v, 'isoformat') else v)
                for k, v in extracted.items()
                if v is not None
            }
        }

    # Step 7: Get client info
    client_name = extracted.get("policy_holder") or "Άγνωστος πελάτης"
    client_email = extracted.get("email") or "unknown@unknown"

    # Step 8: Check duplicate
    query = db.query(Policy).filter(Policy.expiry_date == expiry_date)
    if client_email:
        query = query.filter(Policy.email == client_email)
    else:
        query = query.filter(Policy.client_name == client_name)

    exists = query.first()

    if exists:
        return {
            "message": "Το ασφαλιστήριο υπάρχει ήδη στη βάση",
            "policy_id": exists.id,
            "existing_policy": {
                "client_name": exists.client_name,
                "email": exists.email,
                "expiry_date": exists.expiry_date.isoformat(),
                "policy_number": exists.policy_number,
            }
        }

    # Step 9: Get/Create Client
    client = insurance_service._get_or_create_client(
        db,
        name=client_name,
        email=client_email
    )

    # Step 10: Insert Policy
    policy = Policy(
        client_id=client.id,
        client_name=client_name,
        email=client_email,
        policy_number=extracted.get("policy_number"),
        insurer=extracted.get("insurer"),
        expiry_date=expiry_date,
        status="active",
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)

    return {
        "message": "Το ασφαλιστήριο εισήχθη επιτυχώς από PDF",
        "policy_id": policy.id,
        "days_until_expiry": days_until_expiry,
        "extracted": {
            "client_name": client_name,
            "email": client_email,
            "policy_number": extracted.get("policy_number"),
            "insurer": extracted.get("insurer"),
            "expiry_date": expiry_date.isoformat(),
        },
        "metadata": {
            "pages": metadata.get("pages"),
            "title": metadata.get("title"),
            "characters_extracted": len(text),
        }
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
    tab: str = Query(default="expiring", description="expiring | expired"),
    days: int = Query(default=DEFAULT_EXPIRING_POLICIES_DAYS, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    today = datetime.now(timezone.utc).date()

    if tab == "expired":
        # Get already expired policies (same logic as dashboard)
        policies = (
            db.query(Policy)
            .filter(
                Policy.expiry_date < today,
                Policy.status.notin_(["renewed", "archived"]),
            )
            .order_by(Policy.expiry_date.asc())
            .all()
        )
    elif tab == "expiring":
        # Get expiring soon policies (same logic as dashboard)
        from app.engine.renewal_logic import get_expiring_policies
        policies = get_expiring_policies(db, days=days)
    else:
        policies = []

    return [insurance_service._policy_to_alert(p) for p in policies]


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
