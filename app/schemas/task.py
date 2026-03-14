from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


TaskPriority = Literal["high", "medium", "low"]


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    category: str | None = None
    project_name: str | None = None
    priority: TaskPriority = "medium"
    due_date: str | None = None
    deadline: str | None = None

    @model_validator(mode="after")
    def harmonize_fields(self) -> "TaskCreate":
        if not self.category and self.project_name:
            self.category = self.project_name
        if not self.project_name and self.category:
            self.project_name = self.category
        if not self.category and not self.project_name:
            self.category = "Γενικά"
            self.project_name = "Γενικά"
        if not self.due_date and self.deadline:
            self.due_date = self.deadline
        if not self.deadline and self.due_date:
            self.deadline = self.due_date
        return self


class TaskResponse(BaseModel):
    id: str
    title: str
    description: str | None = None
    category: str = "Γενικά"
    project_name: str = "Γενικά"
    priority: TaskPriority = "medium"
    due_date: str | None = None
    deadline: str | None = None
    completed: bool = False
    created_at: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskResponse":
        return cls(**{k: d.get(k) for k in cls.model_fields})


class TaskComplete(BaseModel):
    task_id: str
    completed: bool = True


class TaskUpdate(BaseModel):
    completed: bool = Field(default=True)
