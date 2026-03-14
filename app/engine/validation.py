from __future__ import annotations

import re
from datetime import date


REQUIRED_POLICY_FIELDS = ("client_name", "email", "expiry_date")


def missing_required_columns(columns: list[str]) -> list[str]:
    column_set = set(columns)
    return [field for field in REQUIRED_POLICY_FIELDS if field not in column_set]


def validate_policy_payload(payload: dict) -> list[str]:
    errors: list[str] = []

    client_name = str(payload.get("client_name") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    expiry_date = payload.get("expiry_date")

    if not client_name:
        errors.append("client_name is required")

    if not email:
        errors.append("email is required")
    elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        errors.append("email is invalid")

    if expiry_date is None:
        errors.append("expiry_date is required")
    elif not isinstance(expiry_date, date):
        errors.append("expiry_date must be a date")

    return errors
