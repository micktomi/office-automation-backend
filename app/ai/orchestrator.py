from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.ai.client import AIClient
from app.ai.tools import call_tool
from app.config import get_settings

logger = logging.getLogger(__name__)

# Initialize a global AI client instance
settings = get_settings()
ai_client = AIClient(settings)


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    return value


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _build_tool_payload(
    action: str,
    message: str,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message}
    if context:
        payload.update(context)

    if action == "tasks.create":
        payload.setdefault("title", message.strip())
    elif action == "calendar.create":
        payload.setdefault("summary", message.strip())
    elif action == "email.reply":
        email_id = _first_string(
            payload.get("email_id"),
            payload.get("id"),
            payload.get("selectedEmailId"),
        )
        if email_id:
            payload["email_id"] = email_id
    elif action == "tasks.complete":
        task_id = _first_string(
            payload.get("task_id"),
            payload.get("id"),
            payload.get("selectedTaskId"),
        )
        if task_id:
            payload["task_id"] = task_id
    elif action == "calendar.delete":
        event_id = _first_string(
            payload.get("event_id"),
            payload.get("id"),
            payload.get("selectedCalendarEventId"),
            payload.get("selectedEventId"),
        )
        if event_id:
            payload["event_id"] = event_id
    elif action in {"insurance.approve", "insurance.dismiss", "insurance.notify"}:
        alert_id = _first_string(
            payload.get("alert_id"),
            payload.get("policy_id"),
            payload.get("id"),
            payload.get("selectedPolicyId"),
        )
        if alert_id:
            payload["alert_id"] = alert_id

    return payload


def _format_tool_response(action: str, data: Any) -> str:
    if data is None:
        return "Η ενέργεια ολοκληρώθηκε."

    if isinstance(data, str):
        text = data.strip()
        return text or "Η ενέργεια ολοκληρώθηκε."

    if isinstance(data, dict):
        message = _first_string(data.get("message"), data.get("detail"), data.get("summary"))
        if message:
            return message

        if action == "email.list" and isinstance(data.get("items"), list):
            return f"Βρέθηκαν {len(data['items'])} email."

        if action == "calendar.list" and isinstance(data.get("items"), list):
            return f"Βρέθηκαν {len(data['items'])} ραντεβού."

        if action == "tasks.list" and isinstance(data.get("items"), list):
            return f"Βρέθηκαν {len(data['items'])} tasks."

        if "count" in data and isinstance(data["count"], (int, float)):
            return f"Βρέθηκαν {data['count']} αποτελέσματα."

        return json.dumps(data, ensure_ascii=False)

    if isinstance(data, list):
        if action == "email.list":
            return f"Βρέθηκαν {len(data)} email."
        if action == "calendar.list":
            return f"Βρέθηκαν {len(data)} ραντεβού."
        if action == "tasks.list":
            return f"Βρέθηκαν {len(data)} tasks."
        return f"Βρέθηκαν {len(data)} αποτελέσματα."

    return str(data)


def _build_routing_context(context: dict[str, Any] | None, db: Session) -> str | None:
    if not context:
        return None

    from app.models.email_message import SyncedEmail
    from app.models.policy import Policy
    from app.services.task_service import task_service

    parts: list[str] = []

    current_tab = context.get("currentTab")
    if isinstance(current_tab, str) and current_tab.strip():
        parts.append(f"Current UI tab: {current_tab}")

    selected_email_id = context.get("selectedEmailId")
    if selected_email_id:
        email = db.query(SyncedEmail).filter(SyncedEmail.id == selected_email_id).first()
        if email:
            parts.append(f"Επιλεγμένο email (περιεχόμενο): Από {email.sender}, Θέμα '{email.subject}', Κείμενο: {email.body[:300]}")

    selected_policy_id = context.get("selectedPolicyId")
    if selected_policy_id:
        policy = db.query(Policy).filter(Policy.id == selected_policy_id).first()
        if policy:
            parts.append(f"Επιλεγμένο συμβόλαιο: Πελάτης {policy.client_name}, Λήξη {policy.expiry_date}, Ποσό/Αριθμός {policy.policy_number}")

    selected_task_id = context.get("selectedTaskId")
    if selected_task_id:
        try:
            task = task_service.get_task(selected_task_id)
            if task:
                parts.append(f"Επιλεγμένη εργασία: {task.title}, Προτεραιότητα {task.priority}")
        except: pass

    return "\n".join(parts) if parts else None


def _get_data_context(db: Session) -> str:
    """Fetch recent data to provide context to the AI."""
    from app.models.email_message import SyncedEmail
    from app.services.task_service import task_service

    parts = []
    
    # Recent Emails
    emails = db.query(SyncedEmail).order_by(SyncedEmail.received_at.desc()).limit(3).all()
    if emails:
        parts.append("ΤΕΛΕΥΤΑΙΑ EMAILS:")
        for e in emails:
            parts.append(f"- Από: {e.sender}, Θέμα: {e.subject}, Περίληψη: {e.body[:100]}...")

    # Recent Tasks
    tasks = task_service.list_tasks(limit=5)
    if tasks:
        parts.append("\nΤΡΕΧΟΝΤΑ TASKS:")
        for t in tasks:
            parts.append(f"- {t.title} ({t.priority}) - status: {'completed' if t.completed else 'pending'}")

    return "\n".join(parts)


async def handle_chat_message(
    message: str,
    db: Session,
    context: dict[str, Any] | None = None,
) -> tuple[str, str | None, Any | None]:
    """
    Main orchestrator entrypoint.
    1. Uses AIClient to detect intent.
    2. Maps action string to a deterministic skill.
    3. Calls the Tool Layer (app.ai.tools) to execute.
    4. Builds the final response from tool data only.
    """
    logger.info("Orchestrator predicting intent for: '%s'", message)

    routing_context = _build_routing_context(context, db)
    intent_result = await ai_client.route_intent(message, routing_context)
    action = intent_result.get("action", "chat")
    if not isinstance(action, str) or not action.strip():
        action = "chat"
    else:
        action = action.strip()

    # Action mapping between AI prompt names and tool actions
    action_map = {
        "list_emails": "email.list",
        "sync_inbox": "email.sync",
        "generate_reply": "email.reply",
        "list_needs_reply": "email.needs_reply",
        "send_message": "messaging.send",
        "list_calendar": "calendar.list",
        "create_calendar_event": "calendar.create",
        "delete_calendar_event": "calendar.delete",
        "list_insurance_alerts": "insurance.alerts",
        "scan_insurance": "insurance.scan",
        "batch_sms_reminders": "insurance.batch_sms",
        "list_documents": "documents.list",
        "list_tasks": "tasks.list",
        "create_task": "tasks.create",
        "complete_task": "tasks.complete",
        "help": "chat",
    }

    mapped_action = action_map.get(action, action)

    # Chat stays AI-driven. Every other action is tool-driven first.
    if mapped_action == "chat":
        data_context = _get_data_context(db)
        chat_reply = await ai_client.chat_response(message, context_data=data_context)
        return chat_reply, "chat", None

    try:
        payload = _build_tool_payload(mapped_action, message, context)
        result = await call_tool(mapped_action, payload, db)

        if result["status"] == "success":
            data = _to_jsonable(result.get("data"))
            response_text = _format_tool_response(mapped_action, data)
            return response_text, mapped_action, data

        # If the tool fails, AI may only explain the error.
        error_msg = result.get("message", "Άγνωστο σφάλμα")
        chat_reply = await ai_client.chat_response(message, action_result=f"Σφάλμα: {error_msg}", context_data=_get_data_context(db))
        return chat_reply, mapped_action, None

    except Exception as e:
        logger.error("Orchestrator Error: %s", e)
        error_reply = await ai_client.chat_response(message, action_result=f"Η ενέργεια απέτυχε: {e}", context_data=_get_data_context(db))
        return error_reply, mapped_action, None
