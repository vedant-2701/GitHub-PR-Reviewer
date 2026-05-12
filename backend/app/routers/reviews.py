# TODO: Implement in the review pipeline session.
#       Will expose:
#           GET  /api/reviews           — paginated list of past reviews
#           GET  /api/reviews/{id}      — full review detail + issues
#           GET  /api/reviews/job/{id}  — Celery job status by job_id
#
import logging
from datetime import datetime, timezone
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def list_reviews() -> dict:
    return {
        "data": None,
        "error": {"code": "NOT_IMPLEMENTED", "message": "Reviews endpoint coming in a future session"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/{review_id}")
async def get_review(review_id: int) -> dict:
    return {
        "data": None,
        "error": {"code": "NOT_IMPLEMENTED", "message": "Review detail endpoint coming in a future session"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }