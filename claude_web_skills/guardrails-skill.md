---
name: guardrails
description: Use when writing or modifying the guardrail layer, confidence gating, issue filtering, grounding checks, or anything that sits between the LLM output and the GitHub comment poster. This is the most critical component — never simplify or bypass it.
---

# Guardrail Layer Skill — ACE Code Review Agent

## Use this skill when
- Writing or modifying `app/services/guardrail.py`
- Adding new filter rules
- Writing tests for the guardrail
- Logging filtered issues to the database
- Debugging why an issue was filtered

## Do not use this skill when
- Working on the LLM agent (that's groq-agent skill)
- Working on GitHub comment posting

---

## Why This Layer Exists

LLMs hallucinate. Specifically they:
1. Flag issues they cannot attribute to any real tool finding
2. Cite line numbers that don't exist in the diff
3. Generate low-confidence output with the same severity as high-confidence output

The guardrail layer catches all three before anything reaches GitHub. Without it, the tool posts garbage at production rate.

**This is also the most impressive feature to demonstrate. Never simplify it to ship faster.**

---

## Complete Guardrail Implementation

```python
# app/services/guardrail.py
import logging
import re
from dataclasses import dataclass, field
from typing import List, Set
from app.schemas.review import ReviewResult, Issue, Severity
from app.services.diff_parser import FileDiff

logger = logging.getLogger(__name__)

# Tool names the evidence_from_tool field must reference
VALID_TOOL_NAMES = {"bandit", "radon", "ast", "eslint"}

@dataclass
class FilteredIssue:
    issue: dict           # raw issue data
    filter_reason: str    # human-readable reason
    file: str

@dataclass
class GuardrailResult:
    passed_issues: List[Issue] = field(default_factory=list)
    filtered_issues: List[FilteredIssue] = field(default_factory=list)
    original_confidence: float = 0.0
    applied_confidence_gating: bool = False


def guardrail_check(
    review: ReviewResult,
    file_diff: FileDiff,
) -> GuardrailResult:
    """
    Run all 3 guardrail checks on a ReviewResult for one file.
    
    Checks:
    1. Grounding: evidence_from_tool must reference a real tool name
    2. Line validation: flagged line must exist in the diff
    3. Confidence gating: low confidence → severity downgrade + prefix
    
    Returns GuardrailResult with passed and filtered issues.
    Does NOT raise — always returns a result. Caller logs filtered issues.
    """
    result = GuardrailResult(original_confidence=review.confidence)
    valid_line_numbers = _get_valid_line_numbers(file_diff)

    for issue in review.issues:
        # CHECK 1: Grounding check
        grounding_failure = _check_grounding(issue)
        if grounding_failure:
            result.filtered_issues.append(FilteredIssue(
                issue=issue.model_dump(),
                filter_reason=f"grounding_failure: {grounding_failure}",
                file=file_diff.filename,
            ))
            logger.info(
                "FILTERED [grounding] %s:%d — %s",
                file_diff.filename, issue.line_number, grounding_failure
            )
            continue

        # CHECK 2: Line number validation
        if issue.line_number not in valid_line_numbers:
            result.filtered_issues.append(FilteredIssue(
                issue=issue.model_dump(),
                filter_reason=f"line_not_in_diff: line {issue.line_number} not in changed lines",
                file=file_diff.filename,
            ))
            logger.info(
                "FILTERED [line_validation] %s:%d — line not in diff (valid range: %s)",
                file_diff.filename, issue.line_number,
                f"{min(valid_line_numbers)}-{max(valid_line_numbers)}" if valid_line_numbers else "empty"
            )
            continue

        # CHECK 3: Confidence gating (does not filter — modifies and passes)
        if review.confidence < 0.6:
            issue = _apply_confidence_gating(issue)
            result.applied_confidence_gating = True

        result.passed_issues.append(issue)

    logger.info(
        "Guardrail result for %s: %d passed, %d filtered (confidence=%.2f)",
        file_diff.filename,
        len(result.passed_issues),
        len(result.filtered_issues),
        review.confidence,
    )
    return result


def _check_grounding(issue: Issue) -> str | None:
    """
    Returns a failure reason string if the issue fails grounding.
    Returns None if it passes.
    """
    evidence = issue.evidence_from_tool.lower().strip()
    
    # Already validated non-empty by Pydantic, but check tool name reference
    if not any(tool in evidence for tool in VALID_TOOL_NAMES):
        return (
            f"evidence does not reference a known tool "
            f"(must mention one of: {', '.join(VALID_TOOL_NAMES)}). "
            f"Got: '{issue.evidence_from_tool[:80]}'"
        )
    
    return None


def _get_valid_line_numbers(file_diff: FileDiff) -> Set[int]:
    """
    Extract the set of line numbers that were actually changed in the diff.
    Only added/modified lines are reviewable — context lines are not.
    """
    return set(file_diff.added_line_numbers)


def _apply_confidence_gating(issue: Issue) -> Issue:
    """
    Downgrade severity and prepend warning prefix.
    Returns a modified copy — does not mutate in place.
    """
    severity_downgrade = {
        Severity.HIGH: Severity.MEDIUM,
        Severity.MEDIUM: Severity.LOW,
        Severity.LOW: Severity.LOW,  # can't go lower
    }
    return issue.model_copy(update={
        "severity": severity_downgrade[issue.severity],
        "message": f"Low confidence: {issue.message}",
    })
```

---

## Logging Filtered Issues to DB

```python
# app/services/guardrail.py (continued)
import json
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.filtered_issue import FilteredIssueRecord

async def log_filtered_issues(
    review_id: int,
    filtered: List[FilteredIssue],
    db: AsyncSession,
) -> None:
    """
    Persist all filtered issues to the database for audit and dashboard display.
    Called after guardrail_check(), before posting GitHub comments.
    """
    if not filtered:
        return
    
    records = [
        FilteredIssueRecord(
            review_id=review_id,
            issue_data=json.dumps(f.issue),
            filter_reason=f.filter_reason,
            file=f.file,
        )
        for f in filtered
    ]
    db.add_all(records)
    # Session commit handled by the caller (review_task.py)
    logger.info("Logged %d filtered issues for review %d", len(records), review_id)
```

---

## FileDiff Schema (needed by guardrail)

The guardrail needs `added_line_numbers` from the diff parser. Ensure `FileDiff` exposes this:

```python
# app/schemas/diff.py
from pydantic import BaseModel
from typing import List, Set

class FileDiff(BaseModel):
    filename: str
    language: str           # "python" | "javascript" | "typescript" | "unknown"
    added_line_numbers: List[int]    # line numbers of added/changed lines
    raw_diff: str           # full unified diff text for this file
    context_lines: str      # ±20 lines of surrounding context
```

---

## Test Requirements for Guardrail

Every possible filter path must have a test. This is not optional.

```python
# tests/test_guardrail.py

def test_grounding_check_no_tool_name_filters_issue():
    """Issue with evidence not mentioning any tool is filtered."""
    ...

def test_grounding_check_bandit_reference_passes():
    """Issue with 'bandit: B608 SQL injection' in evidence passes."""
    ...

def test_line_validation_nonexistent_line_filters_issue():
    """Issue flagging line 999 when diff only has lines 10-50 is filtered."""
    ...

def test_line_validation_valid_line_passes():
    """Issue flagging a line that exists in added_line_numbers passes."""
    ...

def test_confidence_gating_below_threshold_downgrades_severity():
    """confidence=0.4 → HIGH becomes MEDIUM, message prefixed."""
    ...

def test_confidence_gating_above_threshold_no_change():
    """confidence=0.8 → severity unchanged, no prefix."""
    ...

def test_confidence_gating_does_not_filter_issue():
    """Low confidence issues are still posted — just modified. Not dropped."""
    ...

def test_filtered_issues_logged_to_db():
    """Filtered issues are written to DB with correct filter_reason."""
    ...

def test_guardrail_clean_review_passes_all():
    """A well-formed ReviewResult with good evidence and valid lines passes fully."""
    ...
```

---

## Dashboard Stats Endpoint

The frontend's right panel shows guardrail stats. The backend must expose:

```
GET /api/logs/stats

Response:
{
  "data": {
    "total_issues_found": 142,
    "filtered_grounding": 23,
    "filtered_line_validation": 11,
    "applied_confidence_gating": 18,
    "average_confidence": 0.74
  }
}
```

This is the "responsible AI proof" — show it explicitly during any demo or review.

---

## What NEVER to Do

```python
# WRONG — skipping guardrail and posting directly
await post_github_comments(review.issues, pr)

# WRONG — making guardrail optional with a flag
if not settings.SKIP_GUARDRAILS:
    result = guardrail_check(review, diff)

# WRONG — only running one of the three checks
def weak_guardrail(review):
    return [i for i in review.issues if i.evidence_from_tool]
    # ^ misses line validation and confidence gating

# WRONG — silently dropping filter reason
result.filtered_issues.append({"issue": issue})  # no filter_reason

# WRONG — mutating the issue in place
issue.severity = new_severity  # Pydantic models should be treated as immutable
# use issue.model_copy(update={...}) instead
```