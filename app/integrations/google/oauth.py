from __future__ import annotations

import base64
import json
import os

from google_auth_oauthlib.flow import Flow

from app.config import get_settings

SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
)


def get_google_flow(
    state: str | None = None,
    redirect_uri: str | None = None,
    autogenerate_code_verifier: bool = False,
) -> Flow:
    settings = get_settings()

    credentials_json_b64 = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if credentials_json_b64:
        client_config = json.loads(base64.b64decode(credentials_json_b64))
        flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            state=state,
            autogenerate_code_verifier=autogenerate_code_verifier,
        )
    else:
        flow = Flow.from_client_secrets_file(
            settings.google_credentials_file,
            scopes=SCOPES,
            state=state,
            autogenerate_code_verifier=autogenerate_code_verifier,
        )

    flow.redirect_uri = redirect_uri or settings.google_redirect_uri
    return flow
