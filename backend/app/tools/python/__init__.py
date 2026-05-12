"""
Python static analysis analyser.

Registered for: "python"
Tools: bandit (security), radon (complexity), flake8 (style/syntax)

All three tools run independently. A failure in one does not skip the others.
"""
from __future__ import annotations

from typing import List

from app.schemas.analysis import ToolResult
from app.tools.python.complexity import run_radon
from app.tools.python.security import run_bandit
from app.tools.python.syntax import run_flake8
from app.tools.registry import LanguageAnalyser, register


@register("python")
class PythonAnalyser(LanguageAnalyser):
    """
    Python analyser that runs bandit, radon, and flake8.
    """
    async def run(
        self,
        context_lines: str,
        added_line_numbers: set[int],
        filename: str,
    ) -> List[ToolResult]:
        bandit_result = await run_bandit(context_lines, added_line_numbers)
        radon_result = await run_radon(context_lines, added_line_numbers)
        flake8_result = await run_flake8(context_lines, added_line_numbers)
        return [bandit_result, radon_result, flake8_result]