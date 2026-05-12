import logging
from celery_worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,    # seconds before first retry
    name="tasks.process_pr_review",
)
def process_pr_review(self, *, repo_full_name: str, pr_number: int) -> dict:
    """
    Full PR review pipeline — diff → static analysis → LLM → guardrail → GitHub comment.

    This task stub is wired and dispatchable. The pipeline body is implemented
    in a future session. Returning a placeholder result now so job tracking works
    end-to-end from Session 1.

    Args:
        repo_full_name: e.g. "owner/repo"
        pr_number:      GitHub PR number

    Returns:
        dict with task status — will be replaced with real ReviewResult summary.
    """
    logger.info(
        "process_pr_review received — repo=%s pr=%d (pipeline stub, not yet implemented)",
        repo_full_name,
        pr_number,
    )

    # TODO: Implement full pipeline in the review pipeline session:
    #   1. parse_pr_diff()
    #   2. filter_skip_files()
    #   3. for each FileDiff: static_analysis → review_agent → guardrail → log_filtered
    #   4. post_github_comments()
    #   5. save_review_to_db()

    return {
        "status": "stub",
        "repo": repo_full_name,
        "pr": pr_number,
        "message": "Pipeline not yet implemented",
    }