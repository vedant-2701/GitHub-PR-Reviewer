"""
Runs flake8 (Python style/syntax linter) on a temp file built from context_lines.

flake8 covers three categories:
  E/W — pycodestyle errors and warnings (style, whitespace, indentation)
  F   — pyflakes (undefined names, unused imports, undefined variables)
  C   — McCabe complexity (if --max-complexity is set — we don't set it here,
        radon handles complexity separately and more granularly)

Exit codes:
  0 — no issues
  1 — issues found (NOT a tool failure — parse stdout normally)
  anything else with empty stdout — treat as tool error

We ignore E501 (line too long) by default — it's a formatting preference that
generates noise and adds no value to a code review. Add codes to IGNORED_CODES
to suppress others project-wide.

Finding format: "flake8: {code} {message} line {line}"
Example:        "flake8: F401 'os' imported but unused line 3"

The guardrail grounding check looks for "flake8" in evidence_from_tool — this
format satisfies that check for any real finding.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from typing import List, Set

from app.schemas.analysis import ToolResult

logger = logging.getLogger(__name__)

TOOL_TIMEOUT = 30  # seconds

# Flake8 codes to suppress globally.
# E501: line too long — formatting preference, not a meaningful review finding.
# Extend this set to silence other noisy rules project-wide.
IGNORED_CODES: Set[str] = {"E501"}

# Format string for flake8 output — machine-parseable, no JSON plugin needed.
# Produces lines like: "3::1::F401::'os' imported but unused"
_FORMAT = "%(row)d::%(col)d::%(code)s::%(text)s"


async def run_flake8(
    context_lines: str,
    added_line_numbers: Set[int],
) -> ToolResult:
    """
    Run flake8 on context_lines written to a temp file.

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
            _run_flake8_sync, tmp_path, added_line_numbers
        )
    finally:
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _run_flake8_sync(tmp_path: str, added_line_numbers: Set[int]) -> ToolResult:
    """Synchronous flake8 execution — called via asyncio.to_thread."""
    try:
        result = subprocess.run(
            [
                "flake8",
                f"--format={_FORMAT}",
                "--isolated",   # ignore project-level .flake8/setup.cfg — consistent behaviour
                tmp_path,
            ],
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT,
        )
        # Exit 0 = clean, exit 1 = findings found — both are normal.
        # Any other exit code with empty stdout is a configuration/crash error.
        if result.returncode not in (0, 1) and not result.stdout.strip():
            msg = f"flake8 exited with code {result.returncode}: {result.stderr[:200]}"
            logger.warning("syntax_tool: %s", msg)
            return ToolResult(
                tool_name="flake8",
                findings=[],
                raw_output=result.stderr,
                error=msg,
            )

        raw_output = result.stdout
        return _parse_flake8_output(raw_output, added_line_numbers)

    except FileNotFoundError:
        msg = "flake8 not installed or not on PATH"
        logger.warning("syntax_tool: %s", msg)
        return ToolResult(tool_name="flake8", findings=[], raw_output="", error=msg)

    except subprocess.TimeoutExpired:
        msg = f"flake8 timed out after {TOOL_TIMEOUT}s"
        logger.warning("syntax_tool: %s", msg)
        return ToolResult(tool_name="flake8", findings=[], raw_output="", error=msg)

    except Exception as e:
        msg = f"flake8 unexpected error: {e}"
        logger.exception("syntax_tool: unexpected error running flake8")
        return ToolResult(tool_name="flake8", findings=[], raw_output="", error=msg)


def _parse_flake8_output(raw_output: str, added_line_numbers: Set[int]) -> ToolResult:
    """
    Parse flake8 custom-format output into finding strings.

    Expected line format: "{row}::{col}::{code}::{text}"
    Malformed lines are logged and skipped — one bad line does not discard the rest.
    """
    if not raw_output.strip():
        return ToolResult(
            tool_name="flake8", findings=[], raw_output=raw_output, error=None
        )

    findings: List[str] = []

    for raw_line in raw_output.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        parts = raw_line.split("::", maxsplit=3)
        if len(parts) != 4:
            logger.debug("syntax_tool: skipping malformed flake8 line: %s", raw_line)
            continue

        row_str, _col_str, code, text = parts

        try:
            line_number = int(row_str)
        except ValueError:
            logger.debug("syntax_tool: could not parse line number from: %s", raw_line)
            continue

        # Strip the code prefix from text if flake8 includes it (some versions do)
        # e.g. "F401 'os' imported but unused" → we want just the message
        message = text.strip()
        if message.startswith(code):
            message = message[len(code):].strip()

        if code in IGNORED_CODES:
            continue

        if line_number not in added_line_numbers:
            continue

        findings.append(f"flake8: {code} {message} line {line_number}")

    return ToolResult(
        tool_name="flake8",
        findings=findings,
        raw_output=raw_output,
        error=None,
    )
