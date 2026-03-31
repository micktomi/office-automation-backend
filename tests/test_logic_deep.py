import pytest
import asyncio
from app.services.task_service import TaskService
from app.services.insurance_service import InsuranceService
from datetime import date, datetime, timezone
from datetime import timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base
from app.models.email_message import SyncedEmail
import app.services.insurance_service as insurance_service_module

def test_task_service_flow():
    ts = TaskService()
    # 1. Create
    task = ts.create_task(title="Τηλέφωνο στον Κώστα", priority="high")
    assert task["title"] == "Τηλέφωνο στον Κώστα"
    assert task["priority"] == "high"
    assert task["completed"] is False
    
    # 2. List
    tasks = ts.list_tasks(include_completed=False)
    assert len(tasks) == 1
    
    # 3. Complete
    task_id = task["id"]
    updated = ts.complete_task(task_id=task_id, completed=True)
    assert updated["completed"] is True
    
    # 4. List again (exclude completed)
    tasks_remaining = ts.list_tasks(include_completed=False)
    assert len(tasks_remaining) == 0

def test_insurance_date_extraction():
    is_service = InsuranceService()
    
    # Test Greek date formats
    text_gr = "Το συμβόλαιο λήγει στις 25/12/2026. Παρακαλώ ανανεώστε."
    extracted_date = is_service._extract_date_from_text(text_gr)
    assert extracted_date == date(2026, 12, 25)
    
    # Test ISO format
    text_iso = "Expiry: 2026-05-20"
    extracted_date_iso = is_service._extract_date_from_text(text_iso)
    assert extracted_date_iso == date(2026, 5, 20)

def test_insurance_policy_number_extraction():
    is_service = InsuranceService()
    
    # Case 1: Simple Greek label
    text = "Αριθμός συμβολαίου: ΑΣ-12345-Β"
    pol_num = is_service._extract_policy_number(text)
    assert pol_num == "ΑΣ-12345-Β"
    
    # Case 2: English label
    text_en = "Your policy no: XYZ-999-000"
    pol_num_en = is_service._extract_policy_number(text_en)
    assert pol_num_en == "XYZ-999-000"

def test_public_insurance_helpers_support_greek_labels():
    is_service = InsuranceService()
    expiry = date.today() + timedelta(days=45)
    text = (
        f"Συμβόλαιο αυτοκινήτου\n"
        f"Πελάτης: Δημήτρης Νικολάου\n"
        f"Έναρξη: 01/01/2026\n"
        f"Λήξη: {expiry.strftime('%d/%m/%Y')}\n"
        f"Αριθμός συμβολαίου: ΑΣ-12345-Β\n"
        "Ανανέωση διαθέσιμη"
    )

    assert is_service.extract_expiry_date(text) == expiry
    assert is_service.extract_policy_number(text) == "ΑΣ-12345-Β"
    assert is_service.extract_policy_holder(text) == "Δημήτρης Νικολάου"

def test_insurance_insurer_guessing():
    is_service = InsuranceService()
    
    # Guess from email domain
    insurer = is_service._guess_insurer("", "info@generali.gr")
    assert insurer == "Generali"
    
    # Guess from sender name
    insurer_name = is_service._guess_insurer("Εθνική Ασφαλιστική", "no-reply@ethniki.gr")
    assert insurer_name == "Εθνική Ασφαλιστική"

def test_scan_uses_ai_only_when_regex_parsing_fails(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    future_expiry = date.today() + timedelta(days=30)
    ai_expiry = date.today() + timedelta(days=40)
    ai_calls = []

    class DummyAIClient:
        def __init__(self, settings):
            pass

        async def extract_insurance_info(self, sender: str, subject: str, body: str):
            ai_calls.append({"sender": sender, "subject": subject, "body": body})
            return {
                "is_insurance": True,
                "policy_holder": "Broker Two",
                "policy_number": "POL-222-XYZ",
                "insurer": "Broker Two",
                "expiry_date": ai_expiry.isoformat(),
                "draft_notification_greek": "Test draft",
            }

    monkeypatch.setattr(insurance_service_module, "AIClient", DummyAIClient)

    db = SessionLocal()
    try:
        db.add_all([
            SyncedEmail(
                id="mail-1",
                gmail_id="mail-1",
                sender='"Broker One" <broker1@example.com>',
                subject="Ανανέωση συμβολαίου",
                body=(
                    f"Έναρξη: 01/01/2026\n"
                    f"Λήξη: {future_expiry.strftime('%d/%m/%Y')}\n"
                    "Αριθμός συμβολαίου: POL-111-ABC"
                ),
                received_at=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
            ),
            SyncedEmail(
                id="mail-2",
                gmail_id="mail-2",
                sender='"Broker Two" <broker2@example.com>',
                subject="Ανανέωση συμβολαίου",
                body="Συμβόλαιο προς ανανέωση. Παρακαλούμε ελέγξτε τα στοιχεία.",
                received_at=datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc),
            ),
            SyncedEmail(
                id="mail-3",
                gmail_id="mail-3",
                sender='"News" <news@example.com>',
                subject="Weekly update",
                body="Απλό ενημερωτικό email χωρίς στοιχεία ασφαλιστηρίου.",
                received_at=datetime(2026, 3, 3, 10, 0, tzinfo=timezone.utc),
            ),
        ])
        db.commit()
        result = asyncio.run(
            InsuranceService().scan_emails_for_insurance(db, limit=10, days=90)
        )
        second_result = asyncio.run(
            InsuranceService().scan_emails_for_insurance(db, limit=10, days=90)
        )
    finally:
        db.close()

    assert result["scanned"] == 3
    assert result["alerts_created"] == 2
    assert second_result["scanned"] == 0
    assert second_result["alerts_created"] == 0
    assert second_result["already_processed"] == 0
    assert len(ai_calls) == 1
    assert ai_calls[0]["sender"] == '"Broker Two" <broker2@example.com>'
