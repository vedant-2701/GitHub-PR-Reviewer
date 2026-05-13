# app/tasks/review_task.py
"""
Celery task: entry point for the PR review pipeline.

This module is intentionally thin. All pipeline logic lives in app/pipeline/:

    pipeline/github_fetcher.py   — GitHub API I/O (fetch PR file diffs)
    pipeline/file_reviewer.py    — per-file loop (static analysis → LLM → guardrail)
    pipeline/summary_builder.py  — verdict computation and summary comment text
    pipeline/db_writer.py        — atomic DB persistence (Review + FilteredIssue)
    pipeline/orchestrator.py     — step sequencing (the canonical pipeline order)

Pipeline order (do not reorder — defined in orchestrator.py):
  1. fetch_pr_files()       — PyGitHub, per-file (raw_diff, file_content, filename)
  2. parse_file_diff()      — diff_parser.py → Optional[FileDiff] per file
  3. review_all_files()     — static analysis → LLM → guardrail per FileDiff
  4. post_review_comments() — inline comments + PR summary
  5. save_review()          — Review row + FilteredIssue rows (atomic commit)

Error handling:
  GroqRateLimitError  → self.retry(countdown=60, max_retries=3)
  GithubException     → log + raise (fatal — needs manual investigation)
  Any unhandled       → log + raise (Celery marks task FAILED)

Async boundary:
  Celery tasks are synchronous. All async work runs inside async_pipeline(),
  called via asyncio.run() at the task boundary.
  async_pipeline() is module-level (not a nested closure) per project convention.
"""
import asyncio
import logging

from celery import Task
from github import GithubException

from app.pipeline import async_pipeline
from app.utils.groq_client import GroqRateLimitError
from celery_worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    name="app.tasks.review_task.process_pr_review",
)
def process_pr_review(self: Task, repo_full_name: str, pr_number: int) -> None:
    """
    Celery entry point. Synchronous wrapper around async_pipeline().

    Retry policy:
      GroqRateLimitError → retry after 60 s (max 3 retries)
      All other errors   → mark FAILED, do not retry
    """
    job_id = self.request.id

    logger.info(
        "Task start [attempt %d/%d] | Job ID: %s | %s PR #%d",
        self.request.retries + 1,
        self.max_retries + 1,
        job_id,
        repo_full_name,
        pr_number,
    )

    try:
        asyncio.run(async_pipeline(repo_full_name, pr_number, job_id))
    except GroqRateLimitError as exc:
        logger.warning(
            "Groq rate limit — scheduling retry %d/%d in 60s | Job ID: %s | %s PR #%d",
            self.request.retries + 1,
            self.max_retries,
            job_id,
            repo_full_name,
            pr_number,
        )
        raise self.retry(exc=exc, countdown=60)
    except GithubException as exc:
        logger.error(
            "GitHub API error | Job ID: %s | %s PR #%d: status=%d message=%s — task FAILED",
            job_id,
            repo_full_name,
            pr_number,
            exc.status,
            exc.data.get("message", str(exc)),
        )
        raise  # do not retry — GitHub errors need investigation
    except Exception:
        logger.exception(
            "Unhandled error in pipeline | Job ID: %s | %s PR #%d — task FAILED",
            job_id,
            repo_full_name,
            pr_number,
        )
        raise
