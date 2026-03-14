from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String

from app.models.database import Base


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String, nullable=False)
    client_name = Column(String, nullable=True)
    policy_number = Column(String, nullable=True)
    channel = Column(String, nullable=True)  # email / sms / calendar
    status = Column(String, nullable=False)  # success / failed
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
