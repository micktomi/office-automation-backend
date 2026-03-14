from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog
from app.models.database import get_db

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("")
def get_activity(db: Session = Depends(get_db)):
    """
    Returns the last 50 activity logs sorted by most recent.
    """
    logs = (
        db.query(ActivityLog)
        .order_by(ActivityLog.created_at.desc())
        .limit(50)
        .all()
    )
    return logs
