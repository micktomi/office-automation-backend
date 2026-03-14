import logging
from io import BytesIO

import pandas as pd

from app.adapters.csv_adapter import apply_mapping, auto_detect_mapping
from app.engine.normalization import normalize_policy_row
from app.engine.validation import validate_policy_payload

logger = logging.getLogger(__name__)


def parse_excel(
    content: bytes, mapping: dict[str, str | None] | None = None
) -> tuple[list[dict], list[dict], dict[str, str | None]]:
    try:
        df = pd.read_excel(BytesIO(content))
        logger.info("Excel columns detected: %s", df.columns.tolist())

        resolved_mapping = mapping or auto_detect_mapping(df.columns.tolist())
        logger.info("Resolved mapping: %s", resolved_mapping)

        # Check for missing fields before applying
        missing = [f for f, v in resolved_mapping.items() if v is None]
        if missing:
            raise ValueError(
                f"Δεν βρέθηκαν οι στήλες: {', '.join(missing)}. Παρακαλώ ελέγξτε το αρχείο Excel."
            )

        mapped = apply_mapping(df, resolved_mapping)

        # Convert expiry_date to datetime objects
        mapped["expiry_date"] = pd.to_datetime(
            mapped["expiry_date"], errors="coerce"
        ).dt.date

        rows: list[dict] = []
        invalid_rows: list[dict] = []

        for _, row in mapped.iterrows():
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

        return rows, invalid_rows, resolved_mapping
    except Exception as exc:
        logger.error("Error parsing excel: %s", exc)
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Σφάλμα κατά την ανάγνωση του Excel: {str(exc)}")
