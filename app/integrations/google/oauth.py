from __future__ import annotations

from google_auth_oauthlib.flow import Flow

from app.config import get_settings


def get_google_flow(state: str | None = None) -> Flow:
    settings = get_settings()
    scopes = list(settings.google_scopes)

    flow = Flow.from_client_secrets_file(
        settings.google_credentials_file,
        scopes=scopes,
        state=state,
        redirect_uri=settings.google_redirect_uri,
    )
    return flow
