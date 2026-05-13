# app/pipeline/file_reviewer.py
"""
Per-file review loop (pipeline step 4).

Responsibilities:
  - Run static analysis, LLM review, and guardrail for each FileDiff.
  - Inject file_path into guardrail-passed issues via IssueWithPath.
  - Collect filtered issues for later DB persistence (review_id not yet known).
  - Throttle between files with INTER_FILE_DELAY.

Error handling (per project-conventions):
  - GroqRateLimitError  → NOT caught here; propagates to orchestrator → task
                          which retries the whole job. Correct: retry from scratch
                          rather than posting partial results to GitHub.
  - ReviewAgentError    → caught; file marked skipped, pipeline continues.
  - Static analysis exc → caught; file marked skipped, pipeline continues.

Returns from review_all_files():
    (outcomes, agent_skipped, all_filtered_issues)

    outcomes            List[FileReviewOutcome] — one per file_diff, including
                        skipped files (skipped=True, no guardrail_result).
    agent_skipped       List[str] — filenames skipped due to errors.
    all_filtered_issues List[FilteredIssueData] — accumulated across all files
                        for bulk DB write after the Review row is committed.
"""
import asyncio
import logging
from typing import List

from app.pipeline.types import FileReviewOutcome, IssueWithPath
from app.schemas.diff import FileDiff
from app.schemas.review import ReviewResult
from app.services.guardrail import FilteredIssueData, guardrail_check
from app.services.review_agent import ReviewAgentError, review_file
from app.services.static_analysis import run_static_analysis
from app.utils.constants import INTER_FILE_DELAY
from app.utils.groq_client import GroqRateLimitError

logger = logging.getLogger(__name__)


async def review_single_file(
    file_diff: FileDiff,
    index: int,
    total: int,
) -> tuple[FileReviewOutcome, list[FilteredIssueData]]:
    """
    Run the full review pipeline for a single file (steps 4a → 4c).

    Args:
        file_diff: The parsed diff to review.
        index:     0-based position in the batch (used for log messages).
        total:     Total number of files being reviewed (used for log messages).

    Returns:
        (outcome, filtered_issues)

        outcome         FileReviewOutcome with guardrail_result populated on
                        success, or skipped=True on error.
        filtered_issues Issues that did not pass the guardrail for this file
                        (empty list if file was skipped).

    Raises:
        GroqRateLimitError — propagates; Celery task will retry.
    """
    logger.info(
        "Reviewing file %d/%d: %s",
        index + 1, total, file_diff.filename,
    )

    outcome = FileReviewOutcome(file_diff=file_diff)
    filtered_issues: list[FilteredIssueData] = []

    # Step 4a: Static analysis
    try:
        tool_findings = await run_static_analysis(file_diff)
    except Exception as exc:
        logger.error(
            "Static analysis failed for %s: %s — skipping file",
            file_diff.filename, exc,
        )
        outcome.skipped = True
        outcome.skip_reason = f"static_analysis_error: {exc}"
        return outcome, filtered_issues

    # Step 4b: LLM review
    # GroqRateLimitError is NOT caught — propagates to Celery for retry.
    try:
        review_result: ReviewResult = await review_file(file_diff, tool_findings)
    except GroqRateLimitError:
        logger.warning(
            "Groq rate limit hit on file %s — propagating for task retry",
            file_diff.filename,
        )
        raise
    except ReviewAgentError as exc:
        logger.error(
            "ReviewAgentError on %s: %s — skipping file",
            file_diff.filename, exc,
        )
        outcome.skipped = True
        outcome.skip_reason = f"review_agent_error: {exc}"
        return outcome, filtered_issues

    # Step 4c: Guardrail
    guardrail_result = guardrail_check(review_result, file_diff)

    # Inject file_path into each passed issue.
    # Issue (locked LLM schema) has no file_path; github_poster.py needs it.
    guardrail_result.passed_issues = [
        IssueWithPath.from_issue(issue, file_diff.filename)
        for issue in guardrail_result.passed_issues
    ]

    outcome.guardrail_result = guardrail_result
    filtered_issues = list(guardrail_result.filtered_issues)

    return outcome, filtered_issues


async def review_all_files(
    file_diffs: List[FileDiff],
) -> tuple[List[FileReviewOutcome], List[str], List[FilteredIssueData]]:
    """
    Run the per-file review loop over all parsed file diffs.

    Throttles with INTER_FILE_DELAY between files to avoid saturating the
    Groq API. Accumulates filtered issues in memory — they are written to DB
    only after the Review row is committed (so they can reference a real id).

    Args:
        file_diffs: Parsed diffs returned by the parse step.

    Returns:
        (outcomes, agent_skipped, all_filtered_issues)

    Raises:
        GroqRateLimitError — propagates; orchestrator lets it reach the task.
    """
    outcomes: List[FileReviewOutcome] = []
    agent_skipped: List[str] = []
    all_filtered_issues: List[FilteredIssueData] = []

    total = len(file_diffs)

    for i, file_diff in enumerate(file_diffs):
        if i > 0:
            await asyncio.sleep(INTER_FILE_DELAY)

        outcome, filtered = await review_single_file(file_diff, i, total)
        outcomes.append(outcome)
        all_filtered_issues.extend(filtered)

        if outcome.skipped:
            agent_skipped.append(file_diff.filename)

    return outcomes, agent_skipped, all_filtered_issues
