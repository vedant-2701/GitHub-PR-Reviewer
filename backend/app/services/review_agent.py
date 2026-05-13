"""
Review agent — orchestrates the LLM call for a single file.

Responsibility:
  1. Build the system and user prompts from FileDiff + ToolFindings.
  2. Call Groq via call_groq_with_retry().
  3. Parse and validate the response against ReviewResult (up to 3 attempts).
  4. Raise ReviewAgentError if all attempts fail.

This module does NOT run static analysis and does NOT post GitHub comments.
It sits between static_analysis.py and guardrail.py in the pipeline.

Design note — why no LangChain AgentExecutor here:
  The agent framework is retained as a dependency (CLAUDE.md), but the
  tool-routing step (bandit/radon/flake8/eslint) already ran before this
  function is called. The findings are passed in as ToolFindings. Running
  AgentExecutor here would duplicate that work and add latency with no
  benefit. AgentExecutor is reserved for future multi-step reasoning if the
  review prompt needs to call tools mid-generation. For now, one Groq call
  with the full findings list is the correct approach.
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from app.schemas.diff import FileDiff
from app.schemas.analysis import ToolFindings
from app.schemas.review import ReviewResult
from app.utils.groq_client import GroqRateLimitError, GroqResponseError, call_groq_with_retry

logger = logging.getLogger(__name__)

_MAX_PARSE_ATTEMPTS = 3


class ReviewAgentError(Exception):
    """
    Raised when all parse/validation attempts for a Groq response fail.

    Callers (review_task.py) should log this and decide whether to retry
    the entire file review or skip it and continue with remaining files.
    Do not silently swallow this — it means the LLM returned unusable output.
    """


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a senior software engineer performing a code review on a pull request diff.

You have been given:
- The file diff (added lines prefixed with +, removed lines prefixed with -)
- Surrounding context lines for each changed hunk
- Findings from static analysis tools that have already run on the changed code

Your job is to produce a structured code review based on what the static analysis \
tools actually found. Do not invent issues that are not supported by a tool finding.

You must respond ONLY with a valid JSON object that exactly matches this schema. \
Do not include any explanation, markdown, code fences, or text outside the JSON object.

Schema:
{
  "issues": [
    {
      "line_number": <integer — must be a line number that appears in the diff>,
      "type": <"security" | "complexity" | "style" | "syntax">,
      "severity": <"HIGH" | "MEDIUM" | "LOW">,
      "message": <string — concise description of the issue>,
      "suggestion": <string — specific, actionable fix>,
      "evidence_from_tool": <string — REQUIRED. Must cite the exact tool name and finding.
        Examples:
          "bandit: B608 [HIGH/MEDIUM] Possible SQL injection via string-based query construction. line 42"
          "radon: calculate_score complexity=15 (grade D) line 18"
          "flake8: E501 line too long (132 > 79 characters) line 7"
          "eslint: no-unused-vars [error] 'userId' is defined but never used. line 23"
        Do NOT use vague strings like "the code looks complex" or "see bandit output".
        The string must contain the tool name (bandit/radon/flake8/eslint) and the specific finding.>
    }
  ],
  "summary": <string — one paragraph summary of the overall review>,
  "overall_verdict": <"APPROVE" | "REQUEST_CHANGES" | "COMMENT">,
  "confidence": <float between 0.0 and 1.0 — your confidence in this review.
    Use < 0.6 if the diff is ambiguous, the tool findings are sparse, or the context is insufficient.>,
  "files_reviewed": [<string — filename(s) reviewed>]
}

Constraints:
- issues must be an array (empty array [] if no issues found)
- Every issue MUST have a non-empty evidence_from_tool that names the tool
- line_number must be a line number visible in the diff — do not guess
- If confidence is below 0.6, still produce the full response — the pipeline handles low confidence
- Do not include any text outside the JSON object
"""


def _build_user_prompt(file_diff: FileDiff, tool_findings: ToolFindings) -> str:
    """
    Build the user message from the file diff and tool findings.

    The findings are numbered for readability, but the LLM must reference
    the tool name in evidence_from_tool — not the finding number.
    """
    findings = tool_findings.all_findings()

    if findings:
        findings_block = "\n".join(
            f"  {i + 1}. {finding}" for i, finding in enumerate(findings)
        )
    else:
        findings_block = "  (no findings from static analysis tools)"

    return (
        f"File: {file_diff.filename}\n"
        f"Language: {file_diff.language}\n"
        f"\n"
        f"--- Static analysis findings ---\n"
        f"{findings_block}\n"
        f"\n"
        f"--- Diff (changed lines) ---\n"
        f"{file_diff.raw_diff}\n"
        f"\n"
        f"--- Context (surrounding lines) ---\n"
        f"{file_diff.context_lines}\n"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def review_file(
    file_diff: FileDiff,
    tool_findings: ToolFindings,
) -> ReviewResult:
    """
    Run the LLM review for a single file.

    Calls Groq once per attempt. Retries up to _MAX_PARSE_ATTEMPTS times if
    the response is not valid JSON or fails Pydantic validation. 429 errors
    are handled inside call_groq_with_retry() — they do not consume a parse
    attempt here.

    Raises:
        GroqRateLimitError: Groq returned 429 after its own max retries.
            Let this propagate — the Celery task handles re-queueing.
        ReviewAgentError: All _MAX_PARSE_ATTEMPTS failed to produce a valid
            ReviewResult. Raw response is logged at ERROR level before raising.
    """
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(file_diff, tool_findings)},
    ]

    last_error: Exception | None = None
    raw: str = ""

    for attempt in range(1, _MAX_PARSE_ATTEMPTS + 1):
        try:
            raw = await call_groq_with_retry(messages)
            data = json.loads(raw)
            result = ReviewResult(**data)
            logger.info(
                "review_agent: parsed ReviewResult for %s on attempt %d "
                "(%d issues, confidence=%.2f)",
                file_diff.filename,
                attempt,
                len(result.issues),
                result.confidence,
            )
            return result

        except GroqRateLimitError:
            # Do not retry here — rate limit errors come with their own backoff
            # inside call_groq_with_retry(). Propagate immediately so the
            # Celery task can decide whether to requeue the whole PR review.
            logger.warning(
                "review_agent: Groq rate limit hit for %s — propagating",
                file_diff.filename,
            )
            raise

        except json.JSONDecodeError as exc:
            logger.warning(
                "review_agent: JSONDecodeError on attempt %d/%d for %s: %s | "
                "raw (first 300 chars): %s",
                attempt,
                _MAX_PARSE_ATTEMPTS,
                file_diff.filename,
                exc,
                raw[:300],
            )
            last_error = exc

        except ValidationError as exc:
            logger.warning(
                "review_agent: Pydantic ValidationError on attempt %d/%d for %s: %s",
                attempt,
                _MAX_PARSE_ATTEMPTS,
                file_diff.filename,
                exc,
            )
            logger.debug(
                "review_agent: raw response that failed validation for %s: %s",
                file_diff.filename,
                raw,
            )
            last_error = exc

        except GroqResponseError as exc:
            # Non-retryable Groq error (e.g. empty response body). Count as
            # a parse failure so we still attempt the full _MAX_PARSE_ATTEMPTS.
            logger.warning(
                "review_agent: GroqResponseError on attempt %d/%d for %s: %s",
                attempt,
                _MAX_PARSE_ATTEMPTS,
                file_diff.filename,
                exc,
            )
            last_error = exc

    # All attempts exhausted.
    logger.error(
        "review_agent: all %d parse attempts failed for %s. "
        "Last raw response (first 500 chars): %s | Last error: %s",
        _MAX_PARSE_ATTEMPTS,
        file_diff.filename,
        raw[:500],
        last_error,
    )
    raise ReviewAgentError(
        f"All {_MAX_PARSE_ATTEMPTS} Groq parse attempts failed for "
        f"{file_diff.filename}. Last error: {last_error}"
    )
