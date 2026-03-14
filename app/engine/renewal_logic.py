from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.policy import Policy


def validate_policy_invariants(policy: Policy) -> None:
    """
    Core validation function to enforce domain invariants.
    Protects against DB tampering and illegal state combinations.
    """
    if policy.reminder_attempts is None or policy.reminder_attempts < 0:
        policy.reminder_attempts = 0
    elif policy.reminder_attempts > 3:
        policy.reminder_attempts = 3

    today = datetime.now(timezone.utc).date()

    if policy.status not in ("renewed", "archived"):
        if policy.expiry_date < today or policy.reminder_attempts >= 3:
            policy.status = "overdue"


def get_upcoming_policies(db_session: Session, days: int):
    today = datetime.now(timezone.utc).date()
    target_date = today + timedelta(days=days)

    policies = (
        db_session.query(Policy)
        .filter(
            Policy.expiry_date >= today,
            Policy.expiry_date <= target_date,
            Policy.status.in_(["active", "reminder_pending", "reminder_sent"]),
            Policy.reminder_attempts < 3,
        )
        .all()
    )

    valid_policies = []
    for policy in policies:
        validate_policy_invariants(policy)
        if policy.computed_state not in ("renewed", "archived", "overdue"):
            valid_policies.append(policy)

    return valid_policies


def get_overdue_policies(db_session: Session):
    today = datetime.now(timezone.utc).date()

    policies = (
        db_session.query(Policy)
        .filter(
            Policy.status.notin_(["renewed", "archived"]),
            or_(
                Policy.status == "overdue",
                Policy.expiry_date < today,
                Policy.reminder_attempts >= 3,
            ),
        )
        .all()
    )

    for policy in policies:
        validate_policy_invariants(policy)

    return policies


def process_successful_send(policy: Policy) -> None:
    validate_policy_invariants(policy)

    policy.reminder_attempts += 1
    policy.last_reminder_sent_at = datetime.now(timezone.utc)

    if policy.reminder_attempts >= 3:
        policy.status = "overdue"
    else:
        policy.status = "reminder_sent"

    validate_policy_invariants(policy)
