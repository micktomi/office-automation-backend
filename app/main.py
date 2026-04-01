from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from app.config import get_settings
from app.models.database import Base, engine
from app.routers import (
    activity,
    agent,
    assistant,
    auth,
    auth_google,
    dashboard,
    clients,
    email,
    insurance,
    messaging,
    reports,
    tasks,
)
from app.services.logging_service import setup_logging
from app.services.scheduler_service import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    backend_root = Path(__file__).resolve().parents[1]
    data_dir = Path(os.getenv("DATA_DIR", str(backend_root / "data"))).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Startup config | gemini=%s | sms_provider=%s | whatsapp_gateway=%s | google_creds=%s | google_token=%s | frontend=%s | redirect=%s | db_scheme=%s | data_dir=%s",
        bool(settings.gemini_api_key),
        settings.sms_provider or "<empty>",
        settings.whatsapp_gateway_url,
        Path(settings.google_credentials_file).exists(),
        Path(settings.google_token_file).exists(),
        settings.frontend_url,
        settings.google_redirect_uri,
        settings.database_url.split(":", 1)[0],
        data_dir,
    )

    # Import models to ensure they are registered
    from app.models import user, activity_log, client, email_message, policy, reminder_log

    # This creates all tables (users, clients, policies, reminders, activity_log) 
    # if they don't exist in office_agent.db
    Base.metadata.create_all(bind=engine)
    
    # Simple migration for client_id if policies table exists but lacks it
    # (In case someone is migrating from renewals.db by renaming it)
    inspector = inspect(engine)
    if inspector.has_table("policies"):
        existing_columns = {column["name"] for column in inspector.get_columns("policies")}
        with engine.begin() as connection:
            if "client_id" not in existing_columns:
                connection.execute(text("ALTER TABLE policies ADD COLUMN client_id INTEGER"))
            if "last_notified_at" not in existing_columns:
                connection.execute(text("ALTER TABLE policies ADD COLUMN last_notified_at DATETIME"))
    if inspector.has_table("synced_emails"):
        existing_columns = {column["name"] for column in inspector.get_columns("synced_emails")}
        with engine.begin() as connection:
            if "processed" not in existing_columns:
                connection.execute(text("ALTER TABLE synced_emails ADD COLUMN processed BOOLEAN DEFAULT 0"))
            if "received_at" not in existing_columns:
                connection.execute(text("ALTER TABLE synced_emails ADD COLUMN received_at DATETIME"))
            if "synced_at" not in existing_columns:
                connection.execute(text("ALTER TABLE synced_emails ADD COLUMN synced_at DATETIME"))

    start_scheduler()
    yield
    stop_scheduler()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        description="Deterministic backend for insurance office automation",
    )

    allow_origins = settings.cors_origins_list
    if not allow_origins:
        allow_origins = ["*"]
        
    allow_all_origins = "*" in allow_origins
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=not allow_all_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(email.router)
    app.include_router(insurance.router)
    app.include_router(tasks.router)
    app.include_router(reports.router)
    app.include_router(agent.router)
    app.include_router(assistant.router)
    app.include_router(auth.router)
    app.include_router(auth_google.router)
    app.include_router(messaging.router)
    app.include_router(activity.router)
    app.include_router(clients.router)
    app.include_router(dashboard.router)

    @app.get("/")
    def root():
        return {
            "status": "running",
            "service": settings.app_name,
            "version": settings.app_version,
            "mode": "deterministic",
        }

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
