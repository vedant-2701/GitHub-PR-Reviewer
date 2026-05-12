"""
JavaScript/TypeScript static analysis analyser.

Registered for: "javascript", "typescript"
Tools: eslint (style, and security/complexity if project has relevant plugins configured)

Note: ESLint security and complexity coverage depends on the project's .eslintrc.
If eslint-plugin-security or complexity rules are not configured, those categories
will have no findings. This is a known limitation — there is no standalone equivalent
of bandit or radon that can be assumed universally installed for JS/TS.
"""
from __future__ import annotations

from typing import List

from app.schemas.analysis import ToolResult
from app.tools.javascript.style import run_eslint
from app.tools.registry import LanguageAnalyser, register


@register("javascript", "typescript")
class JavaScriptAnalyser(LanguageAnalyser):
    """
    JavaScript/TypeScript static analysis analyser.
    """
    async def run(
        self,
        context_lines: str,
        added_line_numbers: set[int],
        filename: str,
    ) -> List[ToolResult]:
        eslint_result = await run_eslint(context_lines, added_line_numbers, filename)
        return [eslint_result]