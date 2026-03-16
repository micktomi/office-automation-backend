from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.orm import Session

from app.services.email_service import email_service
from app.services.messaging_service import messaging_service
from app.services.calendar_service import calendar_service
from app.services.document_service import document_service
from app.services.insurance_service import insurance_service
from app.services.task_service import task_service

logger = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any], Session], Awaitable[Any]]


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


async def _email_list(payload: dict[str, Any], db: Session) -> Any:
    return email_service.list_emails(
        db,
        include_archived=_as_bool(payload.get("include_archived"), False),
        limit=_as_int(payload.get("limit"), 50),
    )


async def _email_needs_reply(payload: dict[str, Any], db: Session) -> Any:
    return email_service.list_needs_reply(db, limit=_as_int(payload.get("limit"), 50))


async def _email_sync(payload: dict[str, Any], db: Session) -> Any:
    return email_service.sync_inbox(
        db,
        days_ahead=_as_int(payload.get("days_ahead") or payload.get("days"), 30),
        limit=_as_int(payload.get("limit"), 30),
    )


async def _email_reply(payload: dict[str, Any], db: Session) -> Any:
    email_id = payload.get("email_id") or payload.get("id")
    if not isinstance(email_id, str) or not email_id.strip():
        raise ValueError("email_id is required")
    return await email_service.reply_email(db, email_id=email_id)


async def _email_send(payload: dict[str, Any], db: Session) -> Any:
    del db
    to = payload.get("to")
    body = payload.get("body") or payload.get("message")
    subject = payload.get("subject", "Ενημέρωση από το Ασφαλιστικό Γραφείο")
    if not to or not body:
        raise ValueError("Recipient and body are required")
    return await email_service.send_email(to=str(to), subject=str(subject), body=str(body))


async def _messaging_send(payload: dict[str, Any], db: Session) -> Any:
    del db
    phone = payload.get("phone") or payload.get("to")
    message = payload.get("message") or payload.get("text")
    provider = str(payload.get("provider") or "whatsapp").lower()
    client_name = payload.get("client_name")
    policy_number = payload.get("policy_number")

    if not phone or not message:
        raise ValueError("Phone and message are required")

    if provider == "sms":
        return await messaging_service.send_sms(
            to=str(phone),
            message=str(message),
            client_name=client_name,
            policy_number=policy_number,
        )
    return await messaging_service.send_whatsapp(
        to=str(phone),
        message=str(message),
        client_name=client_name,
        policy_number=policy_number,
    )


async def _calendar_list(payload: dict[str, Any], db: Session) -> Any:
    del db
    return await calendar_service.list_events(max_results=_as_int(payload.get("limit"), 10))


async def _calendar_create(payload: dict[str, Any], db: Session) -> Any:
    del db
    summary = payload.get("summary") or payload.get("title")
    start = payload.get("start") or payload.get("start_time")
    end = payload.get("end") or payload.get("end_time")
    if not summary or not start or not end:
        raise ValueError("Summary, start, and end times are required")

    return await calendar_service.create_event(
        summary=str(summary),
        start_time=str(start),
        end_time=str(end),
        description=str(payload.get("description", "")),
        location=str(payload.get("location", "")),
    )


async def _insurance_alerts(payload: dict[str, Any], db: Session) -> Any:
    return insurance_service.list_alerts(
        db,
        status=payload.get("status") if isinstance(payload.get("status"), str) else None,
        days=_as_int(payload.get("days"), 90),
    )


async def _insurance_scan(payload: dict[str, Any], db: Session) -> Any:
    return await insurance_service.scan_emails_for_insurance(
        db,
        limit=_as_int(payload.get("limit"), 200),
        days=_as_int(payload.get("days"), 90),
    )


async def _insurance_batch_sms(payload: dict[str, Any], db: Session) -> Any:
    days = _as_int(payload.get("days"), 10)
    return await insurance_service.batch_send_sms(db, days=days)


async def _insurance_approve(payload: dict[str, Any], db: Session) -> Any:
    alert_id = payload.get("alert_id") or payload.get("policy_id") or payload.get("id")
    if not isinstance(alert_id, str) or not alert_id.strip():
        raise ValueError("alert_id/policy_id is required")
    return insurance_service.approve_alert(
        db,
        alert_id=alert_id,
        edited_draft=payload.get("edited_draft") if isinstance(payload.get("edited_draft"), str) else None,
    )


async def _insurance_dismiss(payload: dict[str, Any], db: Session) -> Any:
    alert_id = payload.get("alert_id") or payload.get("policy_id") or payload.get("id")
    if not isinstance(alert_id, str) or not alert_id.strip():
        raise ValueError("alert_id/policy_id is required")
    return insurance_service.dismiss_alert(db, alert_id=alert_id)


async def _insurance_notify(payload: dict[str, Any], db: Session) -> Any:
    alert_id = payload.get("alert_id") or payload.get("policy_id") or payload.get("id")
    if not isinstance(alert_id, str) or not alert_id.strip():
        raise ValueError("alert_id/policy_id is required")
    return await insurance_service.notify_alert(
        db,
        alert_id=alert_id,
        custom_message=payload.get("message") if isinstance(payload.get("message"), str) else None,
    )


async def _tasks_list(payload: dict[str, Any], db: Session) -> Any:
    del db
    return task_service.list_tasks(
        include_completed=_as_bool(payload.get("include_completed"), True),
        limit=_as_int(payload.get("limit"), 100),
    )


async def _tasks_create(payload: dict[str, Any], db: Session) -> Any:
    del db
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title is required")
    priority = payload.get("priority")
    return task_service.create_task(
        title=title.strip(),
        description=str(payload.get("description") or ""),
        category=str(payload.get("category") or payload.get("project_name") or "Γενικά"),
        priority=str(priority) if priority in {"high", "medium", "low"} else "medium",
        due_date=payload.get("due_date") if isinstance(payload.get("due_date"), str) else None,
        deadline=payload.get("deadline") if isinstance(payload.get("deadline"), str) else None,
    )


async def _tasks_complete(payload: dict[str, Any], db: Session) -> Any:
    del db
    task_id = payload.get("task_id") or payload.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id is required")
    return task_service.complete_task(task_id=task_id, completed=_as_bool(payload.get("completed"), True))


async def _documents_list(payload: dict[str, Any], db: Session) -> Any:
    return document_service.list_documents(db, limit=_as_int(payload.get("limit"), 50))


async def _calendar_delete(payload: dict[str, Any], db: Session) -> Any:
    del db
    event_id = payload.get("event_id") or payload.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id is required")
    await calendar_service.delete_event(event_id=event_id)
    return {"message": "Event deleted successfully"}


async def _activity_list(payload: dict[str, Any], db: Session) -> Any:
    from app.models.activity_log import ActivityLog
    limit = _as_int(payload.get("limit"), 50)
    return db.query(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(limit).all()


TOOLS: dict[str, ToolHandler] = {
    "email.list": _email_list,
    "email.needs_reply": _email_needs_reply,
    "email.sync": _email_sync,
    "email.reply": _email_reply,
    "email.send": _email_send,
    "messaging.send": _messaging_send,
    "calendar.list": _calendar_list,
    "calendar.create": _calendar_create,
    "calendar.delete": _calendar_delete,
    "insurance.alerts": _insurance_alerts,
    "insurance.scan": _insurance_scan,
    "insurance.batch_sms": _insurance_batch_sms,
    "insurance.approve": _insurance_approve,
    "insurance.dismiss": _insurance_dismiss,
    "insurance.notify": _insurance_notify,
    "tasks.list": _tasks_list,
    "tasks.create": _tasks_create,
    "tasks.complete": _tasks_complete,
    "documents.list": _documents_list,
    "activity.list": _activity_list,
    "activity": _activity_list,
}


async def call_tool(action: str, payload: dict[str, Any], db: Session) -> dict[str, Any]:
    logger.info("AI Tool Layer: Executing action '%s'", action)

    handler = TOOLS.get(action)
    if handler is None:
        logger.warning("AI Tool Layer: Unknown action '%s'", action)
        return {"status": "error", "message": f"Action {action} not supported by tool layer"}

    try:
        result = await handler(payload, db)
        return {"status": "success", "data": result}
    except Exception as exc:
        logger.error("AI Tool Layer Error on %s: %s", action, exc)
        return {"status": "error", "message": str(exc)}
