"""
Guardrail layer — runs between LLM output and GitHub comment poster.

Three checks, in order, for every issue in a ReviewResult:

  1. Grounding check
     evidence_from_tool must reference a known tool name from VALID_TOOL_NAMES.
     Issues where the LLM invents evidence or uses vague language are filtered.

  2. Line number validation
     The flagged line_number must exist in the diff's added/changed lines.
     LLMs hallucinate line numbers. Every one is validated.

  3. Confidence gating
     If ReviewResult.confidence < 0.6, each issue's severity is downgraded
     one level and "Low confidence: " is prepended to the message.
     The issue is NOT filtered — it is posted with a caveat.

Filtered issues are returned in GuardrailResult.filtered_issues and must be
persisted to the database via log_filtered_issues() before the caller posts
any GitHub comments.

This module never raises. It always returns a GuardrailResult.
The caller is responsible for deciding what to do with filtered issues.

CRITICAL: Do not add a bypass flag accessible at runtime in production.
The only permitted bypass is:
    if settings.ENVIRONMENT == "testing" and settings.BYPASS_GUARDRAILS:
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import List, Set

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.filtered_issue import FilteredIssue
from app.schemas.diff import FileDiff
from app.schemas.review import Issue, ReviewResult, Severity
from app.utils.constants import VALID_TOOL_NAMES

logger = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 0.6

_SEVERITY_DOWNGRADE: dict[Severity, Severity] = {
    Severity.HIGH: Severity.MEDIUM,
    Severity.MEDIUM: Severity.LOW,
    Severity.LOW: Severity.LOW,  # floor — cannot go lower
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class FilteredIssueData:
    """
    Holds a single issue that was filtered by the guardrail, along with the
    reason it was filtered and the file it came from.

    Named FilteredIssueData to avoid collision with the ORM model FilteredIssue.
    """
    issue: dict          # raw issue dict (Issue.model_dump())
    filter_reason: str   # human-readable, logged to DB
    file: str            # filename from FileDiff


@dataclass
class GuardrailResult:
    passed_issues: List[Issue] = field(default_factory=list)
    filtered_issues: List[FilteredIssueData] = field(default_factory=list)
    original_confidence: float = 0.0
    applied_confidence_gating: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def guardrail_check(
    review: ReviewResult,
    file_diff: FileDiff,
) -> GuardrailResult:
    """
    Run all three guardrail checks on a ReviewResult for one file.

    Args:
        review:    The ReviewResult returned by the LLM.
        file_diff: The parsed diff for the same file, providing valid line numbers.

    Returns:
        GuardrailResult — always. Never raises.

    The caller must:
      1. Call log_filtered_issues() with result.filtered_issues.
      2. Only post result.passed_issues to GitHub.
    """
    result = GuardrailResult(original_confidence=review.confidence)
    valid_line_numbers: Set[int] = _get_valid_line_numbers(file_diff)

    for issue in review.issues:

        # -- Check 1: Grounding --------------------------------------------------
        grounding_failure = _check_grounding(issue)
        if grounding_failure:
            result.filtered_issues.append(FilteredIssueData(
                issue=issue.model_dump(),
                filter_reason=f"grounding_failure: {grounding_failure}",
                file=file_diff.filename,
            ))
            logger.info(
                "FILTERED [grounding] %s:%d — %s",
                file_diff.filename,
                issue.line_number,
                grounding_failure,
            )
            continue

        # -- Check 2: Line number validation ------------------------------------
        if issue.line_number not in valid_line_numbers:
            valid_range = (
                f"{min(valid_line_numbers)}-{max(valid_line_numbers)}"
                if valid_line_numbers
                else "empty diff"
            )
            result.filtered_issues.append(FilteredIssueData(
                issue=issue.model_dump(),
                filter_reason=(
                    f"line_not_in_diff: line {issue.line_number} "
                    f"not in changed lines (valid range: {valid_range})"
                ),
                file=file_diff.filename,
            ))
            logger.info(
                "FILTERED [line_validation] %s:%d — line not in diff (valid: %s)",
                file_diff.filename,
                issue.line_number,
                valid_range,
            )
            continue

        # -- Check 3: Confidence gating (modifies, does not filter) -------------
        if review.confidence < _CONFIDENCE_THRESHOLD:
            issue = _apply_confidence_gating(issue)
            result.applied_confidence_gating = True

        result.passed_issues.append(issue)

    logger.info(
        "Guardrail result for %s: %d passed, %d filtered (confidence=%.2f%s)",
        file_diff.filename,
        len(result.passed_issues),
        len(result.filtered_issues),
        review.confidence,
        ", gating applied" if result.applied_confidence_gating else "",
    )
    return result


async def log_filtered_issues(
    review_id: int,
    filtered: List[FilteredIssueData],
    db: AsyncSession,
) -> None:
    """
    Persist all filtered issues to the database for audit and dashboard display.

    IMPORTANT: This function does NOT commit the session. The caller
    (review_task.py) is responsible for committing. This keeps the filtered
    issue write in the same transaction as the parent review record save,
    so the two are never out of sync.

    Args:
        review_id: Primary key of the Review record this belongs to.
        filtered:  List of FilteredIssueData from guardrail_check().
        db:        Open async session. Do not commit here.
    """
    if not filtered:
        return

    records = [
        FilteredIssue(
            review_id=review_id,
            issue_data=json.dumps(item.issue),
            filter_reason=item.filter_reason,
            file=item.file,
        )
        for item in filtered
    ]
    db.add_all(records)
    logger.info(
        "Staged %d filtered issue(s) for review_id=%d (awaiting caller commit)",
        len(records),
        review_id,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _check_grounding(issue: Issue) -> str | None:
    """
    Return a failure reason string if the issue fails the grounding check.
    Return None if it passes.

    Passing condition: evidence_from_tool (already validated non-empty by
    Pydantic) must contain at least one name from VALID_TOOL_NAMES (case-
    insensitive). The Pydantic validator ensures the string is at least 10
    characters — this check ensures it actually references a real tool.
    """
    evidence_lower = issue.evidence_from_tool.lower()
    if not any(tool in evidence_lower for tool in VALID_TOOL_NAMES):
        return (
            f"evidence does not reference a known tool "
            f"(must mention one of: {', '.join(sorted(VALID_TOOL_NAMES))}). "
            f"Got: {issue.evidence_from_tool[:80]!r}"
        )
    return None


def _get_valid_line_numbers(file_diff: FileDiff) -> Set[int]:
    """
    Return the set of line numbers that were added or changed in the diff.

    Only added/modified lines are reviewable. Context lines (unchanged
    surrounding lines) are not valid targets for inline comments.
    """
    return set(file_diff.added_line_numbers)


def _apply_confidence_gating(issue: Issue) -> Issue:
    """
    Downgrade severity by one level and prepend a low-confidence prefix.

    Returns a new Issue via model_copy — does not mutate the original.
    Pydantic models must be treated as immutable throughout the pipeline.
    """
    return issue.model_copy(update={
        "severity": _SEVERITY_DOWNGRADE[issue.severity],
        "message": f"Low confidence: {issue.message}",
    })
