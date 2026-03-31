from __future__ import annotations

import logging
import os
import json
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.config import get_settings
from app.integrations.google.oauth import get_google_flow
from app.integrations.google.oauth_state import oauth_state_store

router = APIRouter(prefix="/auth/google", tags=["auth_google"])
logger = logging.getLogger(__name__)


def _build_redirect_uri(request: Request) -> str:
    return get_settings().google_redirect_uri


@router.get("/start")
def google_start(request: Request):
    try:
        redirect_uri = _build_redirect_uri(request)
        print("REDIRECT_URI =", redirect_uri, flush=True)
        flow = get_google_flow(redirect_uri=redirect_uri)
        auth_url, state = flow.authorization_url(
            prompt="select_account consent",
            access_type="offline",
        )
        print("AUTH_URL =", auth_url, flush=True)

        code_verifier = getattr(flow, "code_verifier", None)
        if state and code_verifier:
            oauth_state_store.put(state, code_verifier)

        return {"auth_url": auth_url}
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail="Google credentials file not found. Add GOOGLE_CREDENTIALS_FILE in backend .env.",
        ) from exc


@router.get("/")
@router.get("/login")
def google_login_alias(request: Request):
    try:
        redirect_uri = _build_redirect_uri(request)
        print("REDIRECT_URI =", redirect_uri, flush=True)
        flow = get_google_flow(redirect_uri=redirect_uri)
        auth_url, state = flow.authorization_url(
            prompt="select_account consent",
            access_type="offline",
        )
        print("AUTH_URL =", auth_url, flush=True)

        code_verifier = getattr(flow, "code_verifier", None)
        if state and code_verifier:
            oauth_state_store.put(state, code_verifier)

        return RedirectResponse(url=auth_url)
    except Exception as exc:
        logger.exception("Google login redirect failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/callback")
def google_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
):
    if not code:
        # Returning 400 instead of 422 for scanners and missing params
        raise HTTPException(status_code=400, detail="Missing OAuth code parameter.")

    settings = get_settings()

    try:
        flow = get_google_flow(state=state, redirect_uri=_build_redirect_uri(request))
        code_verifier = oauth_state_store.pop(state)
        if code_verifier:
            flow.code_verifier = code_verifier
        else:
            logger.warning("OAuth code verifier missing for state=%s. Proceeding without PKCE verifier.", state)
        # Google may return a superset of already-granted scopes for the same client.
        # Accept that scope set instead of failing callback with scope mismatch.
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
        flow.fetch_token(code=code)

        token_path = Path(settings.google_token_file)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(flow.credentials.to_json(), encoding="utf-8")

        return RedirectResponse(url=f"{settings.frontend_url}?google=connected")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Google callback failed: %s", exc)
        reason = quote_plus(str(exc))
        return RedirectResponse(url=f"{settings.frontend_url}/login?google=error&reason={reason}")


@router.get("/status")
def google_status():
    settings = get_settings()
    token_path = Path(settings.google_token_file)
    creds_path = Path(settings.google_credentials_file)
    token_scopes: list[str] | None = None
    if token_path.exists():
        try:
            raw = json.loads(token_path.read_text(encoding="utf-8"))
            scopes = raw.get("scopes") or raw.get("scope")
            if isinstance(scopes, list):
                token_scopes = scopes
            elif isinstance(scopes, str):
                token_scopes = scopes.split()
        except Exception:
            token_scopes = None

    return {
        "connected": token_path.exists(),
        "token_file": str(token_path),
        "token_file_exists": token_path.exists(),
        "credentials_file": str(creds_path),
        "credentials_file_exists": creds_path.exists(),
        "token_scopes": token_scopes,
    }
