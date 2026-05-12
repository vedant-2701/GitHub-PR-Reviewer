import logging
from datetime import datetime, timezone
from fastapi import APIRouter
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def health_check() -> dict:
    """
    Liveness probe endpoint.
    Returns 200 as long as the app process is running.
    Does NOT check DB or Redis connectivity — that is a readiness concern
    and will be added when those connections are critical path.
    """
    settings = get_settings()
    return {
        "data": {
            "status": "ok",
            "environment": settings.ENVIRONMENT,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }