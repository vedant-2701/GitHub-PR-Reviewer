# tests/test_static_analysis.py
"""
Tests for static analysis orchestration and individual tool runners.

All subprocess calls are mocked — no real bandit/radon/eslint/flake8 needed.

Structure:
  - Registry tests: correct analyser returned per language, None for unknown
  - run_static_analysis() orchestration: early-exit cases, routing, result assembly
  - Tool runner tests: each tool in isolation (import from new paths)
    bandit, radon, flake8 from app.tools.python.*
    eslint from app.tools.javascript.style
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.analysis import ToolFindings, ToolResult
from app.schemas.diff import FileDiff
from app.services.static_analysis import run_static_analysis
from app.tools.javascript.style import run_eslint
from app.tools.python.complexity import run_radon
from app.tools.python.security import run_bandit
from app.tools.python.syntax import run_flake8
from app.tools.registry import get_analyser, registered_languages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_python_diff(
    context_lines: str = "def foo():\n    pass\n",
    added_line_numbers: list[int] | None = None,
) -> FileDiff:
    return FileDiff(
        filename="app/services/foo.py",
        language="python",
        added_line_numbers=added_line_numbers if added_line_numbers is not None else [1],
        raw_diff="@@ -0,0 +1,2 @@\n+def foo():\n+    pass\n",
        context_lines=context_lines,
    )


def make_js_diff(
    context_lines: str = "const x = 1;\n",
    added_line_numbers: list[int] | None = None,
) -> FileDiff:
    return FileDiff(
        filename="src/app.js",
        language="javascript",
        added_line_numbers=added_line_numbers if added_line_numbers is not None else [1],
        raw_diff="@@ -0,0 +1 @@\n+const x = 1;\n",
        context_lines=context_lines,
    )


def make_unknown_diff() -> FileDiff:
    return FileDiff(
        filename="README.md",
        language="unknown",
        added_line_numbers=[1],
        raw_diff="@@ -0,0 +1 @@\n+hello\n",
        context_lines="hello\n",
    )


def bandit_json(line: int = 1) -> str:
    return json.dumps({
        "results": [{
            "test_id": "B608",
            "issue_severity": "HIGH",
            "issue_confidence": "MEDIUM",
            "issue_text": "Possible SQL injection via string-based query construction",
            "line_number": line,
        }],
        "errors": [],
    })


def radon_json(name: str = "do_work", lineno: int = 1, complexity: int = 8, rank: str = "B") -> str:
    return json.dumps({
        "/tmp/tmpXXXXXX.py": [
            {"name": name, "lineno": lineno, "complexity": complexity, "rank": rank}
        ]
    })


def eslint_json(line: int = 1, rule_id: str = "no-unused-vars", severity: int = 2) -> str:
    return json.dumps([{
        "filePath": "/tmp/tmpXXXXXX.js",
        "messages": [{
            "ruleId": rule_id,
            "severity": severity,
            "message": "'x' is defined but never used.",
            "line": line,
            "column": 7,
        }],
    }])


def flake8_output(line: int = 1, code: str = "F401", message: str = "'os' imported but unused") -> str:
    return f"{line}::1::{code}::{message}\n"


def completed_process(stdout: str = "", stderr: str = "", returncode: int = 0):
    p = MagicMock(spec=subprocess.CompletedProcess)
    p.stdout = stdout
    p.stderr = stderr
    p.returncode = returncode
    return p


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_python_returns_python_analyser():
    analyser = get_analyser("python")
    assert analyser is not None
    assert type(analyser).__name__ == "PythonAnalyser"


def test_registry_javascript_returns_javascript_analyser():
    analyser = get_analyser("javascript")
    assert analyser is not None
    assert type(analyser).__name__ == "JavaScriptAnalyser"


def test_registry_typescript_returns_javascript_analyser():
    """typescript shares the JavaScriptAnalyser."""
    analyser = get_analyser("typescript")
    assert analyser is not None
    assert type(analyser).__name__ == "JavaScriptAnalyser"


def test_registry_unknown_language_returns_none():
    assert get_analyser("unknown") is None
    assert get_analyser("go") is None
    assert get_analyser("rust") is None


def test_registry_registered_languages_includes_expected():
    langs = registered_languages()
    assert "python" in langs
    assert "javascript" in langs
    assert "typescript" in langs


# ---------------------------------------------------------------------------
# run_static_analysis — orchestration and early-exit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_static_analysis_empty_context_lines_returns_empty():
    diff = make_python_diff(context_lines="")
    result = await run_static_analysis(diff)
    assert isinstance(result, ToolFindings)
    assert result.results == []


@pytest.mark.asyncio
async def test_static_analysis_empty_added_line_numbers_returns_empty():
    diff = make_python_diff(added_line_numbers=[])
    result = await run_static_analysis(diff)
    assert result.results == []


@pytest.mark.asyncio
async def test_static_analysis_unknown_language_returns_empty():
    diff = make_unknown_diff()
    result = await run_static_analysis(diff)
    assert result.language == "unknown"
    assert result.results == []


@pytest.mark.asyncio
async def test_static_analysis_python_routes_to_python_analyser():
    """Python diff → PythonAnalyser.run() called, all three tool results present."""
    diff = make_python_diff()

    expected_results = [
        ToolResult(tool_name="bandit", findings=[], raw_output="", error=None),
        ToolResult(tool_name="radon", findings=[], raw_output="", error=None),
        ToolResult(tool_name="flake8", findings=[], raw_output="", error=None),
    ]

    # Patch at the analyser class level — clean, no asyncio.to_thread namespace issues.
    with patch("app.tools.python.PythonAnalyser.run", new=AsyncMock(return_value=expected_results)):
        result = await run_static_analysis(diff)

    tool_names = [r.tool_name for r in result.results]
    assert tool_names == ["bandit", "radon", "flake8"]


@pytest.mark.asyncio
async def test_static_analysis_javascript_routes_to_javascript_analyser():
    diff = make_js_diff()

    expected_results = [
        ToolResult(tool_name="eslint", findings=[], raw_output="", error=None),
    ]

    with patch("app.tools.javascript.JavaScriptAnalyser.run", new=AsyncMock(return_value=expected_results)):
        result = await run_static_analysis(diff)

    assert [r.tool_name for r in result.results] == ["eslint"]


@pytest.mark.asyncio
async def test_static_analysis_typescript_routes_to_javascript_analyser():
    diff = FileDiff(
        filename="src/app.ts",
        language="typescript",
        added_line_numbers=[1],
        raw_diff="",
        context_lines="const x: number = 1;\n",
    )

    expected_results = [
        ToolResult(tool_name="eslint", findings=[], raw_output="", error=None),
    ]

    with patch("app.tools.javascript.JavaScriptAnalyser.run", new=AsyncMock(return_value=expected_results)):
        result = await run_static_analysis(diff)

    assert result.results[0].tool_name == "eslint"


@pytest.mark.asyncio
async def test_static_analysis_assembles_tool_findings_correctly():
    """ToolFindings.filename and language mirror the FileDiff."""
    diff = make_python_diff()

    with patch("app.tools.python.PythonAnalyser.run", new=AsyncMock(return_value=[])):
        result = await run_static_analysis(diff)

    assert result.filename == diff.filename
    assert result.language == diff.language


# ---------------------------------------------------------------------------
# bandit — run_bandit (app/tools/python/security.py)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bandit_not_installed_returns_error_no_raise():
    with patch("app.tools.python.security.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="bandit", findings=[], raw_output="",
            error="bandit not installed or not on PATH",
        )
        result = await run_bandit("def foo(): pass\n", {1})

    assert result.tool_name == "bandit"
    assert result.error is not None
    assert "not installed" in result.error
    assert result.findings == []


@pytest.mark.asyncio
async def test_bandit_timeout_returns_error_no_raise():
    with patch("app.tools.python.security.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="bandit", findings=[], raw_output="",
            error="bandit timed out after 30s",
        )
        result = await run_bandit("def foo(): pass\n", {1})

    assert "timed out" in result.error
    assert result.findings == []


@pytest.mark.asyncio
async def test_bandit_finding_on_added_line_is_included():
    with patch("app.tools.python.security.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="bandit",
            findings=["bandit: B608 [HIGH/MEDIUM] Possible SQL injection via string-based query construction line 1"],
            raw_output=bandit_json(line=1),
            error=None,
        )
        result = await run_bandit("query = f'SELECT * FROM {table}'\n", {1})

    assert len(result.findings) == 1
    assert "bandit: B608" in result.findings[0]
    assert "line 1" in result.findings[0]


@pytest.mark.asyncio
async def test_bandit_finding_not_on_added_line_is_filtered():
    with patch("app.tools.python.security.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="bandit", findings=[], raw_output=bandit_json(line=5), error=None,
        )
        result = await run_bandit("query = f'SELECT * FROM {table}'\n", {1, 2, 3})

    assert result.findings == []


@pytest.mark.asyncio
async def test_bandit_invalid_json_output_returns_error():
    with patch("app.tools.python.security.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="bandit", findings=[], raw_output="not json",
            error="bandit output is not valid JSON: ...",
        )
        result = await run_bandit("def foo(): pass\n", {1})

    assert result.error is not None
    assert result.findings == []


# ---------------------------------------------------------------------------
# radon — run_radon (app/tools/python/complexity.py)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_radon_complexity_above_threshold_on_added_line_is_included():
    with patch("app.tools.python.complexity.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="radon",
            findings=["radon: do_work complexity=8 (grade B) line 1"],
            raw_output=radon_json(lineno=1, complexity=8),
            error=None,
        )
        result = await run_radon("def do_work():\n    pass\n", {1})

    assert len(result.findings) == 1
    assert "radon: do_work complexity=8" in result.findings[0]


@pytest.mark.asyncio
async def test_radon_complexity_at_or_below_threshold_is_not_included():
    with patch("app.tools.python.complexity.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="radon", findings=[], raw_output=radon_json(lineno=1, complexity=5, rank="A"), error=None,
        )
        result = await run_radon("def foo():\n    pass\n", {1})

    assert result.findings == []


@pytest.mark.asyncio
async def test_radon_finding_not_on_added_line_is_filtered():
    with patch("app.tools.python.complexity.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="radon", findings=[], raw_output=radon_json(lineno=10, complexity=15), error=None,
        )
        result = await run_radon("def do_work():\n    pass\n", {1, 2, 3})

    assert result.findings == []


@pytest.mark.asyncio
async def test_radon_not_installed_returns_error_no_raise():
    with patch("app.tools.python.complexity.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="radon", findings=[], raw_output="",
            error="radon not installed or not on PATH",
        )
        result = await run_radon("def foo(): pass\n", {1})

    assert result.error is not None
    assert result.findings == []


@pytest.mark.asyncio
async def test_radon_timeout_returns_error_no_raise():
    with patch("app.tools.python.complexity.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="radon", findings=[], raw_output="", error="radon timed out after 30s",
        )
        result = await run_radon("def foo(): pass\n", {1})

    assert "timed out" in result.error


# ---------------------------------------------------------------------------
# eslint — run_eslint (app/tools/javascript/style.py)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eslint_not_installed_returns_error_no_raise():
    with patch("app.tools.javascript.style.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="eslint", findings=[], raw_output="",
            error="eslint not installed or not on PATH",
        )
        result = await run_eslint("const x = 1;\n", {1}, "src/app.js")

    assert result.error is not None
    assert "not installed" in result.error
    assert result.findings == []


@pytest.mark.asyncio
async def test_eslint_config_error_exit_2_returns_error_no_raise():
    with patch("app.tools.javascript.style.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="eslint", findings=[], raw_output="No ESLint configuration found.",
            error="eslint configuration error (exit 2): No ESLint configuration found.",
        )
        result = await run_eslint("const x = 1;\n", {1}, "src/app.js")

    assert result.error is not None
    assert result.findings == []


@pytest.mark.asyncio
async def test_eslint_finding_on_added_line_is_included():
    with patch("app.tools.javascript.style.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="eslint",
            findings=["eslint: no-unused-vars [error] 'x' is defined but never used. line 1"],
            raw_output=eslint_json(line=1),
            error=None,
        )
        result = await run_eslint("const x = 1;\n", {1}, "src/app.js")

    assert len(result.findings) == 1
    assert "eslint: no-unused-vars" in result.findings[0]
    assert "line 1" in result.findings[0]


@pytest.mark.asyncio
async def test_eslint_finding_not_on_added_line_is_filtered():
    with patch("app.tools.javascript.style.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="eslint", findings=[], raw_output=eslint_json(line=10), error=None,
        )
        result = await run_eslint("const x = 1;\n", {1, 2}, "src/app.js")

    assert result.findings == []


@pytest.mark.asyncio
async def test_eslint_timeout_returns_error_no_raise():
    with patch("app.tools.javascript.style.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="eslint", findings=[], raw_output="", error="eslint timed out after 30s",
        )
        result = await run_eslint("const x = 1;\n", {1}, "src/app.js")

    assert "timed out" in result.error


# ---------------------------------------------------------------------------
# flake8 — run_flake8 (app/tools/python/syntax.py)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flake8_not_installed_returns_error_no_raise():
    with patch("app.tools.python.syntax.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="flake8", findings=[], raw_output="",
            error="flake8 not installed or not on PATH",
        )
        result = await run_flake8("import os\n", {1})

    assert result.error is not None
    assert "not installed" in result.error
    assert result.findings == []


@pytest.mark.asyncio
async def test_flake8_timeout_returns_error_no_raise():
    with patch("app.tools.python.syntax.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="flake8", findings=[], raw_output="", error="flake8 timed out after 30s",
        )
        result = await run_flake8("import os\n", {1})

    assert "timed out" in result.error
    assert result.findings == []


@pytest.mark.asyncio
async def test_flake8_finding_on_added_line_is_included():
    raw = flake8_output(line=1, code="F401", message="'os' imported but unused")
    with patch("app.tools.python.syntax.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="flake8",
            findings=["flake8: F401 'os' imported but unused line 1"],
            raw_output=raw,
            error=None,
        )
        result = await run_flake8("import os\n", {1})

    assert len(result.findings) == 1
    assert "flake8: F401" in result.findings[0]
    assert "line 1" in result.findings[0]


@pytest.mark.asyncio
async def test_flake8_finding_not_on_added_line_is_filtered():
    raw = flake8_output(line=10, code="F401", message="'os' imported but unused")
    with patch("app.tools.python.syntax.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="flake8", findings=[], raw_output=raw, error=None,
        )
        result = await run_flake8("import os\n", {1, 2, 3})

    assert result.findings == []


@pytest.mark.asyncio
async def test_flake8_e501_always_filtered_regardless_of_line():
    raw = flake8_output(line=1, code="E501", message="line too long (120 > 79 characters)")
    with patch("app.tools.python.syntax.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="flake8", findings=[], raw_output=raw, error=None,
        )
        result = await run_flake8("x = 'a' * 120\n", {1})

    assert result.findings == []


@pytest.mark.asyncio
async def test_flake8_malformed_line_skipped_rest_parsed():
    raw = "NOTPARSEABLE\n1::1::W291::trailing whitespace\n"
    with patch("app.tools.python.syntax.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="flake8",
            findings=["flake8: W291 trailing whitespace line 1"],
            raw_output=raw,
            error=None,
        )
        result = await run_flake8("x = 1   \n", {1})

    assert len(result.findings) == 1
    assert "W291" in result.findings[0]


@pytest.mark.asyncio
async def test_flake8_empty_output_returns_empty_findings():
    with patch("app.tools.python.syntax.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = ToolResult(
            tool_name="flake8", findings=[], raw_output="", error=None,
        )
        result = await run_flake8("x = 1\n", {1})

    assert result.findings == []
    assert result.error is None


# ---------------------------------------------------------------------------
# ToolFindings helpers
# ---------------------------------------------------------------------------

def test_tool_findings_has_findings_false_when_empty():
    findings = ToolFindings(filename="foo.py", language="python", results=[])
    assert not findings.has_findings()


def test_tool_findings_has_findings_true_when_results_have_findings():
    result = ToolResult(tool_name="bandit", findings=["bandit: B101 finding"], raw_output="", error=None)
    findings = ToolFindings(filename="foo.py", language="python", results=[result])
    assert findings.has_findings()


def test_tool_findings_all_findings_flat_list():
    r1 = ToolResult(tool_name="bandit", findings=["bandit: A", "bandit: B"], raw_output="", error=None)
    r2 = ToolResult(tool_name="radon", findings=["radon: C"], raw_output="", error=None)
    findings = ToolFindings(filename="foo.py", language="python", results=[r1, r2])
    assert findings.all_findings() == ["bandit: A", "bandit: B", "radon: C"]