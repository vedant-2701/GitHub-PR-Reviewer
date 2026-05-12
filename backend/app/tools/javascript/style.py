"""
Runs ESLint on a temp file built from context_lines.

ESLint severity mapping:
  1 → warning
  2 → error

We report both. The LLM decides whether to surface a warning as LOW severity.

Finding format: "eslint: {rule_id} [{severity_label}] {message} line {line}"

Note: ESLint requires a config file (.eslintrc) to be present, or it falls back
to its default ruleset. We run with --no-eslintrc and pass --rule flags would
require knowing the ruleset ahead of time — impractical. Instead we rely on
whatever config ESLint discovers from the project root. If no config is found,
ESLint may exit with "no rules" — this is treated as an empty findings result,
not an error.

The --stdin-filename flag tells ESLint the "virtual" filename so it can apply
extension-specific rules (e.g., JSX rules for .jsx files). We pass the original
filename, not the temp path.
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

# ESLint severity int → human label for the finding string
_SEVERITY_LABEL = {1: "warning", 2: "error"}


async def run_eslint(
    context_lines: str,
    added_line_numbers: Set[int],
    original_filename: str,
) -> ToolResult:
    """
    Run ESLint on context_lines written to a temp file.

    original_filename is passed as --stdin-filename so ESLint applies the correct
    extension-specific ruleset (e.g. .tsx vs .js).

    Filters results to added_line_numbers.
    Never raises — tool failures are captured in ToolResult.error.
    """
    # ESLint needs the correct extension for its parser
    import os
    _, ext = os.path.splitext(original_filename)
    if not ext:
        ext = ".js"

    with tempfile.NamedTemporaryFile(
        suffix=ext, mode="w", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(context_lines)
        tmp_path = tmp.name

    try:
        return await asyncio.to_thread(
            _run_eslint_sync, tmp_path, added_line_numbers
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _run_eslint_sync(tmp_path: str, added_line_numbers: Set[int]) -> ToolResult:
    """Synchronous ESLint execution — called via asyncio.to_thread."""
    try:
        result = subprocess.run(
            ["eslint", "--format", "json", "--no-ignore", tmp_path],
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT,
        )
        # ESLint exits 1 when it finds linting errors — that's not a tool failure.
        # Exit 2 means configuration error — treat as tool error.
        if result.returncode == 2:
            msg = f"eslint configuration error (exit 2): {result.stderr[:200]}"
            logger.warning("style_tool: %s", msg)
            return ToolResult(
                tool_name="eslint",
                findings=[],
                raw_output=result.stderr,
                error=msg,
            )
        raw_output = result.stdout or result.stderr
        return _parse_eslint_output(raw_output, added_line_numbers)

    except FileNotFoundError:
        msg = "eslint not installed or not on PATH"
        logger.warning("style_tool: %s", msg)
        return ToolResult(tool_name="eslint", findings=[], raw_output="", error=msg)

    except subprocess.TimeoutExpired:
        msg = f"eslint timed out after {TOOL_TIMEOUT}s"
        logger.warning("style_tool: %s", msg)
        return ToolResult(tool_name="eslint", findings=[], raw_output="", error=msg)

    except Exception as e:
        msg = f"eslint unexpected error: {e}"
        logger.exception("style_tool: unexpected error running eslint")
        return ToolResult(tool_name="eslint", findings=[], raw_output="", error=msg)


def _parse_eslint_output(raw_output: str, added_line_numbers: Set[int]) -> ToolResult:
    """
    Parse ESLint JSON output into formatted finding strings.

    ESLint JSON format: [{filePath, messages: [{ruleId, severity, message, line, column}]}]
    """
    if not raw_output.strip():
        return ToolResult(
            tool_name="eslint", findings=[], raw_output=raw_output, error=None
        )

    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as e:
        msg = f"eslint output is not valid JSON: {e}"
        logger.warning("style_tool: %s | raw: %s", msg, raw_output[:200])
        return ToolResult(
            tool_name="eslint", findings=[], raw_output=raw_output, error=msg
        )

    findings: List[str] = []

    # data is a list of file results — we have exactly one file
    for file_result in data:
        for message in file_result.get("messages", []):
            line_number = message.get("line", 0)
            if line_number not in added_line_numbers:
                continue

            rule_id = message.get("ruleId") or "unknown-rule"
            severity_int = message.get("severity", 1)
            severity_label = _SEVERITY_LABEL.get(severity_int, "warning")
            msg_text = message.get("message", "").strip()

            findings.append(
                f"eslint: {rule_id} [{severity_label}] {msg_text} line {line_number}"
            )

    return ToolResult(
        tool_name="eslint",
        findings=findings,
        raw_output=raw_output,
        error=None,
    )
