from sqlalchemy import Column, Integer, String, Date, DateTime
from datetime import datetime, timezone
from app.models.database import Base

class Policy(Base):
    __tablename__ = "policies"

    id = Column(Integer, primary_key=True, index=True)
    client_name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    policy_number = Column(String, nullable=True)
    insurer = Column(String, nullable=True)
    draft_notification = Column(String, nullable=True)
    source_email_id = Column(String, nullable=True)
    expiry_date = Column(Date, nullable=False)
    status = Column(String, default="active")
    last_reminder_sent_at = Column(DateTime, nullable=True)
    reminder_attempts = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def computed_state(self) -> str:
        """
        Read-only state rule:
        If expiry_date < today -> automatically considered overdue.
        If reminder_attempts >= 3 -> automatically considered overdue.
        """
        if self.status in ("renewed", "archived"):
            return self.status
            
        today = datetime.now(timezone.utc).date()
        if self.expiry_date < today:
            return "overdue"
            
        if self.reminder_attempts >= 3:
            return "overdue"
            
        return self.status
