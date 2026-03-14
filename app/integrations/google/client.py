from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import get_settings

logger = logging.getLogger(__name__)


async def load_credentials_from_db(db_service: Any, user_id: str) -> Credentials | None:
    settings = get_settings()
    token_row = await db_service.get_google_token(user_id)

    if token_row and "token_data" in token_row:
        return Credentials.from_authorized_user_info(token_row["token_data"], settings.google_scopes)

    return None


async def save_credentials_to_db(db_service: Any, user_id: str, creds: Credentials) -> None:
    token_dict = json.loads(creds.to_json())
    await db_service.upsert_google_token(user_id, token_dict)


def _build_service_blocking(api_name: str, api_version: str, creds: Credentials) -> Any:
    return build(api_name, api_version, credentials=creds, cache_discovery=False)


async def get_google_service(db_service: Any, user_id: str, api_name: str, api_version: str) -> Any | None:
    creds = await load_credentials_from_db(db_service, user_id)

    if not creds:
        logger.warning("No Google credentials found in DB for user %s", user_id)
        return None

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                await asyncio.to_thread(creds.refresh, Request())
                await save_credentials_to_db(db_service, user_id, creds)
            except Exception as exc:
                logger.error("Failed to refresh Google token for user %s: %s", user_id, exc)
                return None
        else:
            logger.warning("Google credentials for user %s are invalid and not refreshable", user_id)
            return None

    try:
        return await asyncio.to_thread(_build_service_blocking, api_name, api_version, creds)
    except Exception as exc:
        logger.error(
            "Failed to build Google service %s/%s for user %s: %s",
            api_name,
            api_version,
            user_id,
            exc,
        )
        return None
