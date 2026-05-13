"""
Diff parser — converts a raw unified diff + full file content into FileDiff.

Entry point: parse_file_diff(raw_diff, file_content, filename)
Returns FileDiff, or None if the file should be skipped entirely.

Callers must filter None values:
    diffs = [parse_file_diff(d, c, f) for d, c, f in file_data]
    diffs = [d for d in diffs if d is not None]

Design decisions:
- context_lines is built from file_content (not the diff), giving us a
  controlled ±20 line window independent of GitHub's default ±3 context.
- added_line_numbers tracks only "+" lines (not "+++ header" lines).
  Context lines (" " prefix) and removed lines ("-") are not included.
- Empty diff (no added lines): returns FileDiff with empty added_line_numbers.
  The guardrail's line validation will filter all issues for such files —
  this is correct behaviour, not a parser concern.
- Binary files: detected both by the "Binary files" diff prefix and by
  UnicodeDecodeError on file_content decode (caller's responsibility to
  catch the latter before calling this function).
"""
import logging
import re
from pathlib import Path
from typing import Optional

from app.schemas.diff import FileDiff
from app.utils.language import Language, detect_language
from app.utils.constants import SKIP_FILENAMES, SKIP_EXTENSIONS, MIGRATION_PATH_PATTERN

logger = logging.getLogger(__name__)

# Alembic auto-generated migration files: migrations/*.py
_MIGRATION_RE = re.compile(MIGRATION_PATH_PATTERN)

# Hunk header pattern: @@ -old_start,old_count +new_start,new_count @@
# new_count is optional (defaults to 1 when omitted by git)
_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# If two hunk context windows are within this many lines of each other, merge them.
_CONTEXT_MERGE_GAP = 10
_CONTEXT_RADIUS = 20


def parse_file_diff(
    raw_diff: str,
    file_content: str,
    filename: str,
) -> Optional[FileDiff]:
    """
    Parse a unified diff for a single file into a FileDiff.

    Args:
        raw_diff: Unified diff string for this file (from GitHub API).
        file_content: Full file content at PR HEAD. Used to build context_lines.
                      Pass empty string "" if the file was deleted.
        filename: File path as reported by GitHub (e.g. "backend/app/main.py").

    Returns:
        FileDiff if the file should be reviewed.
        None if the file should be skipped (lock file, binary, migration, etc.).
    """
    if _should_skip(raw_diff, filename):
        logger.debug("Skipping file: %s", filename)
        return None

    language = _detect_language(filename)
    added_line_numbers = _extract_added_line_numbers(raw_diff)
    context_lines = _build_context_lines(file_content, added_line_numbers)

    logger.debug(
        "Parsed %s: language=%s, added_lines=%d",
        filename,
        language,
        len(added_line_numbers),
    )

    return FileDiff(
        filename=filename,
        language=language,
        added_line_numbers=added_line_numbers,
        raw_diff=raw_diff,
        context_lines=context_lines,
    )


# ---------------------------------------------------------------------------
# Skip logic
# ---------------------------------------------------------------------------


def _should_skip(raw_diff: str, filename: str) -> bool:
    """Return True if this file must not be sent to the LLM."""
    basename = Path(filename).name

    if basename in SKIP_FILENAMES:
        return True

    # Check compound extensions first (.min.js before .js)
    for ext in SKIP_EXTENSIONS:
        if filename.endswith(ext):
            return True

    if _MIGRATION_RE.search(filename):
        return True

    if raw_diff.startswith("Binary files"):
        return True

    return False


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


def _detect_language(filename: str) -> Language:
    """Detect language — delegates to app.utils.language.detect_language."""
    return detect_language(filename)


# ---------------------------------------------------------------------------
# Added line number extraction
# ---------------------------------------------------------------------------


def _extract_added_line_numbers(raw_diff: str) -> list[int]:
    """
    Parse hunk headers to track new-file line numbers for all "+" lines.

    Skips the "+++ filename" header line (starts with "+++").
    Only counts lines starting with "+" (added lines).
    Lines starting with " " (context) advance the counter but are not recorded.
    Lines starting with "-" (removed) do NOT advance the new-file counter.
    """
    added: list[int] = []
    current_new_line: int = 0

    for line in raw_diff.splitlines():
        hunk_match = _HUNK_HEADER_RE.match(line)
        if hunk_match:
            current_new_line = int(hunk_match.group(1))
            continue

        if line.startswith("+++") or line.startswith("---"):
            # Diff file header lines — ignore, don't advance counter
            continue

        if line.startswith("+"):
            added.append(current_new_line)
            current_new_line += 1
        elif line.startswith("-"):
            # Removed line — does not exist in new file, don't advance
            pass
        elif line.startswith(" ") or line == "":
            # Context line — exists in new file, advance counter
            current_new_line += 1
        # Lines that don't match any prefix (e.g. "\ No newline at end of file")
        # are ignored and don't advance the counter.

    return added


# ---------------------------------------------------------------------------
# Context line extraction
# ---------------------------------------------------------------------------


def _build_context_lines(file_content: str, added_line_numbers: list[int]) -> str:
    """
    Build a ±20-line context window around all changed lines.

    If two windows overlap or are within _CONTEXT_MERGE_GAP lines of each other,
    they are merged into one block to avoid duplicating lines and fragmenting context.

    Args:
        file_content: Full file content at PR HEAD.
        added_line_numbers: 1-indexed line numbers of added lines.

    Returns:
        Merged context as a single string (lines joined by newline).
        Returns empty string if file_content is empty or added_line_numbers is empty.
    """
    if not file_content or not added_line_numbers:
        return ""

    lines = file_content.splitlines()
    total_lines = len(lines)

    # Build raw windows: (start_0indexed, end_0indexed) inclusive
    # added_line_numbers are 1-indexed
    windows: list[tuple[int, int]] = []
    for ln in added_line_numbers:
        start = max(0, ln - 1 - _CONTEXT_RADIUS)       # convert to 0-indexed
        end = min(total_lines - 1, ln - 1 + _CONTEXT_RADIUS)
        windows.append((start, end))

    if not windows:
        return ""

    merged = _merge_windows(windows, gap=_CONTEXT_MERGE_GAP)

    # Extract and join line slices
    blocks: list[str] = []
    for start, end in merged:
        block = "\n".join(lines[start : end + 1])
        blocks.append(block)

    return "\n\n".join(blocks)


def _merge_windows(
    windows: list[tuple[int, int]],
    gap: int,
) -> list[tuple[int, int]]:
    """
    Merge overlapping or near-adjacent windows.

    Two windows (a_start, a_end) and (b_start, b_end) are merged if
    b_start - a_end <= gap (they overlap or are within `gap` lines of each other).

    Input need not be sorted — this function sorts first.
    Returns a sorted list of non-overlapping merged windows.
    """
    sorted_windows = sorted(set(windows), key=lambda w: w[0])
    merged: list[tuple[int, int]] = []

    for start, end in sorted_windows:
        if merged and start - merged[-1][1] <= gap:
            # Extend the last window
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    return merged
