"""
Runs radon cc (cyclomatic complexity) on a temp file built from context_lines.

Complexity grade mapping (radon standard):
  A (1-5):  simple, no concern
  B (6-10): slightly complex — we flag these
  C (11-15): complex
  D (16-20): more complex
  E (21-25): complex, high risk
  F (26+):  untestable, very high risk

We report grade B and above (complexity > 5). Grade A is noise.

Finding format: "radon: {name} complexity={score} (grade {grade}) line {line}"
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
COMPLEXITY_THRESHOLD = 5  # report anything > 5 (grade B and above)


async def run_radon(
    context_lines: str,
    added_line_numbers: Set[int],
) -> ToolResult:
    """
    Run radon cc on context_lines written to a temp file.

    Filters results to functions/methods whose definition line is in added_line_numbers.
    Never raises — tool failures are captured in ToolResult.error.
    """
    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(context_lines)
        tmp_path = tmp.name

    try:
        return await asyncio.to_thread(
            _run_radon_sync, tmp_path, added_line_numbers
        )
    finally:
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _run_radon_sync(tmp_path: str, added_line_numbers: Set[int]) -> ToolResult:
    """Synchronous radon execution — called via asyncio.to_thread."""
    try:
        result = subprocess.run(
            ["radon", "cc", "-j", tmp_path],
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT,
        )
        raw_output = result.stdout or result.stderr
        return _parse_radon_output(raw_output, added_line_numbers)

    except FileNotFoundError:
        msg = "radon not installed or not on PATH"
        logger.warning("complexity_tool: %s", msg)
        return ToolResult(tool_name="radon", findings=[], raw_output="", error=msg)

    except subprocess.TimeoutExpired:
        msg = f"radon timed out after {TOOL_TIMEOUT}s"
        logger.warning("complexity_tool: %s", msg)
        return ToolResult(tool_name="radon", findings=[], raw_output="", error=msg)

    except Exception as e:
        msg = f"radon unexpected error: {e}"
        logger.exception("complexity_tool: unexpected error running radon")
        return ToolResult(tool_name="radon", findings=[], raw_output="", error=msg)


def _parse_radon_output(raw_output: str, added_line_numbers: Set[int]) -> ToolResult:
    """
    Parse radon JSON output into formatted finding strings.

    radon cc -j returns: {filename: [{"name": str, "lineno": int, "complexity": int, "rank": str}, ...]}
    We iterate all files in the JSON (there will be exactly one — our temp file).
    """
    if not raw_output.strip():
        return ToolResult(
            tool_name="radon", findings=[], raw_output=raw_output, error=None
        )

    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as e:
        msg = f"radon output is not valid JSON: {e}"
        logger.warning("complexity_tool: %s | raw: %s", msg, raw_output[:200])
        return ToolResult(
            tool_name="radon", findings=[], raw_output=raw_output, error=msg
        )

    findings: List[str] = []

    # data is {filename: [block, ...]} — we don't care about the key, just the blocks
    for blocks in data.values():
        for block in blocks:
            complexity = block.get("complexity", 0)
            if complexity <= COMPLEXITY_THRESHOLD:
                continue

            line_number = block.get("lineno", 0)
            if line_number not in added_line_numbers:
                continue

            name = block.get("name", "unknown")
            rank = block.get("rank", "?")

            findings.append(
                f"radon: {name} complexity={complexity} (grade {rank}) line {line_number}"
            )

    return ToolResult(
        tool_name="radon",
        findings=findings,
        raw_output=raw_output,
        error=None,
    )
