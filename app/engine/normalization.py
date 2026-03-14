from __future__ import annotations

import re
from unicodedata import normalize


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    collapsed = " ".join(value.strip().split())
    return normalize("NFKC", collapsed)


def normalize_email(value: str | None) -> str:
    return normalize_text(value).lower()


def normalize_column_name(value: str | None) -> str:
    text = normalize_text(value).lower()
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_policy_row(raw: dict) -> dict:
    return {
        "client_name": normalize_text(raw.get("client_name")),
        "email": normalize_email(raw.get("email")),
        "expiry_date": raw.get("expiry_date"),
    }
