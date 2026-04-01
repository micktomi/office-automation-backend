from __future__ import annotations

import logging
import os
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException, Query, Request, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from googleapiclient.discovery import build

from app.config import get_settings
from app.integrations.google.oauth import get_google_flow
from app.integrations.google.oauth_state import oauth_state_store
from app.models.database import get_db
from app.models.user import User

router = APIRouter(prefix="/auth/google", tags=["auth_google"])
logger = logging.getLogger(__name__)


def _build_redirect_uri(request: Request) -> str:
    return get_settings().google_redirect_uri


def _get_code_verifier(flow) -> str | None:
    code_verifier = getattr(flow, "code_verifier", None)
    if code_verifier:
        return code_verifier

    oauth2session = getattr(flow, "oauth2session", None)
    client = getattr(oauth2session, "_client", None)
    return getattr(client, "code_verifier", None)


def _set_code_verifier_cookie(response, code_verifier: str) -> None:
    response.set_cookie(
        key="google_code_verifier",
        value=code_verifier,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )


@router.get("/start")
def google_start(request: Request):
    try:
        redirect_uri = _build_redirect_uri(request)
        logger.info("Google start redirect_uri=%s", redirect_uri)
        flow = get_google_flow(redirect_uri=redirect_uri, autogenerate_code_verifier=True)
        auth_url, state = flow.authorization_url(
            prompt="select_account consent",
            access_type="offline",
        )
        logger.info("Google start auth_url=%s", auth_url)

        code_verifier = _get_code_verifier(flow)
        if state and code_verifier:
            oauth_state_store.put(state, code_verifier)

        response = JSONResponse({"auth_url": auth_url})
        if code_verifier:
            _set_code_verifier_cookie(response, code_verifier)

        return response
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
        logger.info("Google login redirect_uri=%s", redirect_uri)
        flow = get_google_flow(redirect_uri=redirect_uri, autogenerate_code_verifier=True)
        auth_url, state = flow.authorization_url(
            prompt="select_account consent",
            access_type="offline",
        )
        logger.info("Google login auth_url=%s", auth_url)

        code_verifier = _get_code_verifier(flow)
        if state and code_verifier:
            oauth_state_store.put(state, code_verifier)

        response = RedirectResponse(url=auth_url)
        if code_verifier:
            _set_code_verifier_cookie(response, code_verifier)

        return response
    except Exception as exc:
        logger.exception("Google login redirect failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/callback")
def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
):
    if not code:
        raise HTTPException(status_code=400, detail="Missing OAuth code parameter.")

    settings = get_settings()

    try:
        flow = get_google_flow(state=state, redirect_uri=_build_redirect_uri(request))
        code_verifier = request.cookies.get("google_code_verifier") or oauth_state_store.pop(state)
        if code_verifier:
            flow.code_verifier = code_verifier
        
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
        flow.fetch_token(code=code)
        credentials = flow.credentials

        # Fetch user info from Google
        service = build("oauth2", "v2", credentials=credentials)
        user_info = service.userinfo().get().execute()
        email = user_info.get("email")
        google_id = user_info.get("id")
        name = user_info.get("name")
        picture = user_info.get("picture")

        if not email:
            raise HTTPException(status_code=400, detail="Could not retrieve email from Google.")

        # Update or Create User in DB
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(email=email, google_id=google_id, name=name, picture=picture)
            db.add(user)
        else:
            user.last_login = datetime.now(timezone.utc)
            if name: user.name = name
            if picture: user.picture = picture
        
        db.commit()
        db.refresh(user)

        # Save dynamic token path based on user email
        token_path = Path(settings.get_token_path(email))
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")

        # Also keep a symlink/copy as default 'token.json' for single-user compatibility
        default_token = Path(settings.google_token_file)
        default_token.write_text(credentials.to_json(), encoding="utf-8")

        response = RedirectResponse(
            url=f"{settings.frontend_url}?google=connected&email={quote_plus(email)}"
        )
        response.set_cookie(
            key="session",
            value=f"user_{user.id}", # Real user session ID
            httponly=True,
            secure=True,
            samesite="none",
            path="/"
        )
        response.delete_cookie(key="google_code_verifier", path="/")

        return response
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
