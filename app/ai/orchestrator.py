from __future__ import annotations

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


def _build_routing_context(context: dict[str, Any] | None) -> str | None:
    if not context:
        return None

    parts: list[str] = []

    current_tab = context.get("currentTab")
    if isinstance(current_tab, str) and current_tab.strip():
        parts.append(f"Current UI tab: {current_tab}")

    last_action = context.get("lastActionPerformed")
    if isinstance(last_action, str) and last_action.strip():
        parts.append(f"Last completed action: {last_action}")

    recent_messages = context.get("recentMessages")
    if isinstance(recent_messages, list) and recent_messages:
        rendered_messages: list[str] = []
        for item in recent_messages[-6:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if isinstance(role, str) and isinstance(content, str) and content.strip():
                rendered_messages.append(f"{role}: {content.strip()}")
        if rendered_messages:
            parts.append("Recent conversation:\n" + "\n".join(rendered_messages))

    selected_email = context.get("selectedEmailId")
    if isinstance(selected_email, str) and selected_email.strip():
        parts.append(f"Selected email id: {selected_email}")

    selected_policy = context.get("selectedPolicyId")
    if isinstance(selected_policy, str) and selected_policy.strip():
        parts.append(f"Selected policy id: {selected_policy}")

    selected_client = context.get("selectedClientId")
    if isinstance(selected_client, str) and selected_client.strip():
        parts.append(f"Selected client id: {selected_client}")

    return "\n".join(parts) if parts else None


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
    4. Returns a natural language response, the action, and data.
    """
    logger.info("Orchestrator predicting intent for: '%s'", message)
    
    routing_context = _build_routing_context(context)
    intent_result = await ai_client.route_intent(message, routing_context)
    action = intent_result.get("action", "chat")
    natural_response = intent_result.get("response", "Εντάξει.")
    
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
    
    # If it's just a chat, return AI response
    if mapped_action == "chat":
        chat_reply = await ai_client.chat_response(message)
        return chat_reply, "chat", None
        
    try:
        # Call the Tool Layer Bridge
        # We pass the full user message as a fallback payload
        payload: dict[str, Any] = {"message": message}
        if context:
            payload.update(context)
        
        # Skill extraction
        if mapped_action == "calendar.create":
            event_data = await ai_client.extract_event_info(message)
            if event_data.get("confidence") != "low":
                payload.update(event_data)
                if "start_time" in event_data:
                    payload["start"] = event_data["start_time"]
                if "end_time" in event_data:
                    payload["end"] = event_data["end_time"]
        elif mapped_action == "calendar.delete":
            # If no ID is found, use the one from the context if available
            if not payload.get("id") and context:
                payload["id"] = context.get("selectedPolicyId") or context.get("selectedEmailId")
        elif mapped_action == "tasks.create":
            payload["title"] = message

        result = await call_tool(mapped_action, payload, db)
        
        if result["status"] == "success":
            return natural_response, mapped_action, result["data"]
        else:
            # If tool failed, let AI explain why
            error_msg = result.get("message", "Άγνωστο σφάλμα")
            chat_reply = await ai_client.chat_response(message, action_result=f"Σφάλμα: {error_msg}")
            return chat_reply, mapped_action, None
            
    except Exception as e:
        logger.error("Orchestrator Error: %s", e)
        error_reply = await ai_client.chat_response(message, action_result=f"Η ενέργεια απέτυχε: {e}")
        return error_reply, None, None
