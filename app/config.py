from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(override=True)


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _normalize_origin(value: str) -> str:
    return value.strip().rstrip("/")


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_version: str
    host: str
    port: int
    log_level: str
    cors_origins: str
    cors_origin_regex: str | None
    frontend_url: str

    database_url: str

    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    from_email: str
    sms_provider: str
    sms_from_number: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_messaging_service_sid: str

    reminder_days_1: int
    reminder_days_2: int
    insurance_warning_days: int

    google_credentials_file: str
    google_token_file: str
    google_scopes: tuple[str, ...]
    google_redirect_uri: str
    gemini_api_key: str | None

    @property
    def cors_origins_list(self) -> list[str]:
        origins: list[str] = []

        for raw_origin in self.cors_origins.split(","):
            origin = _normalize_origin(raw_origin)
            if origin and origin not in origins:
                origins.append(origin)

        frontend_origin = _normalize_origin(self.frontend_url)
        if frontend_origin and frontend_origin not in origins:
            origins.append(frontend_origin)

        return origins


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    backend_root = Path(__file__).resolve().parents[1]

    return Settings(
        app_name=os.getenv("APP_NAME", "Office Automation Backend"),
        app_version=os.getenv("APP_VERSION", "1.0.0"),
        host=os.getenv("HOST", "0.0.0.0"),
        port=_as_int(os.getenv("PORT"), 3001),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        cors_origins=os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
        ),
        cors_origin_regex=os.getenv("CORS_ORIGIN_REGEX"),
        frontend_url=os.getenv("FRONTEND_URL", "http://localhost:3000"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./office_agent.db"),
        smtp_host=os.getenv("SMTP_HOST", "smtp.example.com"),
        smtp_port=_as_int(os.getenv("SMTP_PORT"), 587),
        smtp_user=os.getenv("SMTP_USER", ""),
        smtp_pass=os.getenv("SMTP_PASS", ""),
        from_email=os.getenv("FROM_EMAIL", "noreply@example.com"),
        sms_provider=os.getenv("SMS_PROVIDER", ""),
        sms_from_number=os.getenv("SMS_FROM_NUMBER", ""),
        twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
        twilio_messaging_service_sid=os.getenv("TWILIO_MESSAGING_SERVICE_SID", ""),
        reminder_days_1=_as_int(os.getenv("REMINDER_DAYS_1"), 30),
        reminder_days_2=_as_int(os.getenv("REMINDER_DAYS_2"), 7),
        insurance_warning_days=_as_int(os.getenv("INSURANCE_WARNING_DAYS"), 90),
        google_credentials_file=str(
            (backend_root / os.getenv("GOOGLE_CREDENTIALS_FILE", "client_secret.json")).resolve()
        ),
        google_token_file=str((backend_root / os.getenv("GOOGLE_TOKEN_FILE", "token.json")).resolve()),
        google_scopes=(
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/drive",
        ),
        google_redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:3001/auth/google/callback"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
    )


settings = get_settings()
