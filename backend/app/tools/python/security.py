"""
Runs bandit (Python security linter) on a temp file built from context_lines.

Findings are filtered to added_line_numbers only — we surface issues introduced
by the PR, not pre-existing problems in the surrounding context window.

Finding format: "bandit: {test_id} [{severity}/{confidence}] {issue_text} line {line}"
The guardrail grounding check looks for "bandit" in evidence_from_tool — this format
makes that check trivially satisfied for any real finding.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import tempfile
from typing import List, Set

from app.schemas.analysis import ToolResult

logger = logging.getLogger(__name__)

TOOL_TIMEOUT = 30  # seconds


async def run_bandit(
    context_lines: str,
    added_line_numbers: Set[int],
) -> ToolResult:
    """
    Run bandit on context_lines written to a temp file.

    Filters results to added_line_numbers.
    Never raises — tool failures are captured in ToolResult.error.
    """
    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(context_lines)
        tmp_path = tmp.name

    try:
        return await asyncio.to_thread(
            _run_bandit_sync, tmp_path, added_line_numbers
        )
    finally:
        # Always clean up — even if _run_bandit_sync raises (it shouldn't, but be safe)
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _run_bandit_sync(tmp_path: str, added_line_numbers: Set[int]) -> ToolResult:
    """Synchronous bandit execution — called via asyncio.to_thread."""
    try:
        result = subprocess.run(
            ["bandit", "-f", "json", "-q", "--exit-zero", tmp_path],
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT,
        )
        raw_output = result.stdout or result.stderr
        return _parse_bandit_output(raw_output, added_line_numbers)

    except FileNotFoundError:
        msg = "bandit not installed or not on PATH"
        logger.warning("security_tool: %s", msg)
        return ToolResult(tool_name="bandit", findings=[], raw_output="", error=msg)

    except subprocess.TimeoutExpired:
        msg = f"bandit timed out after {TOOL_TIMEOUT}s"
        logger.warning("security_tool: %s", msg)
        return ToolResult(tool_name="bandit", findings=[], raw_output="", error=msg)

    except Exception as e:
        msg = f"bandit unexpected error: {e}"
        logger.exception("security_tool: unexpected error running bandit")
        return ToolResult(tool_name="bandit", findings=[], raw_output="", error=msg)


def _parse_bandit_output(raw_output: str, added_line_numbers: Set[int]) -> ToolResult:
    """
    Parse bandit JSON output into formatted finding strings.

    bandit --exit-zero ensures non-zero exit on findings doesn't look like an error.
    We filter to added_line_numbers so we only flag PR-introduced issues.
    """
    if not raw_output.strip():
        return ToolResult(
            tool_name="bandit", findings=[], raw_output=raw_output, error=None
        )

    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as e:
        msg = f"bandit output is not valid JSON: {e}"
        logger.warning("security_tool: %s | raw: %s", msg, raw_output[:200])
        return ToolResult(
            tool_name="bandit", findings=[], raw_output=raw_output, error=msg
        )

    findings: List[str] = []
    for issue in data.get("results", []):
        line_number = issue.get("line_number", 0)
        if line_number not in added_line_numbers:
            continue

        test_id = issue.get("test_id", "UNKNOWN")
        severity = issue.get("issue_severity", "UNKNOWN")
        confidence = issue.get("issue_confidence", "UNKNOWN")
        issue_text = issue.get("issue_text", "").strip()

        findings.append(
            f"bandit: {test_id} [{severity}/{confidence}] {issue_text} line {line_number}"
        )

    return ToolResult(
        tool_name="bandit",
        findings=findings,
        raw_output=raw_output,
        error=None,
    )
