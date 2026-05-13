"""
Tests for app/services/guardrail.py

All three guardrail checks are tested: grounding, line validation,
and confidence gating. The DB logging function is tested with a mocked
async session.

No external dependencies (no Groq, no GitHub, no real DB).
FileDiff and ReviewResult instances are constructed inline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.diff import FileDiff
from app.schemas.review import Issue, IssueType, ReviewResult, Severity, Verdict
from app.services.guardrail import (
    FilteredIssueData,
    GuardrailResult,
    guardrail_check,
    log_filtered_issues,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_issue(
    line_number: int = 10,
    severity: Severity = Severity.HIGH,
    message: str = "SQL injection risk.",
    evidence: str = "bandit: B608 [HIGH/MEDIUM] SQL injection line 10",
) -> Issue:
    return Issue(
        line_number=line_number,
        type=IssueType.SECURITY,
        severity=severity,
        message=message,
        suggestion="Use parameterised queries.",
        evidence_from_tool=evidence,
    )


def _make_file_diff(added_lines: list[int] | None = None) -> FileDiff:
    if added_lines is None:
        added_lines = [10, 11, 12, 42]
    return FileDiff(
        filename="app/auth.py",
        language="python",
        added_line_numbers=added_lines,
        raw_diff="@@ -8,2 +8,4 @@\n+    bad_code()\n",
        context_lines="def authenticate():\n    pass\n",
    )


def _make_review(
    issues: list[Issue] | None = None,
    confidence: float = 0.9,
) -> ReviewResult:
    if issues is None:
        issues = [_make_issue()]
    return ReviewResult(
        issues=issues,
        summary="One issue found.",
        overall_verdict=Verdict.REQUEST_CHANGES,
        confidence=confidence,
        files_reviewed=["app/auth.py"],
    )


# ---------------------------------------------------------------------------
# Grounding check tests
# ---------------------------------------------------------------------------

def test_grounding_check_no_tool_name_filters_issue():
    """
    Issue whose evidence_from_tool does not mention any known tool name
    must be filtered with reason 'grounding_failure'.
    """
    issue = _make_issue(evidence="the code looks complex and might have issues")
    review = _make_review(issues=[issue])
    file_diff = _make_file_diff()

    result = guardrail_check(review, file_diff)

    assert len(result.passed_issues) == 0
    assert len(result.filtered_issues) == 1
    assert "grounding_failure" in result.filtered_issues[0].filter_reason


def test_grounding_check_bandit_reference_passes():
    """
    Issue whose evidence_from_tool mentions 'bandit' passes the grounding check.
    """
    issue = _make_issue(evidence="bandit: B608 [HIGH/MEDIUM] SQL injection line 10")
    review = _make_review(issues=[issue])
    file_diff = _make_file_diff()

    result = guardrail_check(review, file_diff)

    assert len(result.passed_issues) == 1
    assert len(result.filtered_issues) == 0


def test_grounding_check_flake8_reference_passes():
    """
    flake8 is a valid tool name and must pass the grounding check.
    Regression guard — flake8 replaced ast in VALID_TOOL_NAMES this session.
    """
    issue = _make_issue(evidence="flake8: E501 line too long (132 > 79 characters) line 10")
    review = _make_review(issues=[issue])
    file_diff = _make_file_diff()

    result = guardrail_check(review, file_diff)

    assert len(result.passed_issues) == 1


def test_grounding_check_eslint_reference_passes():
    """eslint is a valid tool name and must pass the grounding check."""
    issue = _make_issue(
        evidence="eslint: no-unused-vars [error] 'x' is defined but never used. line 10"
    )
    review = _make_review(issues=[issue])
    file_diff = _make_file_diff()

    result = guardrail_check(review, file_diff)

    assert len(result.passed_issues) == 1


def test_grounding_check_is_case_insensitive():
    """
    Tool name matching must be case-insensitive.
    The LLM might capitalise 'Bandit' — this must still pass.
    """
    issue = _make_issue(evidence="Bandit: B608 SQL injection detected at line 10")
    review = _make_review(issues=[issue])
    file_diff = _make_file_diff()

    result = guardrail_check(review, file_diff)

    assert len(result.passed_issues) == 1


# ---------------------------------------------------------------------------
# Line number validation tests
# ---------------------------------------------------------------------------

def test_line_validation_nonexistent_line_filters_issue():
    """
    Issue flagging line 999 when the diff only contains lines 10-12 and 42
    must be filtered with reason 'line_not_in_diff'.
    """
    issue = _make_issue(line_number=999)
    review = _make_review(issues=[issue])
    file_diff = _make_file_diff(added_lines=[10, 11, 12, 42])

    result = guardrail_check(review, file_diff)

    assert len(result.passed_issues) == 0
    assert len(result.filtered_issues) == 1
    assert "line_not_in_diff" in result.filtered_issues[0].filter_reason
    assert "999" in result.filtered_issues[0].filter_reason


def test_line_validation_valid_line_passes():
    """
    Issue flagging line 42 when 42 is in added_line_numbers passes
    line validation.
    """
    issue = _make_issue(line_number=42)
    review = _make_review(issues=[issue])
    file_diff = _make_file_diff(added_lines=[10, 11, 12, 42])

    result = guardrail_check(review, file_diff)

    assert len(result.passed_issues) == 1
    assert len(result.filtered_issues) == 0


def test_line_validation_empty_diff_filters_all_issues():
    """
    If the diff has no added lines (edge case: diff parsed incorrectly or
    binary file), every issue is filtered for line validation.
    """
    issue = _make_issue(line_number=10)
    review = _make_review(issues=[issue])
    file_diff = _make_file_diff(added_lines=[])

    result = guardrail_check(review, file_diff)

    assert len(result.passed_issues) == 0
    assert len(result.filtered_issues) == 1
    assert "line_not_in_diff" in result.filtered_issues[0].filter_reason


# ---------------------------------------------------------------------------
# Confidence gating tests
# ---------------------------------------------------------------------------

def test_confidence_gating_below_threshold_downgrades_severity():
    """
    confidence=0.4 (below 0.6 threshold):
    HIGH → MEDIUM, message prefixed with 'Low confidence: '.
    Issue is not filtered — it is still in passed_issues.
    """
    issue = _make_issue(severity=Severity.HIGH, message="SQL injection risk.")
    review = _make_review(issues=[issue], confidence=0.4)
    file_diff = _make_file_diff()

    result = guardrail_check(review, file_diff)

    assert len(result.passed_issues) == 1
    assert len(result.filtered_issues) == 0
    assert result.passed_issues[0].severity == Severity.MEDIUM
    assert result.passed_issues[0].message.startswith("Low confidence: ")
    assert result.applied_confidence_gating is True


def test_confidence_gating_medium_downgrades_to_low():
    """MEDIUM severity + low confidence → LOW."""
    issue = _make_issue(severity=Severity.MEDIUM)
    review = _make_review(issues=[issue], confidence=0.59)
    file_diff = _make_file_diff()

    result = guardrail_check(review, file_diff)

    assert result.passed_issues[0].severity == Severity.LOW


def test_confidence_gating_low_stays_low():
    """LOW severity + low confidence → still LOW (floor)."""
    issue = _make_issue(severity=Severity.LOW)
    review = _make_review(issues=[issue], confidence=0.3)
    file_diff = _make_file_diff()

    result = guardrail_check(review, file_diff)

    assert result.passed_issues[0].severity == Severity.LOW


def test_confidence_gating_above_threshold_no_change():
    """
    confidence=0.8 (above 0.6 threshold):
    severity and message unchanged, applied_confidence_gating is False.
    """
    issue = _make_issue(severity=Severity.HIGH, message="SQL injection risk.")
    review = _make_review(issues=[issue], confidence=0.8)
    file_diff = _make_file_diff()

    result = guardrail_check(review, file_diff)

    assert result.passed_issues[0].severity == Severity.HIGH
    assert result.passed_issues[0].message == "SQL injection risk."
    assert result.applied_confidence_gating is False


def test_confidence_gating_exactly_at_threshold_no_change():
    """confidence=0.6 is not below the threshold — no gating applied."""
    issue = _make_issue(severity=Severity.HIGH)
    review = _make_review(issues=[issue], confidence=0.6)
    file_diff = _make_file_diff()

    result = guardrail_check(review, file_diff)

    assert result.passed_issues[0].severity == Severity.HIGH
    assert result.applied_confidence_gating is False


def test_confidence_gating_does_not_filter_issue():
    """
    Low confidence issues are still posted — just modified.
    They must appear in passed_issues, not filtered_issues.
    """
    issue = _make_issue()
    review = _make_review(issues=[issue], confidence=0.1)
    file_diff = _make_file_diff()

    result = guardrail_check(review, file_diff)

    assert len(result.passed_issues) == 1
    assert len(result.filtered_issues) == 0


def test_confidence_gating_does_not_mutate_original_issue():
    """
    _apply_confidence_gating must use model_copy, not in-place mutation.
    The original issue object must be unchanged.
    """
    issue = _make_issue(severity=Severity.HIGH, message="Original message.")
    review = _make_review(issues=[issue], confidence=0.3)
    file_diff = _make_file_diff()

    guardrail_check(review, file_diff)

    # Original issue object is unchanged
    assert issue.severity == Severity.HIGH
    assert issue.message == "Original message."


# ---------------------------------------------------------------------------
# Full clean review
# ---------------------------------------------------------------------------

def test_guardrail_clean_review_passes_all():
    """
    A well-formed ReviewResult with good evidence, valid line numbers,
    and high confidence passes all checks. All issues in passed_issues.
    """
    issues = [
        _make_issue(line_number=10, evidence="bandit: B608 SQL injection line 10"),
        _make_issue(line_number=42, evidence="radon: process_data complexity=12 (grade C) line 42"),
    ]
    review = _make_review(issues=issues, confidence=0.88)
    file_diff = _make_file_diff(added_lines=[10, 11, 12, 42])

    result = guardrail_check(review, file_diff)

    assert len(result.passed_issues) == 2
    assert len(result.filtered_issues) == 0
    assert result.applied_confidence_gating is False


# ---------------------------------------------------------------------------
# Mixed: some pass, some filtered
# ---------------------------------------------------------------------------

def test_guardrail_partial_filter():
    """
    Two issues: one with a valid tool reference and valid line, one with
    no tool reference. Only the valid one passes.
    """
    good_issue = _make_issue(line_number=10, evidence="bandit: B608 SQL injection line 10")
    bad_issue = _make_issue(line_number=11, evidence="this code looks problematic indeed")

    review = _make_review(issues=[good_issue, bad_issue], confidence=0.85)
    file_diff = _make_file_diff(added_lines=[10, 11, 12])

    result = guardrail_check(review, file_diff)

    assert len(result.passed_issues) == 1
    assert len(result.filtered_issues) == 1
    assert result.passed_issues[0].line_number == 10
    assert result.filtered_issues[0].issue["line_number"] == 11


# ---------------------------------------------------------------------------
# DB logging
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_filtered_issues_logged_to_db():
    """
    log_filtered_issues stages FilteredIssue ORM records via db.add_all().
    Session commit is NOT called — that is the caller's responsibility.
    """
    filtered = [
        FilteredIssueData(
            issue={"line_number": 99, "type": "security", "severity": "HIGH"},
            filter_reason="line_not_in_diff: line 99 not in changed lines",
            file="app/auth.py",
        ),
        FilteredIssueData(
            issue={"line_number": 10, "type": "style", "severity": "LOW"},
            filter_reason="grounding_failure: evidence does not reference a known tool",
            file="app/auth.py",
        ),
    ]

    mock_db = AsyncMock()
    mock_db.add_all = MagicMock()  # synchronous method on AsyncSession

    await log_filtered_issues(review_id=7, filtered=filtered, db=mock_db)

    mock_db.add_all.assert_called_once()
    staged_records = mock_db.add_all.call_args[0][0]
    assert len(staged_records) == 2
    assert staged_records[0].review_id == 7
    assert staged_records[1].review_id == 7
    assert "line_not_in_diff" in staged_records[0].filter_reason
    assert "grounding_failure" in staged_records[1].filter_reason

    # Commit must NOT have been called
    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_log_filtered_issues_no_op_when_empty():
    """
    log_filtered_issues with an empty list must not call db.add_all at all.
    """
    mock_db = AsyncMock()
    mock_db.add_all = MagicMock()

    await log_filtered_issues(review_id=1, filtered=[], db=mock_db)

    mock_db.add_all.assert_not_called()