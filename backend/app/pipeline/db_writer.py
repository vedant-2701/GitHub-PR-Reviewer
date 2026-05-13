# app/pipeline/db_writer.py
"""
Database persistence for the review pipeline (pipeline step 6).

Write order (must not be changed — see CLAUDE.md):
  1. db.add(review_record)
  2. db.flush()             — assigns review_record.id, no commit yet
  3. log_filtered_issues()  — inserts FilteredIssue rows with the real id
  4. db.commit()            — commits everything atomically

The flush-before-commit pattern ensures every FilteredIssue row gets the
real review_id, not a placeholder. The single commit keeps the write atomic:
either the Review and all FilteredIssues land together or none do.
"""
import logging
from datetime import datetime, timezone
from typing import List

from app.database import AsyncSessionLocal
from app.models.review import Review, VerdictEnum
from app.pipeline.types import FileReviewOutcome
from app.schemas.diff import FileDiff
from app.services.github_poster import PostResult
from app.services.guardrail import FilteredIssueData, log_filtered_issues

logger = logging.getLogger(__name__)


async def save_review(
    repo_full_name: str,
    pr_number: int,
    job_id: str,
    outcomes: List[FileReviewOutcome],
    post_result: PostResult,
    pattern_skipped: List[str],
    agent_skipped: List[str],
    file_diffs: List[FileDiff],
    summary: str,
    overall_verdict: VerdictEnum,
    all_filtered_issues: List[FilteredIssueData],
) -> int:
    """
    Persist the Review record and all filtered issues atomically.

    Args:
        repo_full_name:       e.g. "org/repo".
        pr_number:            PR number integer.
        job_id:               Celery task request ID.
        outcomes:             Per-file outcomes from file_reviewer.py.
        post_result:          Result from post_review_comments() (inline counts).
        pattern_skipped:      Files skipped by parse_file_diff().
        agent_skipped:        Files skipped due to errors in the review loop.
        file_diffs:           All parsed diffs (used to compute files_reviewed).
        summary:              Markdown summary string (stored in DB for auditing).
        overall_verdict:      Verdict computed by compute_overall_verdict().
        all_filtered_issues:  Filtered issues accumulated across all files.

    Returns:
        The assigned DB id of the new Review record.

    Raises:
        SQLAlchemyError — propagates; orchestrator lets it reach the task.
    """
    # Aggregate confidence across files with a guardrail result.
    confidence_scores = [
        o.guardrail_result.original_confidence
        for o in outcomes
        if o.guardrail_result
    ]
    avg_confidence = (
        sum(confidence_scores) / len(confidence_scores)
        if confidence_scores else 0.0
    )

    total_filtered = sum(
        len(o.guardrail_result.filtered_issues)
        for o in outcomes
        if o.guardrail_result
    )

    async with AsyncSessionLocal() as db:
        review_record = Review(
            repo=repo_full_name,
            pr_number=pr_number,
            job_id=job_id,
            verdict=overall_verdict,
            confidence=avg_confidence,
            files_reviewed=[
                fd.filename for fd in file_diffs
                if fd.filename not in agent_skipped
            ],
            files_skipped=pattern_skipped + agent_skipped,
            total_issues_posted=post_result.inline_posted,
            total_issues_filtered=total_filtered,
            summary=summary,
            created_at=datetime.now(timezone.utc),
        )
        db.add(review_record)
        await db.flush()  # assigns review_record.id without committing

        # Write filtered issues with the real review_id.
        await log_filtered_issues(
            review_id=review_record.id,
            filtered=all_filtered_issues,
            db=db,
        )

        await db.commit()

        logger.info(
            "Review saved to DB: id=%d, posted=%d, filtered=%d",
            review_record.id,
            post_result.inline_posted,
            total_filtered,
        )

        return review_record.id
