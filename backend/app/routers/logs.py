# TODO: Implement in the guardrail + dashboard session.
#       Will expose:
#           GET  /api/logs             — paginated filtered issues log
#           GET  /api/logs/stats       — guardrail stats for dashboard right panel
#               Response shape:
#               {
#                 "data": {
#                   "total_issues_found": 142,
#                   "filtered_grounding": 23,
#                   "filtered_line_validation": 11,
#                   "applied_confidence_gating": 18,
#                   "average_confidence": 0.74
#                 }
#               }
#
import logging
from datetime import datetime, timezone
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def list_logs() -> dict:
    return {
        "data": None,
        "error": {"code": "NOT_IMPLEMENTED", "message": "Logs endpoint coming in a future session"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/stats")
async def get_guardrail_stats() -> dict:
    return {
        "data": None,
        "error": {"code": "NOT_IMPLEMENTED", "message": "Guardrail stats endpoint coming in a future session"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }