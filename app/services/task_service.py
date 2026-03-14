from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4


class TaskService:
    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    @staticmethod
    def _normalize_task(task: dict[str, Any]) -> dict[str, Any]:
        category = task.get("category") or task.get("project_name") or "Γενικά"
        due_date = task.get("due_date") or task.get("deadline")

        return {
            "id": task["id"],
            "title": task.get("title", ""),
            "description": task.get("description", ""),
            "category": category,
            "project_name": category,
            "priority": task.get("priority", "medium"),
            "due_date": due_date,
            "deadline": due_date,
            "completed": bool(task.get("completed", False)),
            "created_at": task.get("created_at"),
        }

    def list_tasks(self, *, include_completed: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._tasks.values())

        rows = [self._normalize_task(value) for value in values]
        if not include_completed:
            rows = [row for row in rows if not row["completed"]]

        rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
        return rows[:limit]

    def create_task(
        self,
        *,
        title: str,
        description: str = "",
        category: str = "Γενικά",
        priority: str = "medium",
        due_date: str | None = None,
        deadline: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        task_id = str(uuid4())
        row = self._normalize_task(
            {
                "id": task_id,
                "title": title,
                "description": description,
                "category": category,
                "project_name": category,
                "priority": priority,
                "due_date": due_date,
                "deadline": deadline,
                "completed": False,
                "created_at": now,
            }
        )

        with self._lock:
            self._tasks[task_id] = row

        return row

    def complete_task(self, *, task_id: str, completed: bool = True) -> dict[str, Any]:
        with self._lock:
            row = self._tasks.get(task_id)
            if not row:
                raise ValueError("Task δεν βρέθηκε")

            row["completed"] = completed
            normalized = self._normalize_task(row)
            self._tasks[task_id] = normalized
            return normalized


task_service = TaskService()
