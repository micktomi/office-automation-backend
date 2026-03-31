from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String, Text

from app.models.database import Base


class SyncedEmail(Base):
    __tablename__ = "synced_emails"

    id = Column(String, primary_key=True, index=True)
    gmail_id = Column(String, unique=True, index=True, nullable=True)
    thread_id = Column(String, index=True, nullable=True)
    subject = Column(String, nullable=False)
    sender = Column(String, nullable=False)
    sender_email = Column(String, nullable=True, index=True)
    body = Column(Text, nullable=False, default="")
    classification = Column(String, nullable=False, default="probable", index=True)
    classification_label = Column(String, nullable=False, default="Πελάτης")
    priority = Column(String, nullable=False, default="medium", index=True)
    status = Column(String, nullable=False, default="inbox", index=True)
    unread = Column(Boolean, nullable=False, default=True)
    processed = Column(Boolean, nullable=False, default=False, index=True)
    received_at = Column(DateTime, nullable=True, index=True)
    synced_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
