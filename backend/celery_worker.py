import logging
from celery import Celery
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery(
    "github_review",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,    # enables job ID tracking via AsyncResult
    include=["app.tasks.review_task"],
)

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Reliability
    task_acks_late=True,                # re-queue if worker dies mid-task
    task_reject_on_worker_lost=True,    # don't silently drop on worker crash
    task_max_retries=3,

    # Throughput
    worker_prefetch_multiplier=1,       # one task at a time per worker
                                        # prevents memory spikes during PR review

    # Result expiry — keep job results for 24 h so dashboard can poll status
    result_expires=86400,
)