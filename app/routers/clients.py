from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.policy import Policy
from app.models.client import Client
from app.models.reminder_log import ReminderLog
from app.engine.renewal_logic import DEFAULT_EXPIRING_POLICIES_DAYS, get_expiring_policies

router = APIRouter(prefix="/clients", tags=["clients"])

class ClientResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: str | None = None
    afm: str | None = None
    created_at: str | None = None

class CreateClientRequest(BaseModel):
    name: str
    email: str
    phone: str | None = None
    afm: str | None = None

@router.get("", response_model=list[ClientResponse])
def list_clients(db: Session = Depends(get_db)):
    """
    Returns clients from the Client table.
    """
    clients = db.query(Client).order_by(Client.name).all()
    
    return [
        ClientResponse(
            id=str(c.id),
            name=c.name,
            email=c.email or "",
            phone=c.phone or "6900000000",
            afm=c.address or "123456789", # Reusing address as AFM for now as per previous placeholder pattern
            created_at=c.created_at.isoformat() if c.created_at else None
        ) for c in clients
    ]

@router.post("", response_model=ClientResponse)
def create_client(body: CreateClientRequest, db: Session = Depends(get_db)):
    """
    Creates a new client.
    """
    # Check if client already exists
    exists = db.query(Client).filter(Client.email == body.email).first()
    if exists:
        raise HTTPException(status_code=400, detail="Client with this email already exists")
    
    new_client = Client(
        name=body.name,
        email=body.email,
        phone=body.phone,
        address=body.afm # Mapping AFM to address field in Client model
    )
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    
    return ClientResponse(
        id=str(new_client.id),
        name=new_client.name,
        email=new_client.email or "",
        phone=new_client.phone or "",
        afm=new_client.address or "",
        created_at=new_client.created_at.isoformat() if new_client.created_at else None
    )

@router.get("/{client_id}", response_model=ClientResponse)
def get_client(client_id: str, db: Session = Depends(get_db)):
    try:
        cid = int(client_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid client id format")

    client = db.query(Client).filter(Client.id == cid).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
        
    return ClientResponse(
        id=str(client.id),
        name=client.name,
        email=client.email or "",
        phone=client.phone or "6900000000",
        afm=client.address or "123456789",
        created_at=client.created_at.isoformat() if client.created_at else None
    )

@router.delete("/{client_id}")
def delete_client(client_id: str, db: Session = Depends(get_db)):
    try:
        cid = int(client_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid client id format")

    client = db.query(Client).filter(Client.id == cid).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    db.delete(client)
    db.commit()
    return {"status": "ok", "message": "Client and related data deleted"}

@router.get("/{client_id}/policies")
def get_client_policies(
    client_id: str,
    days: int = Query(default=DEFAULT_EXPIRING_POLICIES_DAYS, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    try:
        cid = int(client_id)
    except ValueError:
        return []

    policies = get_expiring_policies(db, days=days, client_id=cid)
    today = datetime.now(timezone.utc).date()

    return [
        {
            "id": p.id,
            "policy_number": p.policy_number,
            "insurer": p.insurer,
            "expiry_date": p.expiry_date.isoformat(),
            "status": p.status,
            "days_left": (p.expiry_date - today).days if p.expiry_date else 0,
        } for p in policies
    ]

@router.get("/{client_id}/emails")
def get_client_emails(client_id: str, db: Session = Depends(get_db)):
    # This would normally query a messages table or fetch from Gmail for this client's email
    # For now, we'll return messages where the policy belongs to this client
    try:
        cid = int(client_id)
    except ValueError:
        return []
        
    client = db.query(Client).filter(Client.id == cid).first()
    if not client or not client.email:
        return []
        
    # Mocking client email history from reminder logs for now
    logs = db.query(ReminderLog).join(Policy).filter(Policy.client_id == cid).order_by(ReminderLog.sent_at.desc()).all()
    
    return [
        {
            "id": log.id,
            "subject": f"Υπενθύμιση συμβολαίου #{log.policy_id}",
            "sent_at": log.sent_at.isoformat() if log.sent_at else None,
            "status": log.status
        } for log in logs
    ]

@router.get("/{client_id}/tasks")
def get_client_tasks(client_id: str):
    # Tasks are currently in-memory in tasks.py, we need a better way to link them
    # For now returning empty list or simple mock
    return []
