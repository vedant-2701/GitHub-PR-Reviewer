# app/tasks/review_task.py
"""
Celery task: full PR review pipeline.

Pipeline order (matches CLAUDE.md exactly — do not reorder):
1.  validate_webhook_signature()      — done in webhook router before task is queued
2.  _fetch_pr_files()                 — PyGitHub, returns per-file (raw_diff, file_content, filename)
3.  parse_file_diff()                 — diff_parser.py, called per-file → Optional[FileDiff]
                                        None return = file is skipped (lock file, binary, migration)
                                        Skip logic lives entirely in diff_parser._should_skip —
                                        no duplicate filtering here.
4.  for each FileDiff:
    a. run_static_analysis()          → ToolFindings
    b. review_file()                  → ReviewResult  (Groq + LangChain)
    c. guardrail_check()              → GuardrailResult
    d. (filtered issues collected; written to DB after Review row exists — see step 6)
5.  post_github_comments()            → inline + summary
6.  save_review_to_db() + log_filtered_issues()
                                      → Review row inserted first (db.flush() to get id),
                                        then filtered issues written with real review_id,
                                        then single db.commit().

Error handling:
- GroqRateLimitError  → self.retry(countdown=60, max_retries=3)
- ReviewAgentError    → log + skip file, continue with rest, note in summary
- GithubException     → log + raise (fatal — no point retrying without investigation)
- Any unhandled       → log + raise (Celery marks task FAILED)

Async boundary:
- Celery tasks are synchronous. All async work runs inside _async_pipeline(),
  called via asyncio.run() at the task boundary.
- _async_pipeline() is module-level (not a nested closure) per project convention.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from celery import Task
from github import GithubException, GithubIntegration, Github

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.review import Review, VerdictEnum
from app.schemas.review import Issue, ReviewResult
from app.schemas.diff import FileDiff
from app.services.diff_parser import parse_file_diff
from app.services.guardrail import GuardrailResult, guardrail_check, log_filtered_issues, FilteredIssueData
from app.services.github_poster import post_review_comments
from app.services.review_agent import ReviewAgentError, review_file
from app.services.static_analysis import run_static_analysis
from app.utils.constants import INTER_FILE_DELAY
from app.utils.groq_client import GroqRateLimitError
from celery_worker import celery_app

logger = logging.getLogger(__name__)

settings = get_settings()


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------

@dataclass
class IssueWithPath:
    """
    Wraps a guardrail-passed Issue with its source file path.

    Issue (locked schema) does not carry file_path — the LLM returns issues
    scoped to a single file and doesn't know the path. review_task.py does.
    github_poster.py reads issue.file_path via this wrapper.
    """
    line_number: int
    type: str
    severity: str
    message: str
    suggestion: str
    evidence_from_tool: str
    file_path: str  # injected here, not from LLM

    @classmethod
    def from_issue(cls, issue: Issue, file_path: str) -> "IssueWithPath":
        return cls(
            line_number=issue.line_number,
            type=issue.type,
            severity=issue.severity,
            message=issue.message,
            suggestion=issue.suggestion,
            evidence_from_tool=issue.evidence_from_tool,
            file_path=file_path,
        )


@dataclass
class FileReviewOutcome:
    file_diff: FileDiff
    guardrail_result: Optional[GuardrailResult] = None
    skipped: bool = False
    skip_reason: str = ""


# ---------------------------------------------------------------------------
# GitHub helpers (synchronous — PyGitHub is not async)
# ---------------------------------------------------------------------------

def _get_installation_client() -> Github:
    """
    Return a GitHub client authenticated as the App installation.
    Raises FileNotFoundError (missing .pem) or GithubException (bad auth).
    Both are fatal — let them propagate to the task.
    """
    integration = GithubIntegration(
        integration_id=settings.GITHUB_APP_ID,
        private_key=settings.GITHUB_PRIVATE_KEY,
    )
    installations = integration.get_installations()
    installation = next(iter(installations), None)
    if installation is None:
        raise GithubException(
            status=401,
            data={"message": "No GitHub App installation found."},
        )
    return installation.get_github_for_installation()


def _fetch_pr_files(
    repo_full_name: str,
    pr_number: int,
) -> tuple[List[tuple[str, str, str]], str]:
    """
    Fetch per-file diff data from GitHub.

    Returns:
        (file_data, head_sha) where file_data is a list of
        (raw_diff, file_content, filename) tuples — one per file in the PR.

        raw_diff:     Unified diff string with standard --- / +++ headers.
                      parse_file_diff() expects this exact format.
        file_content: Full file content at PR HEAD (for context_lines).
                      Empty string for deleted files or files we cannot decode.
        filename:     File path as reported by GitHub (e.g. "backend/app/main.py").

    Binary files have no patch — raw_diff is set to the "Binary files ... differ"
    string so parse_file_diff()._should_skip() detects and skips them correctly.

    GithubException propagates — caller marks task FAILED.
    """
    gh = _get_installation_client()
    repo = gh.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)
    head_sha = pr.head.sha

    file_data: List[tuple[str, str, str]] = []

    for gh_file in pr.get_files():
        filename = gh_file.filename

        # Build raw_diff in the format parse_file_diff() expects.
        if gh_file.patch:
            raw_diff = (
                f"--- a/{filename}\n"
                f"+++ b/{filename}\n"
                f"{gh_file.patch}"
            )
        elif gh_file.status == "removed":
            # Deleted file — empty added_line_numbers, guardrail filters everything.
            raw_diff = f"--- a/{filename}\n+++ /dev/null\n"
        else:
            # Binary file — triggers _should_skip() in parse_file_diff().
            raw_diff = f"Binary files a/{filename} and b/{filename} differ"

        # Fetch full file content at HEAD for context_lines.
        # Deleted files and binary files get empty string.
        file_content = ""
        if gh_file.status != "removed" and gh_file.patch:
            try:
                content_obj = repo.get_contents(filename, ref=head_sha)
                file_content = content_obj.decoded_content.decode("utf-8", errors="replace")
            except GithubException as e:
                # File in diff but not fetchable (permissions, race condition).
                # Context lines will be empty — acceptable, review still proceeds.
                logger.warning(
                    "Could not fetch content for %s (status=%d): %s — using empty content",
                    filename, e.status, e.data.get("message", str(e)),
                )
            except UnicodeDecodeError:
                # Binary content despite having a patch (edge case).
                logger.warning("UnicodeDecodeError reading %s — treating as binary", filename)
                raw_diff = f"Binary files a/{filename} and b/{filename} differ"

        file_data.append((raw_diff, file_content, filename))

    return file_data, head_sha


# ---------------------------------------------------------------------------
# Summary builder and helpers
# ---------------------------------------------------------------------------

def _compute_overall_verdict(outcomes: List[FileReviewOutcome]) -> VerdictEnum:
    """Determine the overall verdict across all file review outcomes."""
    has_medium = False
    for o in outcomes:
        if o.guardrail_result and o.guardrail_result.passed_issues:
            severities = {i.severity for i in o.guardrail_result.passed_issues}
            if "HIGH" in severities:
                return VerdictEnum.REQUEST_CHANGES
            elif "MEDIUM" in severities:
                has_medium = True

    if has_medium:
        return VerdictEnum.COMMENT
    return VerdictEnum.APPROVE


def _build_summary(
    outcomes: List[FileReviewOutcome],
    overall_verdict: VerdictEnum,
    pattern_skipped: List[str],
    agent_skipped: List[str],
    repo_full_name: str,
    pr_number: int,
) -> str:
    """
    Build the PR-level summary comment. Caller passes this to post_review_comments().
    All aggregation lives here — github_poster.py is I/O only.

    pattern_skipped: filenames where parse_file_diff() returned None
                     (lock files, binary, migrations — skipped before LLM)
    agent_skipped:   filenames where static analysis or review_file() raised
                     (skipped mid-pipeline, noted as ⚠️ in summary)
    """
    total_passed = sum(
        len(o.guardrail_result.passed_issues)
        for o in outcomes
        if o.guardrail_result
    )
    total_filtered = sum(
        len(o.guardrail_result.filtered_issues)
        for o in outcomes
        if o.guardrail_result
    )
    files_reviewed = sum(1 for o in outcomes if o.guardrail_result)
    confidence_scores = [
        o.guardrail_result.original_confidence
        for o in outcomes
        if o.guardrail_result
    ]
    avg_confidence = (
        sum(confidence_scores) / len(confidence_scores)
        if confidence_scores else 0.0
    )
    confidence_gated = sum(
        1 for o in outcomes
        if o.guardrail_result and o.guardrail_result.applied_confidence_gating
    )

    # Determine overall verdict presentation string
    if overall_verdict == VerdictEnum.REQUEST_CHANGES:
        overall = "🔴 REQUEST CHANGES"
    elif overall_verdict == VerdictEnum.COMMENT:
        overall = "🟡 COMMENT"
    else:
        overall = "🟢 APPROVE"

    lines = [
        f"## ACE Code Review — {repo_full_name} PR #{pr_number}",
        "",
        f"**Verdict:** {overall}",
        "",
        "### Review Stats",
        f"- Files reviewed: **{files_reviewed}**",
        f"- Issues posted: **{total_passed}**",
        f"- Issues filtered by guardrail: **{total_filtered}**",
        f"- Average LLM confidence: **{avg_confidence:.0%}**",
    ]

    if confidence_gated:
        lines.append(
            f"- Files with confidence gating applied: **{confidence_gated}** "
            "(severity downgraded, messages prefixed with 'Low confidence:')"
        )

    if pattern_skipped:
        lines += [
            "",
            f"### ⏭ {len(pattern_skipped)} file(s) skipped (lock files / binary / minified)",
            *[f"  - `{f}`" for f in pattern_skipped],
        ]

    if agent_skipped:
        lines += [
            "",
            f"### ⚠️ {len(agent_skipped)} file(s) skipped due to agent error",
            *[f"  - `{f}`" for f in agent_skipped],
            "",
            "_These files were not reviewed. Check worker logs for details._",
        ]

    lines += [
        "",
        "---",
        "_Posted by ACE Code Review Agent · Guardrail layer active_",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core async pipeline
# ---------------------------------------------------------------------------

async def _async_pipeline(repo_full_name: str, pr_number: int, job_id: str) -> None:
    """
    Full review pipeline. Called via asyncio.run() from the Celery task.

    This function is module-level (not a nested closure) so it can be
    tested and reasoned about independently of the Celery machinery.

    Raises:
        GroqRateLimitError  — propagates to task, triggers self.retry()
        GithubException     — propagates to task, marks FAILED
        Any other exception — propagates to task, marks FAILED
    """
    logger.info("Pipeline start: %s PR #%d", repo_full_name, pr_number)

    # Step 2: Fetch per-file data from GitHub (synchronous — PyGitHub)
    file_data, _head_sha = _fetch_pr_files(repo_full_name, pr_number)

    if not file_data:
        logger.info("No files in PR %s #%d — nothing to review", repo_full_name, pr_number)
        return

    # Step 3: Parse each file.
    # parse_file_diff() handles all skip logic internally (_should_skip).
    # None return = file skipped (lock file, binary, migration, unsupported extension).
    # No separate filter_skip_files() step — that would duplicate diff_parser logic.
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

    # Step 4: Per-file review loop
    outcomes: List[FileReviewOutcome] = []
    agent_skipped: List[str] = []
    # Collect all filtered issues here. Written to DB after Review row exists (step 6)
    # so every FilteredIssue gets the real review_id, not a placeholder.
    all_filtered_issues: List[FilteredIssueData] = []

    for i, file_diff in enumerate(file_diffs):
        if i > 0:
            await asyncio.sleep(INTER_FILE_DELAY)

        logger.info(
            "Reviewing file %d/%d: %s",
            i + 1, len(file_diffs), file_diff.filename,
        )

        outcome = FileReviewOutcome(file_diff=file_diff)

        # Step 4a: Static analysis
        try:
            tool_findings = await run_static_analysis(file_diff)
        except Exception as e:
            logger.error(
                "Static analysis failed for %s: %s — skipping file",
                file_diff.filename, e,
            )
            outcome.skipped = True
            outcome.skip_reason = f"static_analysis_error: {e}"
            agent_skipped.append(file_diff.filename)
            outcomes.append(outcome)
            continue

        # Step 4b: LLM review
        # GroqRateLimitError is NOT caught here — propagates to Celery task
        # which retries the whole job. Correct: retry from scratch with a fresh
        # delay rather than post partial results to GitHub.
        try:
            review_result: ReviewResult = await review_file(file_diff, tool_findings)
        except GroqRateLimitError:
            logger.warning(
                "Groq rate limit hit on file %s — propagating for task retry",
                file_diff.filename,
            )
            raise
        except ReviewAgentError as e:
            logger.error(
                "ReviewAgentError on %s: %s — skipping file",
                file_diff.filename, e,
            )
            outcome.skipped = True
            outcome.skip_reason = f"review_agent_error: {e}"
            agent_skipped.append(file_diff.filename)
            outcomes.append(outcome)
            continue

        # Step 4c: Guardrail
        guardrail_result = guardrail_check(review_result, file_diff)

        # Attach file_path to each passed issue for github_poster.py.
        # Issue (locked schema) has no file_path — injected here via IssueWithPath.
        guardrail_result.passed_issues = [
            IssueWithPath.from_issue(issue, file_diff.filename)
            for issue in guardrail_result.passed_issues
        ]

        outcome.guardrail_result = guardrail_result
        outcomes.append(outcome)

        # Collect filtered issues — do NOT write to DB yet.
        # Review row must be inserted and flushed first so we have a real id.
        all_filtered_issues.extend(guardrail_result.filtered_issues)

    # Step 5: Post GitHub comments
    overall_verdict_enum = _compute_overall_verdict(outcomes)
    summary = _build_summary(outcomes, overall_verdict_enum, pattern_skipped, agent_skipped, repo_full_name, pr_number)
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

    # Step 6: Save Review row, then write filtered issues with the real review_id.
    # Order matters:
    #   db.add(review_record)
    #   db.flush()            → assigns review_record.id (no commit yet)
    #   log_filtered_issues() → inserts FilteredIssue rows with review_record.id
    #   db.commit()           → commits everything atomically
    _total_passed = sum(
        len(o.guardrail_result.passed_issues)
        for o in outcomes if o.guardrail_result
    )
    total_filtered = sum(
        len(o.guardrail_result.filtered_issues)
        for o in outcomes if o.guardrail_result
    )

    # Confidence for DB record
    confidence_scores = [
        o.guardrail_result.original_confidence
        for o in outcomes if o.guardrail_result
    ]
    avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0

    async with AsyncSessionLocal() as db:
        review_record = Review(
            repo=repo_full_name,
            pr_number=pr_number,
            job_id=job_id,
            verdict=overall_verdict_enum,
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

        # Write all filtered issues with the real review_id.
        await log_filtered_issues(
            review_id=review_record.id,
            filtered=all_filtered_issues,
            db=db,
        )

        await db.commit()
        logger.info(
            "Review saved to DB: id=%d, posted=%d, filtered=%d",
            review_record.id, post_result.inline_posted, total_filtered,
        )

    logger.info("Pipeline complete: %s PR #%d", repo_full_name, pr_number)


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    max_retries=3,
    name="app.tasks.review_task.process_pr_review",
)
def process_pr_review(self: Task, repo_full_name: str, pr_number: int) -> None:
    """
    Celery entry point. Synchronous wrapper around _async_pipeline().

    Retry policy:
    - GroqRateLimitError → retry after 60s (max 3 retries)
    - All other exceptions → mark FAILED, do not retry
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
        asyncio.run(_async_pipeline(repo_full_name, pr_number, self.request.id))
    except GroqRateLimitError as exc:
        logger.warning(
            "Groq rate limit — scheduling retry %d/%d in 60s for Job ID: %s | %s PR #%d",
            self.request.retries + 1,
            self.max_retries,
            job_id,
            repo_full_name,
            pr_number,
        )
        raise self.retry(exc=exc, countdown=60)
    except GithubException as exc:
        logger.error(
            "GitHub API error for JOB ID: %s | %s PR #%d: status=%d message=%s — task FAILED",
            job_id,
            repo_full_name,
            pr_number,
            exc.status,
            exc.data.get("message", str(exc)),
        )
        raise  # do not retry — GitHub errors need investigation
    except Exception:
        logger.exception(
            "Unhandled error in pipeline for Job ID: %s | %s PR #%d — task FAILED",
            job_id,
            repo_full_name,
            pr_number,
        )
        raise
