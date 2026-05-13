"""
Tests for app/services/review_agent.py

All Groq calls are mocked — no real API calls ever made.
Mocked at call_groq_with_retry since that is the single choke point
for all Groq communication in the pipeline.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.analysis import ToolFindings, ToolResult
from app.schemas.diff import FileDiff
from app.schemas.review import ReviewResult
from app.services.review_agent import ReviewAgentError, review_file
from app.utils.groq_client import GroqRateLimitError


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

MOCK_GROQ_PATH = "app.services.review_agent.call_groq_with_retry"


def _make_file_diff(filename: str = "app/auth.py") -> FileDiff:
    return FileDiff(
        filename=filename,
        language="python",
        added_line_numbers=[10, 11, 12, 42],
        raw_diff=(
            "@@ -8,4 +8,6 @@\n"
            " def authenticate(user, password):\n"
            "+    query = f\"SELECT * FROM users WHERE name = '{user}'\"\n"
            "+    return db.execute(query)\n"
        ),
        context_lines=(
            "def authenticate(user, password):\n"
            "    # TODO: parameterise this query\n"
        ),
    )


def _make_tool_findings(filename: str = "app/auth.py") -> ToolFindings:
    return ToolFindings(
        filename=filename,
        language="python",
        results=[
            ToolResult(
                tool_name="bandit",
                findings=[
                    "bandit: B608 [HIGH/MEDIUM] Possible SQL injection via "
                    "string-based query construction. line 42"
                ],
                error=None,
                raw_output="",
            )
        ],
    )


def _valid_review_json(filename: str = "app/auth.py") -> str:
    payload = {
        "issues": [
            {
                "line_number": 42,
                "type": "security",
                "severity": "HIGH",
                "message": "SQL injection risk via f-string query construction.",
                "suggestion": "Use parameterised queries: db.execute('SELECT * FROM users WHERE name = ?', [user])",
                "evidence_from_tool": (
                    "bandit: B608 [HIGH/MEDIUM] Possible SQL injection via "
                    "string-based query construction. line 42"
                ),
            }
        ],
        "summary": "One high-severity SQL injection vulnerability found.",
        "overall_verdict": "REQUEST_CHANGES",
        "confidence": 0.92,
        "files_reviewed": [filename],
    }
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# Case 1: Success on first attempt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_file_success_first_attempt():
    """
    Happy path: Groq returns valid JSON on the first call.
    Result is a ReviewResult with the expected issue.
    """
    file_diff = _make_file_diff()
    tool_findings = _make_tool_findings()

    with patch(MOCK_GROQ_PATH, new_callable=AsyncMock) as mock_groq:
        mock_groq.return_value = _valid_review_json()

        result = await review_file(file_diff, tool_findings)

    assert isinstance(result, ReviewResult)
    assert len(result.issues) == 1
    assert result.issues[0].severity.value == "HIGH"
    assert result.issues[0].line_number == 42
    assert result.confidence == 0.92
    assert mock_groq.call_count == 1


# ---------------------------------------------------------------------------
# Case 2: JSONDecodeError on first attempt, success on second
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_file_retries_on_json_decode_error():
    """
    First Groq response is invalid JSON.
    Second Groq response is valid.
    Function returns a ReviewResult after 2 calls total.
    """
    file_diff = _make_file_diff()
    tool_findings = _make_tool_findings()

    with patch(MOCK_GROQ_PATH, new_callable=AsyncMock) as mock_groq:
        mock_groq.side_effect = [
            "this is not json {{{",   # attempt 1: bad JSON
            _valid_review_json(),      # attempt 2: valid
        ]

        result = await review_file(file_diff, tool_findings)

    assert isinstance(result, ReviewResult)
    assert mock_groq.call_count == 2


# ---------------------------------------------------------------------------
# Case 3: ValidationError on all 3 attempts — raises ReviewAgentError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_file_raises_after_all_validation_failures():
    """
    All 3 Groq responses produce valid JSON but fail Pydantic validation
    (confidence out of range). After 3 attempts, ReviewAgentError is raised.
    """
    file_diff = _make_file_diff()
    tool_findings = _make_tool_findings()

    bad_payload = json.dumps({
        "issues": [],
        "summary": "No issues.",
        "overall_verdict": "APPROVE",
        "confidence": 9.99,   # out of range — validator rejects
        "files_reviewed": ["app/auth.py"],
    })

    with patch(MOCK_GROQ_PATH, new_callable=AsyncMock) as mock_groq:
        mock_groq.return_value = bad_payload

        with pytest.raises(ReviewAgentError) as exc_info:
            await review_file(file_diff, tool_findings)

    assert mock_groq.call_count == 3
    assert "3" in str(exc_info.value)  # message references attempt count


# ---------------------------------------------------------------------------
# Case 4: evidence_from_tool empty → Pydantic validator rejects → retries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_file_retries_when_evidence_from_tool_is_empty():
    """
    Groq returns an issue where evidence_from_tool is empty (or too short).
    Pydantic's evidence_must_not_be_empty validator raises ValidationError.
    review_file retries; if all attempts fail, ReviewAgentError is raised.
    """
    file_diff = _make_file_diff()
    tool_findings = _make_tool_findings()

    bad_payload = json.dumps({
        "issues": [
            {
                "line_number": 42,
                "type": "security",
                "severity": "HIGH",
                "message": "SQL injection risk.",
                "suggestion": "Use parameterised queries.",
                "evidence_from_tool": "",   # empty — validator must reject
            }
        ],
        "summary": "One issue.",
        "overall_verdict": "REQUEST_CHANGES",
        "confidence": 0.85,
        "files_reviewed": ["app/auth.py"],
    })

    with patch(MOCK_GROQ_PATH, new_callable=AsyncMock) as mock_groq:
        mock_groq.return_value = bad_payload

        with pytest.raises(ReviewAgentError):
            await review_file(file_diff, tool_findings)

    assert mock_groq.call_count == 3


# ---------------------------------------------------------------------------
# Case 5: GroqRateLimitError propagates immediately — no parse retries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_file_propagates_rate_limit_error_immediately():
    """
    If call_groq_with_retry raises GroqRateLimitError, review_file must
    re-raise it immediately without consuming parse retries.
    The Celery task handles re-queueing — review_file must not mask this.
    """
    file_diff = _make_file_diff()
    tool_findings = _make_tool_findings()

    with patch(MOCK_GROQ_PATH, new_callable=AsyncMock) as mock_groq:
        mock_groq.side_effect = GroqRateLimitError("rate limited")

        with pytest.raises(GroqRateLimitError):
            await review_file(file_diff, tool_findings)

    # Called exactly once — no retries on rate limit
    assert mock_groq.call_count == 1
