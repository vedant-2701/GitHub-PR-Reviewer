"""
Orchestrates static analysis for a single FileDiff.

Language routing is handled entirely by the registry in app/tools/registry.py.
This file has no language-specific logic and never needs to change when a new
language is added. Add a new language by:
  1. Creating app/tools/<language>/__init__.py with a registered LanguageAnalyser.
  2. Adding `import app.tools.<language>` in app/tools/registry.py.

All tool runners are async and use asyncio.to_thread internally for subprocess calls.
Tool failures are captured in ToolResult.error and never propagate as exceptions here.
"""
from __future__ import annotations

import logging

from app.schemas.analysis import ToolFindings
from app.schemas.diff import FileDiff
from app.tools import registry

logger = logging.getLogger(__name__)


async def run_static_analysis(file_diff: FileDiff) -> ToolFindings:
    """
    Run the appropriate static analysis tools for file_diff.language.

    Looks up the registered LanguageAnalyser for the language, delegates to it,
    and wraps the results in ToolFindings.

    Early-returns empty ToolFindings if:
      - context_lines is empty (e.g. deleted file, or diff parser produced nothing)
      - added_line_numbers is empty (no changed lines to analyse)
      - no analyser is registered for this language

    Never raises. All tool errors are captured in ToolResult.error.
    """
    if not file_diff.context_lines.strip():
        logger.info(
            "static_analysis: skipping %s — context_lines is empty",
            file_diff.filename,
        )
        return _empty(file_diff)

    if not file_diff.added_line_numbers:
        logger.info(
            "static_analysis: skipping %s — no added line numbers (nothing changed)",
            file_diff.filename,
        )
        return _empty(file_diff)

    analyser = registry.get_analyser(file_diff.language)
    if analyser is None:
        logger.info(
            "static_analysis: no analyser registered for language '%s' (%s)",
            file_diff.language,
            file_diff.filename,
        )
        return _empty(file_diff)

    added = set(file_diff.added_line_numbers)
    results = await analyser.run(
        context_lines=file_diff.context_lines,
        added_line_numbers=added,
        filename=file_diff.filename,
    )

    total_findings = sum(len(r.findings) for r in results)
    tool_errors = [r.tool_name for r in results if r.error]

    logger.info(
        "static_analysis: %s — %d finding(s) from %d tool(s)%s",
        file_diff.filename,
        total_findings,
        len(results),
        f" [errors: {tool_errors}]" if tool_errors else "",
    )

    return ToolFindings(
        filename=file_diff.filename,
        language=file_diff.language,
        results=results,
    )


def _empty(file_diff: FileDiff) -> ToolFindings:
    return ToolFindings(
        filename=file_diff.filename,
        language=file_diff.language,
        results=[],
    )
