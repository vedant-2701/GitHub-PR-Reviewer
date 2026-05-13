# app/pipeline/orchestrator.py
"""
Async pipeline orchestrator (pipeline step coordinator).

This module is the authoritative index of pipeline execution order.
It contains almost no logic of its own — each step is fully implemented
in its dedicated submodule. Reading this file should read like the
CLAUDE.md pipeline specification.

Pipeline order (do not reorder — matches CLAUDE.md exactly):
  1.  fetch_pr_files()        — PyGitHub, returns per-file (raw_diff, file_content, filename)
  2.  parse_file_diff()       — diff_parser.py, called per-file → Optional[FileDiff]
                                None return = file skipped (lock file, binary, migration).
                                Skip logic lives entirely in diff_parser._should_skip.
  3.  review_all_files()      — for each FileDiff:
                                  a. run_static_analysis()  → ToolFindings
                                  b. review_file()          → ReviewResult (Groq + LangChain)
                                  c. guardrail_check()      → GuardrailResult
  4.  post_review_comments()  — inline comments + summary
  5.  save_review()           — Review row flushed first (to get id), then
                                FilteredIssue rows written, then single commit.

Error handling (propagation contract):
  GroqRateLimitError  → propagates to review_task.py → self.retry(countdown=60)
  GithubException     → propagates to review_task.py → task FAILED
  Any other exception → propagates to review_task.py → task FAILED
"""
import logging
from typing import List

from app.pipeline.db_writer import save_review
from app.pipeline.file_reviewer import review_all_files
from app.pipeline.github_fetcher import fetch_pr_files
from app.pipeline.summary_builder import build_summary, compute_overall_verdict
from app.schemas.diff import FileDiff
from app.services.diff_parser import parse_file_diff
from app.services.github_poster import post_review_comments

logger = logging.getLogger(__name__)


async def async_pipeline(repo_full_name: str, pr_number: int, job_id: str) -> None:
    """
    Full PR review pipeline. Called via asyncio.run() from the Celery task.

    This function is module-level (not a nested closure) so it can be tested
    and reasoned about independently of the Celery machinery.

    Args:
        repo_full_name: GitHub repository full name, e.g. "org/repo".
        pr_number:      Pull request number.
        job_id:         Celery task request ID (stored in the Review record).

    Raises:
        GroqRateLimitError  — propagates for Celery retry.
        GithubException     — propagates; marks task FAILED.
        Any other exception — propagates; marks task FAILED.
    """
    logger.info("Pipeline start: %s PR #%d", repo_full_name, pr_number)

    # Step 1: Fetch per-file data from GitHub (synchronous — PyGitHub).
    file_data, _head_sha = fetch_pr_files(repo_full_name, pr_number)

    if not file_data:
        logger.info("No files in PR %s #%d — nothing to review", repo_full_name, pr_number)
        return

    # Step 2: Parse each file.
    # parse_file_diff() handles all skip logic internally (_should_skip).
    # None return = file skipped (lock file, binary, migration, unsupported ext).
    pattern_skipped: List[str] = []
    file_diffs: List[FileDiff] = []

    for raw_diff, file_content, filename in file_data:
        parsed = parse_file_diff(raw_diff, file_content, filename)
        if parsed is None:
            logger.info("Skipping file (parse_file_diff returned None): %s", filename)
            pattern_skipped.append(filename)
        else:
            file_diffs.append(parsed)

    logger.info(
        "%d files to review, %d skipped by diff parser",
        len(file_diffs), len(pattern_skipped),
    )

    if not file_diffs:
        logger.info("No reviewable files after parsing — skipping LLM phase")
        return

    # Step 3: Per-file review loop (static analysis → LLM → guardrail).
    outcomes, agent_skipped, all_filtered_issues = await review_all_files(file_diffs)

    # Step 4: Post GitHub comments (inline + summary).
    overall_verdict = compute_overall_verdict(outcomes)
    summary = build_summary(
        outcomes, overall_verdict, pattern_skipped, agent_skipped, repo_full_name, pr_number,
    )
    post_result = post_review_comments(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        file_results=[o.guardrail_result for o in outcomes if o.guardrail_result],
        summary=summary,
    )
    logger.info(
        "GitHub post complete: %d inline, %d failed, summary=%s",
        post_result.inline_posted,
        post_result.inline_failed,
        post_result.summary_posted,
    )

    # Step 5: Persist Review record + filtered issues (atomic commit).
    review_id = await save_review(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        job_id=job_id,
        outcomes=outcomes,
        post_result=post_result,
        pattern_skipped=pattern_skipped,
        agent_skipped=agent_skipped,
        file_diffs=file_diffs,
        summary=summary,
        overall_verdict=overall_verdict,
        all_filtered_issues=all_filtered_issues,
    )

    logger.info(
        "Pipeline complete: %s PR #%d (review_id=%d)",
        repo_full_name, pr_number, review_id,
    )
