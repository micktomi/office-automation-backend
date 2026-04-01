from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.engine.renewal_logic import (
    DEFAULT_EXPIRING_POLICIES_DAYS,
    build_expiring_policies_query,
    count_expiring_policies,
    get_expiring_policies,
)
from app.models.database import get_db
from app.models.policy import Policy

router = APIRouter(tags=["dashboard"])
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _policy_payload(policy: Policy) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    reminder_attempts = policy.reminder_attempts or 0

    return {
        "id": policy.id,
        "client_name": policy.client_name,
        "email": policy.email,
        "policy_number": policy.policy_number,
        "insurer": policy.insurer,
        "expiry_date": policy.expiry_date.isoformat(),
        "days_until_expiry": (policy.expiry_date - today).days,
        "status": policy.status,
        "computed_state": policy.computed_state,
        "reminder_attempts": reminder_attempts,
        "last_reminder_sent_at": (
            policy.last_reminder_sent_at.isoformat()
            if policy.last_reminder_sent_at
            else None
        ),
        "created_at": policy.created_at.isoformat() if policy.created_at else None,
    }


def _count_expired_policies(db: Session) -> int:
    today = datetime.now(timezone.utc).date()

    return (
        db.query(Policy.id)
        .filter(
            Policy.expiry_date < today,
            Policy.status.notin_(["renewed", "archived"]),
        )
        .count()
    )


def _count_pending_emails(db: Session) -> int:
    today = datetime.now(timezone.utc).date()
    return (
        db.query(Policy.id)
        .filter(
            Policy.expiry_date >= today,
            Policy.status.notin_(["archived"]),
            func.coalesce(Policy.reminder_attempts, 0) == 0,
        )
        .count()
    )


def _count_pending_sms(db: Session, days: int) -> int:
    return (
        build_expiring_policies_query(db, days=days)
        .filter(func.coalesce(Policy.reminder_attempts, 0) == 0)
        .count()
    )


@router.get("/dashboard/summary")
def get_dashboard_summary(
    days: int = Query(default=DEFAULT_EXPIRING_POLICIES_DAYS, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    expiring_days = days
    return JSONResponse(
        content={
            "expiring_soon": count_expiring_policies(db, days=expiring_days),
            "expired": _count_expired_policies(db),
            "emails_pending": _count_pending_emails(db),
            "sms_pending": _count_pending_sms(db, days=expiring_days),
        },
        headers=NO_CACHE_HEADERS,
    )


@router.get("/policies/expiring-soon")
def list_expiring_policies(
    days: int = Query(default=DEFAULT_EXPIRING_POLICIES_DAYS, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    rows = get_expiring_policies(db, days=days)
    return [_policy_payload(policy) for policy in rows]


@router.get("/policies/expired")
def get_expired_policies(db: Session = Depends(get_db)):
    today = datetime.now(timezone.utc).date()

    rows = (
        db.query(Policy)
        .filter(
            Policy.expiry_date < today,
            Policy.status.notin_(["renewed", "archived"]),
        )
        .order_by(Policy.expiry_date.asc(), Policy.created_at.asc())
        .all()
    )

    return [_policy_payload(policy) for policy in rows]


@router.get("/emails/pending")
def get_pending_emails(
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Policy)
        .filter(
            Policy.status.notin_(["archived"]),
            func.coalesce(Policy.reminder_attempts, 0) == 0,
        )
        .order_by(Policy.expiry_date.asc(), Policy.created_at.asc())
        .limit(limit)
        .all()
    )
    return [_policy_payload(policy) for policy in rows]


@router.get("/reminders/pending")
def get_pending_reminders(
    days: int = Query(default=DEFAULT_EXPIRING_POLICIES_DAYS, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    rows = get_expiring_policies(db, days=days)
    pending = [
        _policy_payload(policy)
        for policy in rows
        if (policy.reminder_attempts or 0) == 0
    ]
    return pending
