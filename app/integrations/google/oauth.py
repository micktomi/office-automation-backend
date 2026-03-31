from __future__ import annotations

import base64
import json
import os

from google_auth_oauthlib.flow import Flow

from app.config import get_settings


def get_google_flow(state: str | None = None, redirect_uri: str | None = None) -> Flow:
    settings = get_settings()
    scopes = list(settings.google_scopes)

    credentials_json_b64 = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if credentials_json_b64:
        client_config = json.loads(base64.b64decode(credentials_json_b64))
        flow = Flow.from_client_config(
            client_config,
            scopes=scopes,
            state=state,
        )
    else:
        flow = Flow.from_client_secrets_file(
            settings.google_credentials_file,
            scopes=scopes,
            state=state,
        )

    flow.redirect_uri = redirect_uri or settings.google_redirect_uri
    return flow
