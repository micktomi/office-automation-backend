from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.engine.renewal_logic import (
    get_overdue_policies,
    get_upcoming_policies,
    validate_policy_invariants,
)
from app.models.policy import Policy


AUTO_SEND = True


class CycleResult:
    def __init__(self):
        self.ran_at: datetime = datetime.now(timezone.utc)
        self.upcoming_count: int = 0
        self.overdue_count: int = 0
        self.eligible_for_send: list[dict] = []
        self.skipped_overdue: list[dict] = []
        self.skipped_max_attempts: list[dict] = []
        self.errors: list[str] = []

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def total_skipped(self) -> int:
        return len(self.skipped_overdue) + len(self.skipped_max_attempts)

    def to_dict(self) -> dict:
        return {
            "ran_at": self.ran_at.isoformat(),
            "upcoming_count": self.upcoming_count,
            "overdue_count": self.overdue_count,
            "eligible_for_send": self.eligible_for_send,
            "skipped_overdue": self.skipped_overdue,
            "skipped_max_attempts": self.skipped_max_attempts,
            "errors": self.errors,
            "auto_send_active": AUTO_SEND,
        }


def _policy_snapshot(policy: Policy) -> dict:
    return {
        "id": policy.id,
        "client_name": policy.client_name,
        "email": policy.email,
        "expiry_date": policy.expiry_date.isoformat(),
        "status": policy.status,
        "computed_state": policy.computed_state,
        "reminder_attempts": policy.reminder_attempts,
        "last_reminder_sent_at": (
            policy.last_reminder_sent_at.isoformat()
            if policy.last_reminder_sent_at
            else None
        ),
    }


def run_reminder_cycle(db_session: Session, days_ahead: int = 30) -> CycleResult:
    result = CycleResult()

    try:
        upcoming = get_upcoming_policies(db_session, days=days_ahead)
        result.upcoming_count = len(upcoming)

        for policy in upcoming:
            validate_policy_invariants(policy)
            computed = policy.computed_state

            if computed in ("overdue", "renewed", "archived"):
                result.skipped_overdue.append(_policy_snapshot(policy))
                continue

            if policy.reminder_attempts >= 3:
                result.skipped_max_attempts.append(_policy_snapshot(policy))
                continue

            result.eligible_for_send.append(_policy_snapshot(policy))

        overdue = get_overdue_policies(db_session)
        result.overdue_count = len(overdue)

        for policy in overdue:
            validate_policy_invariants(policy)

    except Exception as exc:
        result.errors.append(f"Cycle error: {str(exc)}")

    return result
