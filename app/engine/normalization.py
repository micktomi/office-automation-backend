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
import re
from datetime import datetime

def extract_expiry_from_text(text: str | None):
    if not text:
        return None

    text = normalize_text(text)

    # βρίσκει ημερομηνίες τύπου 01/02/2026
    matches = re.findall(r"\d{2}/\d{2}/\d{4}", text)

    if not matches:
        return None

    # παίρνουμε την τελευταία ημερομηνία (συνήθως είναι η λήξη)
    expiry = matches[-1]

    try:
        return datetime.strptime(expiry, "%d/%m/%Y").date()
    except:
        return None