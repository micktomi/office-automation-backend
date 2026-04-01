from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.engine.reminder_cycle import AUTO_SEND, run_reminder_cycle
from app.models.database import SessionLocal
from app.models.policy import Policy
from app.models.reminder_log import ReminderLog
from app.services.email_service import email_service

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _run_daily_cycle() -> None:
    db: Session | None = None

    try:
        db = SessionLocal()
        result = run_reminder_cycle(db_session=db, days_ahead=30)

        logger.info(
            "[ReminderCycle] ran_at=%s | upcoming=%d | overdue=%d | eligible=%d | skipped=%d | errors=%d | auto_send=%s",
            result.ran_at.isoformat(),
            result.upcoming_count,
            result.overdue_count,
            len(result.eligible_for_send),
            result.total_skipped,
            len(result.errors),
            AUTO_SEND,
        )

        for snapshot in result.eligible_for_send:
            logger.info(
                "[ReminderCycle] ELIGIBLE | id=%s | client=%s | email=%s | expiry=%s | attempts=%d",
                snapshot["id"],
                snapshot["client_name"],
                snapshot["email"],
                snapshot["expiry_date"],
                snapshot["reminder_attempts"],
            )

            if AUTO_SEND:
                policy = db.query(Policy).filter(Policy.id == snapshot["id"]).first()
                if policy:
                    subject = f"Υπενθύμιση ανανέωσης συμβολαίου #{policy.id}"
                    body = (
                        f"Αγαπητέ/ή {policy.client_name},\n\n"
                        f"Το ασφαλιστήριό σας λήγει στις {policy.expiry_date}. "
                        "Επικοινωνήστε μαζί μας για ανανέωση."
                    )
                    send_result = asyncio.run(
                        email_service.send_email(str(policy.email), subject, body)
                    )
                    if send_result.get("status") == "sent":
                        policy.last_reminder_sent_at = datetime.now(timezone.utc)
                        policy.reminder_attempts += 1

                        reminder_log = ReminderLog(
                            policy_id=policy.id,
                            sent_at=datetime.now(timezone.utc),
                            status="sent",
                            error_message=None,
                        )
                        db.add(reminder_log)
                        db.commit()
                        logger.info(
                            "[ReminderCycle] SENT | id=%s | to=%s",
                            policy.id,
                            policy.email,
                        )
                    else:
                        reminder_log = ReminderLog(
                            policy_id=policy.id,
                            sent_at=datetime.now(timezone.utc),
                            status="failed",
                            error_message=send_result.get("error", "Unknown error"),
                        )
                        db.add(reminder_log)
                        db.commit()
                        logger.error(
                            "[ReminderCycle] SEND_FAILED | id=%s | error=%s",
                            policy.id,
                            send_result.get("error"),
                        )

        for error in result.errors:
            logger.error("[ReminderCycle] ERROR | %s", error)

    except Exception as exc:
        logger.exception("[ReminderCycle] FATAL | %s", str(exc))

    finally:
        if db:
            db.close()


def start_scheduler() -> None:
    global _scheduler

    if _scheduler is not None:
        logger.warning("[Scheduler] Already running - skipping init.")
        return

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        func=_run_daily_cycle,
        trigger=CronTrigger(hour=8, minute=0),
        id="daily_reminder_cycle",
        name="Daily Insurance Renewal Reminder Cycle",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info("[Scheduler] Started. Daily cycle scheduled at 08:00.")


def stop_scheduler() -> None:
    global _scheduler

    if _scheduler is None:
        return

    _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("[Scheduler] Stopped.")


def get_scheduler_status() -> dict:
    if _scheduler is None:
        return {"running": False, "jobs": []}

    jobs = [
        {
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        for job in _scheduler.get_jobs()
    ]

    return {"running": _scheduler.running, "jobs": jobs}
