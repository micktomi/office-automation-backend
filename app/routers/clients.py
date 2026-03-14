from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.database import get_db
from app.models.policy import Policy

router = APIRouter(prefix="/clients", tags=["clients"])

class ClientResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: str | None = None
    afm: str | None = None

class CreateClientRequest(BaseModel):
    name: str
    email: str
    phone: str | None = None
    afm: str | None = None

@router.get("", response_model=list[ClientResponse])
def list_clients(db: Session = Depends(get_db)):
    """
    Returns unique clients based on their email from the Policy table.
    """
    # Group by email to get unique clients
    unique_clients = (
        db.query(
            func.min(Policy.id).label("id"),
            Policy.client_name.label("name"),
            Policy.email.label("email")
        )
        .group_by(Policy.email)
        .all()
    )
    
    return [
        ClientResponse(
            id=str(c.id),
            name=c.name,
            email=c.email,
            phone="6900000000", # Placeholder for now
            afm="123456789"    # Placeholder for now
        ) for c in unique_clients
    ]

@router.post("", response_model=ClientResponse)
def create_client(body: CreateClientRequest, db: Session = Depends(get_db)):
    """
    Creates a new client by adding a placeholder policy.
    """
    # Check if client already exists
    exists = db.query(Policy).filter(Policy.email == body.email).first()
    if exists:
        raise HTTPException(status_code=400, detail="Client with this email already exists")
    
    from datetime import datetime, timedelta
    
    new_policy = Policy(
        client_name=body.name,
        email=body.email,
        expiry_date=(datetime.now() + timedelta(days=365)).date(),
        status="active"
    )
    db.add(new_policy)
    db.commit()
    db.refresh(new_policy)
    
    return ClientResponse(
        id=str(new_policy.id),
        name=new_policy.client_name,
        email=new_policy.email,
        phone=body.phone,
        afm=body.afm
    )

@router.get("/{client_id}", response_model=ClientResponse)
def get_client(client_id: str, db: Session = Depends(get_db)):
    policy_id = int(client_id.replace("policy-", ""))
    policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Client not found")
        
    return ClientResponse(
        id=str(policy.id),
        name=policy.client_name,
        email=policy.email,
        phone="6900000000",
        afm="123456789"
    )
