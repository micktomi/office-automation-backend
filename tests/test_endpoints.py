import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from datetime import date, timedelta, datetime, timezone

from app.main import app
from app.models.database import Base, get_db
from app.models.client import Client
from app.models.email_message import SyncedEmail
from app.models.policy import Policy

# In-memory SQLite for testing
SQLALCHEMY_DATABASE_URL = "sqlite://"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

def test_daily_summary_empty():
    response = client.get("/reports/daily-summary")
    assert response.status_code == 200
    data = response.json()
    assert data["expiring_7_days"] == 0
    assert data["expired"] == 0

def test_daily_summary_with_data():
    db = TestingSessionLocal()
    today = date.today()
    
    # 1. Expiring in 3 days (should be in expiring_7_days)
    p1 = Policy(
        client_name="Test Client 1",
        email="6900000001",
        expiry_date=today + timedelta(days=3),
        status="active"
    )
    # 2. Expired (should be in expired)
    p2 = Policy(
        client_name="Test Client 2",
        email="6900000002",
        expiry_date=today - timedelta(days=2),
        status="active"
    )
    # 3. Renewed (should not count)
    p3 = Policy(
        client_name="Test Client 3",
        email="6900000003",
        expiry_date=today + timedelta(days=1),
        status="renewed"
    )
    
    db.add_all([p1, p2, p3])
    db.commit()
    
    response = client.get("/reports/daily-summary")
    assert response.status_code == 200
    data = response.json()
    assert data["expiring_7_days"] == 1
    assert data["expired"] == 1

def test_batch_sms_logic():
    db = TestingSessionLocal()
    today = date.today()
    target_date = today + timedelta(days=10)
    
    # Policy expiring exactly in 10 days
    p1 = Policy(
        client_name="SMS Target",
        email="6971234567",
        expiry_date=target_date,
        status="active"
    )
    db.add(p1)
    db.commit()
    
    # Call batch SMS with days=10
    response = client.post("/insurance/batch-sms-reminders?days=10")
    assert response.status_code == 200
    data = response.json()
    
    # Since we can't easily mock the external SMS gateway in this simple test without more setup,
    # we check if it found the policy.
    assert data["total_found"] == 1
    # 'sent' will depend on whether the provider is configured, but we check if it tried.

def test_dashboard_summary_counts():
    db = TestingSessionLocal()
    today = date.today()

    expiring_pending = Policy(
        client_name="Expiring Pending",
        email="pending@example.com",
        expiry_date=today + timedelta(days=3),
        status="active",
    )
    expiring_not_sms_pending = Policy(
        client_name="Expiring Sent",
        email="sent@example.com",
        expiry_date=today + timedelta(days=5),
        status="active",
        reminder_attempts=1,
    )
    expired_policy = Policy(
        client_name="Expired Policy",
        email="expired@example.com",
        expiry_date=today - timedelta(days=1),
        status="active",
    )
    email_pending = Policy(
        client_name="Email Pending",
        email="email@example.com",
        expiry_date=today + timedelta(days=30),
        status="active",
    )

    db.add_all([expiring_pending, expiring_not_sms_pending, expired_policy, email_pending])
    db.commit()

    response = client.get("/dashboard/summary")
    assert response.status_code == 200
    assert "no-store" in response.headers.get("cache-control", "").lower()

    data = response.json()
    assert data == {
        "expiring_soon": 2,
        "expired": 1,
        "emails_pending": 2,
        "sms_pending": 1,
    }


def test_dashboard_summary_ignores_notified_policies():
    db = TestingSessionLocal()
    today = date.today()

    notified_policy = Policy(
        client_name="Notified Client",
        email="notified@example.com",
        expiry_date=today + timedelta(days=4),
        status="notified",
        reminder_attempts=1,
    )

    db.add(notified_policy)
    db.commit()

    response = client.get("/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    assert data == {
        "expiring_soon": 0,
        "expired": 0,
        "emails_pending": 0,
        "sms_pending": 0,
    }

def test_dashboard_policy_endpoints(monkeypatch):
    db = TestingSessionLocal()
    today = date.today()

    expiring_soon = Policy(
        client_name="Soon Client",
        email="soon@example.com",
        expiry_date=today + timedelta(days=5),
        status="active",
    )
    expired = Policy(
        client_name="Expired Client",
        email="expired@example.com",
        expiry_date=today - timedelta(days=1),
        status="active",
    )
    outside_window = Policy(
        client_name="Later Client",
        email="later@example.com",
        expiry_date=today + timedelta(days=20),
        status="active",
    )
    pending_sms = Policy(
        client_name="SMS Client",
        email="sms@example.com",
        expiry_date=today + timedelta(days=4),
        status="active",
    )
    outside_filter = Policy(
        client_name="Outside Filter",
        email="outside@example.com",
        expiry_date=today + timedelta(days=16),
        status="active",
    )
    archived_email = Policy(
        client_name="Archived Client",
        email="archived@example.com",
        expiry_date=today + timedelta(days=3),
        status="archived",
    )

    db.add_all([expiring_soon, expired, outside_window, pending_sms, outside_filter, archived_email])
    db.commit()

    expiring_response = client.get("/policies/expiring-soon")
    assert expiring_response.status_code == 200
    expiring_data = expiring_response.json()
    assert len(expiring_data) == 2
    assert {row["client_name"] for row in expiring_data} == {"Soon Client", "SMS Client"}
    assert all(row["days_until_expiry"] <= 15 for row in expiring_data)
    assert all(row["days_until_expiry"] >= 0 for row in expiring_data)

    expired_response = client.get("/policies/expired")
    assert expired_response.status_code == 200
    expired_data = expired_response.json()
    assert len(expired_data) == 1
    assert expired_data[0]["client_name"] == "Expired Client"

    pending_emails_response = client.get("/emails/pending")
    assert pending_emails_response.status_code == 200
    pending_emails = pending_emails_response.json()
    assert {row["client_name"] for row in pending_emails} >= {
        "Soon Client",
        "Expired Client",
        "Later Client",
        "SMS Client",
    }
    assert all(row["client_name"] != "Archived Client" for row in pending_emails)

    pending_reminders_response = client.get("/reminders/pending?days=15")
    assert pending_reminders_response.status_code == 200
    pending_reminders = pending_reminders_response.json()
    assert len(pending_reminders) == 2
    assert {row["client_name"] for row in pending_reminders} == {"Soon Client", "SMS Client"}


def test_client_and_alert_policy_lists_share_expiring_window():
    db = TestingSessionLocal()
    today = date.today()

    client_row = Client(name="Window Client", email="window@example.com")
    db.add(client_row)
    db.flush()

    within_window = Policy(
        client_id=client_row.id,
        client_name="Window Client",
        email="window@example.com",
        policy_number="W-001",
        insurer="Insurer A",
        expiry_date=today + timedelta(days=15),
        status="active",
    )
    outside_window = Policy(
        client_id=client_row.id,
        client_name="Window Client",
        email="window@example.com",
        policy_number="W-002",
        insurer="Insurer B",
        expiry_date=today + timedelta(days=18),
        status="active",
    )

    db.add_all([within_window, outside_window])
    db.commit()

    client_policies_response = client.get(f"/clients/{client_row.id}/policies")
    assert client_policies_response.status_code == 200
    client_policies = client_policies_response.json()
    assert len(client_policies) == 1
    assert client_policies[0]["policy_number"] == "W-001"
    assert client_policies[0]["days_left"] == 15

    alerts_response = client.get("/insurance/alerts")
    assert alerts_response.status_code == 200
    alerts = alerts_response.json()
    assert len(alerts) == 1
    assert alerts[0]["policy_number"] == "W-001"
    assert alerts[0]["days_until_expiry"] == 15

def test_email_list_hides_noise_by_default(monkeypatch):
    db = TestingSessionLocal()
    db.add_all([
        SyncedEmail(
            id="1",
            gmail_id="1",
            subject="Mail delivery failed",
            sender="mailer-daemon@example.com",
            sender_email="mailer-daemon@example.com",
            body="Delivery failure",
            classification="irrelevant",
            classification_label="Άκυρο",
            priority="medium",
            status="inbox",
            unread=True,
            received_at=datetime(2026, 3, 28, 10, 0, tzinfo=timezone.utc),
        ),
        SyncedEmail(
            id="2",
            gmail_id="2",
            subject="Ανανέωση ασφαλιστηρίου",
            sender="client@example.com",
            sender_email="client@example.com",
            body="Το συμβόλαιο λήγει σύντομα",
            classification="important",
            classification_label="Ασφαλιστήριο",
            priority="high",
            status="inbox",
            unread=True,
            received_at=datetime(2026, 3, 28, 11, 0, tzinfo=timezone.utc),
        ),
    ])
    db.commit()
    db.close()

    default_response = client.get("/email/")
    assert default_response.status_code == 200
    default_data = default_response.json()
    assert len(default_data) == 1
    assert default_data[0]["classification"] == "important"
    assert default_data[0]["classification_label"] == "Ασφαλιστήριο"

    show_all_response = client.get("/email/?include_noise=true")
    assert show_all_response.status_code == 200
    show_all_data = show_all_response.json()
    assert len(show_all_data) == 2
    assert {row["classification"] for row in show_all_data} == {"irrelevant", "important"}

def test_agent_ping():
    response = client.get("/agent/ping")
    assert response.status_code == 200
    assert response.json()["agent"] == "ok"
