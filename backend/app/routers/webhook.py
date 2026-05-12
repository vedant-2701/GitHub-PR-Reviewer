import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.tasks.review_task import process_pr_review
from app.utils.hmac_validator import validate_github_signature

logger = logging.getLogger(__name__)
router = APIRouter()

# PR actions that should trigger a review
REVIEWABLE_ACTIONS = {"opened", "synchronize", "reopened"}


@router.post("")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Receive and dispatch GitHub webhook events.

    Flow:
        1. Validate HMAC-SHA256 signature — reject immediately on failure.
        2. Route by event type — ignore anything that isn't pull_request or ping.
        3. Route by action — only opened / synchronize / reopened trigger a review.
        4. Dispatch Celery task and return job_id for tracking.

    GitHub expects a response within 10 seconds.
    All heavy work is dispatched to Celery — never processed inline here.
    """
    # ── Step 1: Read raw bytes BEFORE any JSON parsing ───────────────────────
    # FastAPI's request.body() and request.json() can both be called, but
    # body() must be called first to get the raw bytes for HMAC validation.
    payload = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not validate_github_signature(payload, signature):
        logger.warning(
            "Rejected webhook from %s — invalid HMAC signature",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=401, detail="Invalid signature")

    # ── Step 2: Route by event type ──────────────────────────────────────────
    event = request.headers.get("X-GitHub-Event", "")

    if event == "ping":
        logger.info("GitHub ping received — webhook is configured correctly")
        return {
            "data": {"status": "pong"},
            "error": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    if event != "pull_request":
        logger.debug("Ignoring event type: %s", event)
        return {
            "data": {"status": "ignored", "event": event},
            "error": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ── Step 3: Parse payload and route by action ─────────────────────────────
    data = await request.json()
    action = data.get("action", "")

    if action not in REVIEWABLE_ACTIONS:
        logger.debug("Ignoring pull_request action: %s", action)
        return {
            "data": {"status": "ignored", "action": action},
            "error": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    pr_number: int = data["pull_request"]["number"]
    repo_full_name: str = data["repository"]["full_name"]

    # ── Step 4: Dispatch to Celery ────────────────────────────────────────────
    # .delay() is non-blocking — returns an AsyncResult immediately.
    # The job_id lets the dashboard poll /api/reviews for status.
    task = process_pr_review.delay(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
    )

    logger.info(
        "Queued review — repo=%s pr=%d action=%s job_id=%s",
        repo_full_name,
        pr_number,
        action,
        task.id,
    )

    return {
        "data": {
            "status": "queued",
            "pr": pr_number,
            "repo": repo_full_name,
            "job_id": task.id,
        },
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }