from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from app.schemas.task import TaskCreate, TaskResponse, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])

_TASKS: dict[str, dict[str, Any]] = {}
_TASKS_LOCK = Lock()


def _normalize_task(task: dict[str, Any]) -> dict[str, Any]:
    category = task.get("category") or task.get("project_name") or "Γενικά"
    due_date = task.get("due_date") or task.get("deadline")

    normalized = {
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
    return normalized


@router.get("/", summary="Λίστα εργασιών")
def list_tasks(
    include_completed: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    with _TASKS_LOCK:
        values = list(_TASKS.values())

    rows = [_normalize_task(v) for v in values]
    if not include_completed:
        rows = [row for row in rows if not row["completed"]]

    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return rows[:limit]


@router.post("/", summary="Δημιουργία εργασίας", status_code=201, response_model=TaskResponse)
def create_task(body: TaskCreate) -> TaskResponse:
    now = datetime.now(timezone.utc).isoformat()
    task_id = str(uuid4())

    row = {
        "id": task_id,
        "title": body.title,
        "description": body.description,
        "category": body.category,
        "project_name": body.project_name,
        "priority": body.priority,
        "due_date": body.due_date,
        "deadline": body.deadline,
        "completed": False,
        "created_at": now,
    }

    normalized = _normalize_task(row)

    with _TASKS_LOCK:
        _TASKS[task_id] = normalized

    return TaskResponse(**normalized)


@router.post("/{task_id}/complete", summary="Ολοκλήρωση εργασίας", response_model=TaskResponse)
def complete_task(task_id: str, body: TaskUpdate | None = None) -> TaskResponse:
    with _TASKS_LOCK:
        row = _TASKS.get(task_id)
        if not row:
            raise HTTPException(status_code=404, detail="Task δεν βρέθηκε")

        desired = True if body is None else bool(body.completed)
        row["completed"] = desired
        _TASKS[task_id] = _normalize_task(row)
        updated = _TASKS[task_id]

    return TaskResponse(**updated)


@router.get("/{task_id}", summary="Λεπτομέρειες εργασίας", response_model=TaskResponse)
def get_task(task_id: str) -> TaskResponse:
    with _TASKS_LOCK:
        row = _TASKS.get(task_id)

    if not row:
        raise HTTPException(status_code=404, detail="Task δεν βρέθηκε")

    return TaskResponse(**_normalize_task(row))


@router.get("/ping")
def tasks_ping() -> dict[str, Any]:
    return {"tasks": "ok"}
