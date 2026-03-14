from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.engine.reminder_cycle import run_reminder_cycle
from app.models.database import get_db
from app.models.policy import Policy
from app.models.reminder_log import ReminderLog
from app.services.calendar_service import CalendarService

router = APIRouter(prefix="/reports", tags=["reports"])
calendar_service = CalendarService()


class MonthlyExpenseReport(BaseModel):
    month: str
    total_amount: float
    document_count: int
    breakdown: list[dict[str, Any]]


class PaymentReminderRequest(BaseModel):
    document_id: str
    title: str
    due_date: str
    amount: float | None = None
    notes: str | None = None


@router.get("/summary")
def report_summary(days: int = Query(default=30, ge=1, le=365), db: Session = Depends(get_db)):
    result = run_reminder_cycle(db_session=db, days_ahead=days)

    total_policies = db.query(Policy).count()
    renewed = db.query(Policy).filter(Policy.status == "renewed").count()
    archived = db.query(Policy).filter(Policy.status == "archived").count()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "total_policies": total_policies,
        "renewed": renewed,
        "archived": archived,
        "upcoming": result.upcoming_count,
        "overdue": result.overdue_count,
        "eligible_for_send": len(result.eligible_for_send),
        "skipped": result.total_skipped,
        "errors": result.errors,
    }


@router.get("/reminders")
def reminder_report(limit: int = Query(default=100, ge=1, le=1000), db: Session = Depends(get_db)):
    rows = db.query(ReminderLog).order_by(ReminderLog.sent_at.desc()).limit(limit).all()
    return [
        {
            "id": row.id,
            "policy_id": row.policy_id,
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            "status": row.status,
            "error_message": row.error_message,
        }
        for row in rows
    ]


@router.get("/expenses/monthly", response_model=MonthlyExpenseReport)
def monthly_expense_report(
    month: str = Query(default="", description="Month in YYYY-MM format. Defaults to current month."),
    db: Session = Depends(get_db),
):
    if not month:
        month = datetime.now(timezone.utc).strftime("%Y-%m")

    try:
        year, mon = month.split("-")
        int(year)
        int(mon)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format") from exc

    policies = db.query(Policy).all()
    monthly = [p for p in policies if p.created_at and p.created_at.strftime("%Y-%m") == month]

    breakdown = [
        {
            "id": p.id,
            "client_name": p.client_name,
            "email": p.email,
            "expiry_date": p.expiry_date.isoformat(),
            "status": p.status,
        }
        for p in monthly
    ]

    return MonthlyExpenseReport(
        month=month,
        total_amount=0.0,
        document_count=len(monthly),
        breakdown=breakdown,
    )


@router.post("/expenses/payment-reminder", status_code=201)
async def create_payment_reminder(body: PaymentReminderRequest):
    try:
        event_summary = f"Πληρωμή: {body.title}"
        if body.amount is not None:
            event_summary += f" ({body.amount} EUR)"

        description = "Υπενθύμιση πληρωμής."
        if body.notes:
            description += f"\nΣημειώσεις: {body.notes}"
        description += f"\nDocument ID: {body.document_id}"

        event = await calendar_service.create_event(
            summary=event_summary,
            start_time=f"{body.due_date}T09:00:00Z",
            end_time=f"{body.due_date}T10:00:00Z",
            description=description,
        )
        return {"status": "created", "event": event}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Calendar event creation failed: {exc}") from exc


@router.get("/ping")
def reports_ping() -> dict[str, Any]:
    return {"reports": "ok"}
