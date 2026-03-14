from __future__ import annotations

from difflib import get_close_matches
from io import BytesIO

import pandas as pd

from app.engine.normalization import normalize_column_name, normalize_policy_row
from app.engine.validation import validate_policy_payload


REQUIRED_FIELDS = ["client_name", "email", "expiry_date"]
FIELD_ALIASES: dict[str, list[str]] = {
    "client_name": [
        "client_name",
        "name",
        "client",
        "customer_name",
        "insured",
        "ονομα",
        "ονοματεπωνυμο",
        "πελατης",
        "πελάτης",
        "ασφαλισμενος",
        "ασφαλισμένος",
    ],
    "email": [
        "email",
        "e-mail",
        "mail",
        "email_address",
        "client_email",
        "ηλεκτρονικο ταχυδρομειο",
        "ηλ. ταχυδρομείο",
    ],
    "expiry_date": [
        "expiry_date",
        "expiry",
        "expiration",
        "expiration_date",
        "renewal_date",
        "due_date",
        "ληξη",
        "λήξη",
        "ημερομηνια ληξης",
        "ημερομηνία λήξης",
    ],
}


def auto_detect_mapping(columns: list[str]) -> dict[str, str | None]:
    normalized_columns = {normalize_column_name(c): c for c in columns}
    result: dict[str, str | None] = {field: None for field in REQUIRED_FIELDS}

    for canonical_field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            normalized_alias = normalize_column_name(alias)
            if normalized_alias in normalized_columns:
                result[canonical_field] = normalized_columns[normalized_alias]
                break

        if result[canonical_field] is None:
            matches = get_close_matches(
                word=normalize_column_name(aliases[0]),
                possibilities=list(normalized_columns.keys()),
                n=1,
                cutoff=0.7,
            )
            if matches:
                result[canonical_field] = normalized_columns[matches[0]]

    return result


def apply_mapping(df: pd.DataFrame, mapping: dict[str, str | None]) -> pd.DataFrame:
    missing = [f for f in REQUIRED_FIELDS if not mapping.get(f)]
    if missing:
        raise ValueError(f"Cannot apply mapping. Missing fields: {missing}")

    rename_map = {raw: canonical for canonical, raw in mapping.items() if raw}
    mapped = df.rename(columns=rename_map)

    for required in REQUIRED_FIELDS:
        if required not in mapped.columns:
            raise ValueError(f"Mapped file is missing required column: {required}")

    return mapped[REQUIRED_FIELDS].copy()


def _to_policy_rows(df: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    df = df.copy()
    df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce").dt.date

    rows: list[dict] = []
    invalid_rows: list[dict] = []

    for _, row in df.iterrows():
        raw = {
            "client_name": row.get("client_name"),
            "email": row.get("email"),
            "expiry_date": row.get("expiry_date"),
        }
        normalized = normalize_policy_row(raw)
        errors = validate_policy_payload(normalized)
        if errors:
            invalid_rows.append({"row": raw, "errors": errors})
            continue
        rows.append(normalized)

    return rows, invalid_rows


def parse_csv(content: bytes, mapping: dict[str, str | None] | None = None) -> tuple[list[dict], list[dict], dict[str, str | None]]:
    df = pd.read_csv(BytesIO(content))
    resolved_mapping = mapping or auto_detect_mapping(df.columns.tolist())
    mapped = apply_mapping(df, resolved_mapping)
    rows, invalid_rows = _to_policy_rows(mapped)
    return rows, invalid_rows, resolved_mapping
