---
name: fastapi-production
description: Use when writing FastAPI routes, middleware, background tasks, database session management, or webhook handling for this project. Enforces async patterns, proper error handling, and production-safe conventions.
---

# FastAPI Production Skill — ACE Code Review Agent

## Use this skill when
- Adding or modifying API endpoints in `app/routers/`
- Writing middleware
- Setting up database sessions
- Handling webhook validation
- Writing Celery task definitions

## Do not use this skill when
- Working on the LLM agent logic
- Working on the frontend
- Writing static analysis tools

---

## App Setup Pattern

```python
# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.database import init_db
from app.routers import webhook, reviews, logs, health
from app.utils.logging_config import setup_logging
import logging

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()
    logger.info("ACE Code Review Agent started")
    yield
    logger.info("ACE Code Review Agent shutting down")

app = FastAPI(
    title="ACE Code Review Agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://your-frontend.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook.router, prefix="/webhook", tags=["webhook"])
app.include_router(reviews.router, prefix="/api/reviews", tags=["reviews"])
app.include_router(logs.router, prefix="/api/logs", tags=["logs"])
app.include_router(health.router, prefix="/health", tags=["health"])
```

---

## Webhook Endpoint Pattern

```python
# app/routers/webhook.py
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from app.utils.hmac_validator import validate_github_signature
from app.tasks.review_task import process_pr_review
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    # Step 1: Always validate signature first
    payload = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    
    if not validate_github_signature(payload, signature):
        logger.warning("Invalid webhook signature from %s", request.client.host)
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Step 2: Parse event type
    event = request.headers.get("X-GitHub-Event", "")
    if event not in ("pull_request", "ping"):
        return {"status": "ignored", "event": event}

    if event == "ping":
        return {"status": "pong"}

    # Step 3: Dispatch to Celery — do not process in the request thread
    data = await request.json()
    action = data.get("action", "")
    if action not in ("opened", "synchronize", "reopened"):
        return {"status": "ignored", "action": action}

    pr_number = data["pull_request"]["number"]
    repo_full_name = data["repository"]["full_name"]
    
    # Celery task — async job, returns immediately
    process_pr_review.delay(repo_full_name=repo_full_name, pr_number=pr_number)
    
    logger.info("Queued review for %s PR #%d", repo_full_name, pr_number)
    return {"status": "queued", "pr": pr_number}
```

**IMPORTANT**: Never process the full PR review in the webhook request handler. GitHub expects a response within 10 seconds. Celery handles the actual work.

---

## Database Session Pattern

```python
# app/database.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# Dependency for endpoints
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

```python
# In a router — correct usage
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

@router.get("/{review_id}")
async def get_review(review_id: int, db: AsyncSession = Depends(get_db)):
    ...
```

---

## Config Pattern (Pydantic Settings)

```python
# app/config.py
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # GitHub
    GITHUB_APP_ID: int
    GITHUB_PRIVATE_KEY_PATH: str
    GITHUB_WEBHOOK_SECRET: str
    
    # Groq
    GROQ_API_KEY: str
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./dev.db"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # App
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    BYPASS_GUARDRAILS: bool = False  # ONLY True in testing environment

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

---

## HMAC Webhook Validation

```python
# app/utils/hmac_validator.py
import hmac
import hashlib
from app.config import get_settings

def validate_github_signature(payload: bytes, signature_header: str) -> bool:
    """
    Validate GitHub webhook HMAC-SHA256 signature.
    Returns False (not raises) — caller decides HTTP response.
    """
    settings = get_settings()
    if not signature_header.startswith("sha256="):
        return False
    
    expected = hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    
    received = signature_header[7:]  # strip "sha256="
    return hmac.compare_digest(expected, received)
```

---

## Standard Response Shape

```python
# app/schemas/responses.py
from pydantic import BaseModel
from typing import Generic, TypeVar, Optional
from datetime import datetime, timezone

T = TypeVar("T")

class APIResponse(BaseModel, Generic[T]):
    data: Optional[T] = None
    error: Optional[dict] = None
    timestamp: str = ""

    def model_post_init(self, __context):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

# Usage in router:
# return APIResponse(data=review)
# return APIResponse(error={"code": "NOT_FOUND", "message": "Review not found"})
```

---

## Celery Setup

```python
# celery_worker.py (project root)
from celery import Celery
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "ace_review",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.review_task"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,           # Re-queue on worker crash
    task_reject_on_worker_lost=True,
    task_max_retries=3,
    worker_prefetch_multiplier=1,  # One task at a time per worker
)
```

---

## What NOT To Do

```python
# WRONG — blocking DB call in async function
def get_review(review_id: int):  # missing async
    session = Session()  # synchronous session
    
# WRONG — processing review in webhook handler
@router.post("")
async def receive_webhook(request: Request):
    # ... validate ...
    await full_review_pipeline(pr_number)  # blocks for 30+ seconds
    return {"status": "done"}  # GitHub already timed out

# WRONG — raw SQL without ORM
result = await db.execute("SELECT * FROM reviews")

# WRONG — missing rollback on error
async with AsyncSession(engine) as session:
    session.add(review)
    await session.commit()  # if this fails, no rollback
```