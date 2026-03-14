from __future__ import annotations

from contextlib import asynccontextmanager

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
    clients,
    email,
    insurance,
    messaging,
    reports,
    tasks,
)
from app.services.logging_service import setup_logging
from app.services.scheduler_service import start_scheduler, stop_scheduler


def _run_startup_migrations() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("policies"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("policies")}
    migrations = {
        "policy_number": "ALTER TABLE policies ADD COLUMN policy_number VARCHAR",
        "insurer": "ALTER TABLE policies ADD COLUMN insurer VARCHAR",
        "draft_notification": "ALTER TABLE policies ADD COLUMN draft_notification TEXT",
        "source_email_id": "ALTER TABLE policies ADD COLUMN source_email_id VARCHAR",
    }

    with engine.begin() as connection:
        for column_name, statement in migrations.items():
            if column_name not in existing_columns:
                connection.execute(text(statement))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)

    # Import models to ensure they are registered
    from app.models import activity_log, policy, reminder_log

    Base.metadata.create_all(bind=engine)
    _run_startup_migrations()
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

    allow_origins = settings.cors_origins_list or ["*"]
    allow_all_origins = "*" in allow_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if allow_all_origins else allow_origins,
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
