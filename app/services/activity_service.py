from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog
from app.models.database import SessionLocal

logger = logging.getLogger(__name__)


def log_action(
    action_type: str,
    client_name: str | None = None,
    policy_number: str | None = None,
    channel: str | None = None,
    status: str = "success",
    db: Session | None = None,
) -> None:
    """
    Logs an activity into the database.
    If a session (db) is provided, it uses it.
    Otherwise, it opens and commits its own session.
    """
    try:
        new_log = ActivityLog(
            action_type=action_type,
            client_name=client_name,
            policy_number=policy_number,
            channel=channel,
            status=status,
            created_at=datetime.now(timezone.utc),
        )

        if db:
            db.add(new_log)
            db.commit()
        else:
            with SessionLocal() as session:
                session.add(new_log)
                session.commit()

        logger.info(
            "Logged activity: %s for %s (%s) - %s",
            action_type,
            client_name,
            channel,
            status,
        )
    except Exception as exc:
        logger.error("Failed to log activity: %s", exc)
