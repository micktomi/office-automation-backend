from __future__ import annotations

from pydantic import BaseModel


class EmailRecord(BaseModel):
    id: str
    gmail_id: str | None = None
    policy_id: int | None = None
    subject: str
    sender: str
    body: str
    classification: str = "probable"
    classification_label: str = "Πελάτης"
    priority: str = "medium"
    status: str = "inbox"
    unread: bool = True
    processed: bool = False
    received_at: str | None = None


class EmailSyncResult(BaseModel):
    processed: int
    skipped: int
    status: str = "ok"


class EmailReplyRequest(BaseModel):
    email_id: str


class EmailReplyResult(BaseModel):
    email_id: str
    reply: str
