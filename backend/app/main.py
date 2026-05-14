import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import health, logs, reviews, webhook
from app.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan handler.

    Startup:
        - Initialise structured logging first (so all subsequent startup
          messages are captured in the correct format).
        - Create database tables.

    Shutdown:
        - Log graceful shutdown. Connection pools are closed automatically
          by SQLAlchemy when the engine is garbage collected.
    """
    setup_logging()
    logger.info("ACE Code Review Agent starting up...")

    # Alembic manages schema — do not call init_db() / create_all here.
    # If tables are missing, the first DB operation will raise and surface
    # the error clearly rather than silently creating an unversioned schema.

    logger.info("Startup complete. Ready to receive webhook events.")
    yield

    logger.info("ACE Code Review Agent shutting down.")


settings = get_settings()

app = FastAPI(
    title="ACE Code Review Agent",
    description="Automated GitHub PR reviewer with guardrail layer.",
    version="1.0.0",
    lifespan=lifespan,
    # Disable docs in production — expose only in development
    docs_url="/docs" if settings.ENVIRONMENT == "development" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT == "development" else None,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Adjust allow_origins when deploying — replace the Vercel wildcard with
# the specific production frontend URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",          # Vite dev server
        "http://localhost:3000",          # alternative local port
        "https://*.vercel.app",           # TODO: replace with specific prod URL
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],        # restrict — no PUT/DELETE needed yet
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(health.router,   prefix="/health",       tags=["health"])
app.include_router(webhook.router,  prefix="/webhook",      tags=["webhook"])
app.include_router(reviews.router,  prefix="/api/reviews",  tags=["reviews"])
app.include_router(logs.router,     prefix="/api/logs",     tags=["logs"])
