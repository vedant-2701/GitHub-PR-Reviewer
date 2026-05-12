"""
Tests for app/services/diff_parser.py

Run: pytest tests/test_diff_parser.py -v

Coverage:
- Normal Python diff → correct added_line_numbers
- context_lines contains ±20 lines around the hunk
- Multiple close hunks → merged context window (no duplicated lines)
- Multiple distant hunks → separate context blocks
- JS file → language = "javascript"
- TS file → language = "typescript"
- TSX/JSX files → correct language
- Unknown extension → language = "unknown"
- package-lock.json → returns None
- yarn.lock → returns None
- Binary file diff → returns None
- Alembic migration → returns None
- .min.js → returns None
- .svg → returns None
- Empty diff (no hunks) → returns FileDiff with empty added_line_numbers
- Removed-only diff → added_line_numbers is empty
- "\ No newline at end of file" line → does not corrupt line counter
- context_lines is empty when file_content is empty
- context_lines is empty when added_line_numbers is empty
- Hunk with no count (git omits ,1) → parsed correctly
"""
import textwrap

import pytest

from app.services.diff_parser import (
    _build_context_lines,
    _detect_language,
    _extract_added_line_numbers,
    _merge_windows,
    _should_skip,
    parse_file_diff,
)
from app.utils.language import Language


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_PYTHON_DIFF = textwrap.dedent("""\
    --- a/backend/app/main.py
    +++ b/backend/app/main.py
    @@ -10,6 +10,8 @@
     def existing_function():
         pass
     
    +def new_function():
    +    return 42
     
     def another_function():
         pass
""")

# File content for the simple diff — 20 lines total
SIMPLE_FILE_CONTENT = "\n".join([f"line {i}" for i in range(1, 21)])


def _make_file_content(n_lines: int) -> str:
    return "\n".join([f"line {i}" for i in range(1, n_lines + 1)])


# ---------------------------------------------------------------------------
# parse_file_diff — integration (happy path)
# ---------------------------------------------------------------------------


class TestParseFileDiff:
    def test_normal_python_diff_returns_file_diff(self):
        result = parse_file_diff(SIMPLE_PYTHON_DIFF, SIMPLE_FILE_CONTENT, "backend/app/main.py")
        assert result is not None
        assert result.filename == "backend/app/main.py"

    def test_normal_python_diff_correct_language(self):
        result = parse_file_diff(SIMPLE_PYTHON_DIFF, SIMPLE_FILE_CONTENT, "backend/app/main.py")
        assert result.language == Language.PYTHON

    def test_normal_python_diff_correct_added_line_numbers(self):
        # @@ -10,6 +10,8 @@ means new file starts at line 10
        # 3 context lines (" "), then 2 added lines ("+"), then 3 more context lines
        result = parse_file_diff(SIMPLE_PYTHON_DIFF, SIMPLE_FILE_CONTENT, "backend/app/main.py")
        # Lines 10, 11, 12 are context (" "), line 13 and 14 are added ("+")
        assert result.added_line_numbers == [13, 14]

    def test_raw_diff_preserved(self):
        result = parse_file_diff(SIMPLE_PYTHON_DIFF, SIMPLE_FILE_CONTENT, "backend/app/main.py")
        assert result.raw_diff == SIMPLE_PYTHON_DIFF


# ---------------------------------------------------------------------------
# Skip logic
# ---------------------------------------------------------------------------


class TestShouldSkip:
    def test_package_lock_json_skipped(self):
        assert _should_skip("", "package-lock.json") is True

    def test_package_lock_in_subdir_skipped(self):
        assert _should_skip("", "frontend/package-lock.json") is True

    def test_yarn_lock_skipped(self):
        assert _should_skip("", "yarn.lock") is True

    def test_poetry_lock_skipped(self):
        assert _should_skip("", "poetry.lock") is True

    def test_pipfile_lock_skipped(self):
        assert _should_skip("", "Pipfile.lock") is True

    def test_min_js_skipped(self):
        assert _should_skip("", "dist/bundle.min.js") is True

    def test_min_css_skipped(self):
        assert _should_skip("", "static/styles.min.css") is True

    def test_map_file_skipped(self):
        assert _should_skip("", "dist/bundle.js.map") is True

    def test_svg_skipped(self):
        assert _should_skip("", "assets/logo.svg") is True

    def test_png_skipped(self):
        assert _should_skip("", "assets/image.png") is True

    def test_ico_skipped(self):
        assert _should_skip("", "favicon.ico") is True

    def test_binary_diff_skipped(self):
        assert _should_skip("Binary files a/image.db and b/image.db differ", "image.db") is True

    def test_alembic_migration_skipped(self):
        assert _should_skip("", "migrations/0001_initial.py") is True

    def test_alembic_migration_in_subdir_skipped(self):
        assert _should_skip("", "backend/migrations/0002_add_column.py") is True

    def test_normal_python_file_not_skipped(self):
        assert _should_skip("", "app/main.py") is False

    def test_normal_js_file_not_skipped(self):
        assert _should_skip("", "src/App.jsx") is False

    def test_normal_ts_file_not_skipped(self):
        assert _should_skip("", "src/api/client.ts") is False


class TestParseFileDiffSkipReturnsNone:
    def test_package_lock_returns_none(self):
        assert parse_file_diff("", "", "package-lock.json") is None

    def test_binary_diff_returns_none(self):
        assert parse_file_diff("Binary files a/x.db and b/x.db differ", "", "data.db") is None

    def test_alembic_migration_returns_none(self):
        assert parse_file_diff("", "", "migrations/0001_auto.py") is None

    def test_svg_returns_none(self):
        assert parse_file_diff("", "", "logo.svg") is None

    def test_min_js_returns_none(self):
        assert parse_file_diff("", "", "bundle.min.js") is None


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    def test_py_is_python(self):
        assert _detect_language("app/main.py") == Language.PYTHON

    def test_js_is_javascript(self):
        assert _detect_language("src/index.js") == Language.JAVASCRIPT

    def test_jsx_is_javascript(self):
        assert _detect_language("src/App.jsx") == Language.JAVASCRIPT

    def test_ts_is_typescript(self):
        assert _detect_language("src/api/client.ts") == Language.TYPESCRIPT

    def test_tsx_is_typescript(self):
        assert _detect_language("src/components/Button.tsx") == Language.TYPESCRIPT

    def test_unknown_extension_is_unknown(self):
        assert _detect_language("Makefile") == Language.UNKNOWN

    def test_sh_is_unknown(self):
        assert _detect_language("scripts/deploy.sh") == Language.UNKNOWN

    def test_yaml_is_unknown(self):
        assert _detect_language("docker-compose.yml") == Language.UNKNOWN


# ---------------------------------------------------------------------------
# Added line number extraction
# ---------------------------------------------------------------------------


class TestExtractAddedLineNumbers:
    def test_simple_addition(self):
        diff = textwrap.dedent("""\
            @@ -1,3 +1,5 @@
             line one
             line two
            +new line a
            +new line b
             line three
        """)
        assert _extract_added_line_numbers(diff) == [3, 4]

    def test_addition_at_start_of_hunk(self):
        diff = textwrap.dedent("""\
            @@ -5,3 +5,4 @@
            +first added line
             context one
             context two
             context three
        """)
        assert _extract_added_line_numbers(diff) == [5]

    def test_removal_only_returns_empty(self):
        diff = textwrap.dedent("""\
            @@ -1,3 +1,2 @@
             context
            -removed line
             another context
        """)
        assert _extract_added_line_numbers(diff) == []

    def test_plus_plus_plus_header_not_counted(self):
        diff = textwrap.dedent("""\
            --- a/file.py
            +++ b/file.py
            @@ -1,2 +1,3 @@
             context
            +added
             context
        """)
        result = _extract_added_line_numbers(diff)
        # +++ header must NOT be counted as an added line
        assert result == [2]

    def test_multiple_hunks_accumulate_correctly(self):
        diff = textwrap.dedent("""\
            @@ -1,3 +1,4 @@
             a
            +b
             c
             d
            @@ -10,3 +11,4 @@
             x
            +y
             z
             w
        """)
        assert _extract_added_line_numbers(diff) == [2, 12]

    def test_no_newline_marker_does_not_advance_counter(self):
        diff = textwrap.dedent("""\
            @@ -1,2 +1,3 @@
             context
            +added
            \\ No newline at end of file
        """)
        result = _extract_added_line_numbers(diff)
        assert result == [2]

    def test_hunk_with_no_count_defaults_to_one(self):
        # git omits ,1 when count is 1: @@ -0,0 +1 @@
        diff = "@@ -0,0 +1 @@\n+only line\n"
        assert _extract_added_line_numbers(diff) == [1]

    def test_empty_diff_returns_empty_list(self):
        assert _extract_added_line_numbers("") == []


# ---------------------------------------------------------------------------
# Context window building
# ---------------------------------------------------------------------------


class TestBuildContextLines:
    def test_empty_file_content_returns_empty_string(self):
        assert _build_context_lines("", [10, 11]) == ""

    def test_empty_added_lines_returns_empty_string(self):
        assert _build_context_lines("line 1\nline 2", []) == ""

    def test_context_includes_lines_around_change(self):
        content = _make_file_content(50)
        # Change at line 25 — context should include lines 5-45
        result = _build_context_lines(content, [25])
        assert "line 5" in result
        assert "line 25" in result
        assert "line 45" in result

    def test_context_does_not_exceed_file_start(self):
        content = _make_file_content(30)
        # Change at line 2 — window would start at max(0, 2-1-20) = 0
        result = _build_context_lines(content, [2])
        assert "line 1" in result  # file start is included

    def test_context_does_not_exceed_file_end(self):
        content = _make_file_content(30)
        # Change at line 29 — window would end at min(29, 28+20) = 29
        result = _build_context_lines(content, [29])
        assert "line 30" in result  # file end is included

    def test_close_hunks_produce_single_merged_block(self):
        content = _make_file_content(100)
        # Lines 20 and 25 are within 10 lines of each other after ±20 expansion
        # Window 1: lines 1-40, Window 2: lines 6-45 → merged: lines 1-45
        result = _build_context_lines(content, [20, 25])
        # Should be one block, not two (no double newline separator between them)
        # Both line 20 and line 25 context should be present
        assert "line 20" in result
        assert "line 25" in result
        # Verify it's a single block by checking the separator count
        assert result.count("\n\n") == 0

    def test_distant_hunks_produce_separate_blocks(self):
        content = _make_file_content(200)
        # Lines 10 and 190 are far apart — windows won't merge
        result = _build_context_lines(content, [10, 190])
        # Two blocks separated by double newline
        assert result.count("\n\n") >= 1
        assert "line 10" in result
        assert "line 190" in result

    def test_no_duplicate_lines_in_merged_context(self):
        content = _make_file_content(60)
        # Lines 25 and 30 — windows definitely overlap
        result = _build_context_lines(content, [25, 30])
        result_lines = result.splitlines()
        # No line should appear twice
        assert len(result_lines) == len(set(result_lines))


# ---------------------------------------------------------------------------
# Window merging
# ---------------------------------------------------------------------------


class TestMergeWindows:
    def test_non_overlapping_windows_not_merged(self):
        windows = [(0, 10), (50, 60)]
        merged = _merge_windows(windows, gap=10)
        assert merged == [(0, 10), (50, 60)]

    def test_overlapping_windows_merged(self):
        windows = [(0, 20), (15, 35)]
        merged = _merge_windows(windows, gap=10)
        assert merged == [(0, 35)]

    def test_windows_within_gap_merged(self):
        # end of first = 20, start of second = 29, gap = 9 ≤ 10 → merged
        windows = [(0, 20), (29, 40)]
        merged = _merge_windows(windows, gap=10)
        assert merged == [(0, 40)]

    def test_windows_outside_gap_not_merged(self):
        # end of first = 20, start of second = 35, gap = 15 > 10 → not merged
        windows = [(0, 20), (35, 50)]
        merged = _merge_windows(windows, gap=10)
        assert merged == [(0, 20), (35, 50)]

    def test_three_windows_all_merged(self):
        windows = [(0, 15), (20, 35), (40, 55)]
        merged = _merge_windows(windows, gap=10)
        assert merged == [(0, 55)]

    def test_unsorted_input_sorted_correctly(self):
        windows = [(50, 60), (0, 10), (25, 35)]
        merged = _merge_windows(windows, gap=5)
        assert merged[0][0] == 0  # first window starts at 0

    def test_duplicate_windows_deduplicated(self):
        windows = [(0, 10), (0, 10), (20, 30)]
        merged = _merge_windows(windows, gap=5)
        assert len(merged) <= 2


# ---------------------------------------------------------------------------
# Empty diff edge case
# ---------------------------------------------------------------------------


class TestEmptyDiff:
    def test_empty_diff_returns_filediff_not_none(self):
        """
        A file with no added lines still returns a FileDiff (not None).
        The guardrail will filter all issues for it since added_line_numbers is empty.
        This is correct — the parser's job is skip detection, not quality gating.
        """
        result = parse_file_diff("", _make_file_content(10), "app/utils.py")
        assert result is not None
        assert result.language == Language.PYTHON
        assert result.added_line_numbers == []
        assert result.context_lines == ""
