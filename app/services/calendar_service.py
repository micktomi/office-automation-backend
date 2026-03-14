from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.utils.google_client import get_google_service

logger = logging.getLogger(__name__)


class CalendarService:
    """Google Calendar integration with deterministic local fallback."""

    def __init__(self) -> None:
        self._fallback_events: dict[str, dict[str, Any]] = {}

    async def list_events(self, max_results: int = 10) -> list[dict[str, Any]]:
        service = await get_google_service("calendar", "v3")
        if service is None:
            events = sorted(self._fallback_events.values(), key=lambda item: item.get("start", ""))
            return events[:max_results]

        def _list() -> list[dict[str, Any]]:
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            result = service.events().list(
                calendarId="primary",
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
            return result.get("items", [])

        rows = await asyncio.to_thread(_list)
        return [
            {
                "id": row["id"],
                "summary": row.get("summary", "No title"),
                "start": row.get("start", {}).get("dateTime", row.get("start", {}).get("date", "")),
                "end": row.get("end", {}).get("dateTime", row.get("end", {}).get("date", "")),
                "description": row.get("description", ""),
                "location": row.get("location", ""),
            }
            for row in rows
        ]

    async def create_event(
        self,
        *,
        summary: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
        client_name: str | None = None,
        policy_number: str | None = None,
    ) -> dict[str, Any]:
        from app.services.activity_service import log_action

        service = await get_google_service("calendar", "v3")
        if service is None:
            event_id = str(uuid.uuid4())
            event = {
                "id": event_id,
                "summary": summary,
                "start": start_time,
                "end": end_time,
                "description": description,
                "location": location,
            }
            self._fallback_events[event_id] = event
            log_action(
                action_type="Δημιουργία ραντεβού στο ημερολόγιο",
                client_name=client_name or summary,
                policy_number=policy_number,
                channel="calendar",
                status="success",
            )
            return event

        body = {
            "summary": summary,
            "description": description,
            "location": location,
            "start": {"dateTime": start_time, "timeZone": "UTC"},
            "end": {"dateTime": end_time, "timeZone": "UTC"},
        }

        def _create() -> dict[str, Any]:
            return service.events().insert(calendarId="primary", body=body).execute()

        try:
            created = await asyncio.to_thread(_create)
            log_action(
                action_type="Δημιουργία ραντεβού στο ημερολόγιο",
                client_name=client_name or summary,
                policy_number=policy_number,
                channel="calendar",
                status="success",
            )
            return {
                "id": created["id"],
                "summary": created.get("summary", summary),
                "start": created.get("start", {}).get("dateTime", start_time),
                "end": created.get("end", {}).get("dateTime", end_time),
                "description": created.get("description", description),
                "location": created.get("location", location),
            }
        except Exception as exc:
            logger.error("CalendarService Error: %s", exc)
            log_action(
                action_type="Δημιουργία ραντεβού στο ημερολόγιο",
                client_name=client_name or summary,
                policy_number=policy_number,
                channel="calendar",
                status="failed",
            )
            raise

    async def delete_event(self, event_id: str) -> None:
        from app.services.activity_service import log_action

        service = await get_google_service("calendar", "v3")
        if service is None:
            if event_id not in self._fallback_events:
                log_action(
                    action_type="Διαγραφή ραντεβού",
                    client_name=f"Event ID: {event_id}",
                    channel="calendar",
                    status="failed",
                )
                raise ValueError("Event not found")
            self._fallback_events.pop(event_id, None)
            log_action(
                action_type="Διαγραφή ραντεβού",
                client_name=f"Event ID: {event_id}",
                channel="calendar",
                status="success",
            )
            return

        def _delete() -> None:
            service.events().delete(calendarId="primary", eventId=event_id).execute()

        try:
            await asyncio.to_thread(_delete)
            log_action(
                action_type="Διαγραφή ραντεβού",
                client_name=f"Event ID: {event_id}",
                channel="calendar",
                status="success",
            )
        except Exception as exc:
            logger.error("CalendarService Error: %s", exc)
            log_action(
                action_type="Διαγραφή ραντεβού",
                client_name=f"Event ID: {event_id}",
                channel="calendar",
                status="failed",
            )
            raise

# Global instance
calendar_service = CalendarService()
