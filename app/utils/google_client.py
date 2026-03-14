from __future__ import annotations

import asyncio
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.config import Settings, get_settings


def _load_credentials_blocking(settings: Settings) -> Credentials | None:
    token_path = Path(settings.google_token_file)
    creds_path = Path(settings.google_credentials_file)
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), settings.google_scopes)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif creds_path.exists():
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), settings.google_scopes)
        creds = flow.run_local_server(port=0)
    else:
        return None

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _build_service_blocking(api_name: str, api_version: str, settings: Settings):
    creds = _load_credentials_blocking(settings)
    if creds is None:
        return None
    return build(api_name, api_version, credentials=creds)


async def get_google_service(api_name: str, api_version: str, settings: Settings | None = None):
    resolved_settings = settings or get_settings()
    return await asyncio.to_thread(_build_service_blocking, api_name, api_version, resolved_settings)
