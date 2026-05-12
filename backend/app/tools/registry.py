"""
Language analyser registry.

Defines the LanguageAnalyser abstract base class and the @register decorator.
Any module that wants to handle a language imports this module, defines a
subclass of LanguageAnalyser, and decorates it with @register("language").

The registry is populated by side-effect imports at the bottom of this file.
When adding a new language:
  1. Create app/tools/<language>/__init__.py with a registered LanguageAnalyser subclass.
  2. Add `import app.tools.<language>` at the bottom of this file.
  That's it. app/services/static_analysis.py never changes.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List

from app.schemas.analysis import ToolResult

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[LanguageAnalyser]] = {}


class LanguageAnalyser(ABC):
    """
    Abstract base for per-language static analysis orchestration.

    Subclasses are responsible for calling the appropriate tool runners
    for their language and returning a list of ToolResults.

    Contract:
      - Never raise. All tool errors must be captured in ToolResult.error.
      - Always return a list (empty is valid — means no tools ran or all failed).
      - filename is provided for tools that need the original extension
        (e.g. ESLint uses it to apply JSX/TSX rules).
    """

    @abstractmethod
    async def run(
        self,
        context_lines: str,
        added_line_numbers: set[int],
        filename: str,
    ) -> List[ToolResult]:
        """
        Run static analysis tools for the given language.

        Args:
            context_lines: The context lines to analyze.
            added_line_numbers: The line numbers that were added.
            filename: The filename of the file to analyze.

        Returns:
            A list of ToolResult objects.
        """
        ...


def register(*languages: str):
    """
    Class decorator. Registers a LanguageAnalyser subclass for one or more
    language strings (as produced by language_router.language_from_filename).

    Usage:
        @register("python")
        class PythonAnalyser(LanguageAnalyser):
            ...

        @register("javascript", "typescript")
        class JavaScriptAnalyser(LanguageAnalyser):
            ...
    """
    def decorator(cls: type[LanguageAnalyser]) -> type[LanguageAnalyser]:
        for lang in languages:
            if lang in _REGISTRY:
                logger.warning(
                    "registry: overwriting existing analyser for language '%s' "
                    "(was %s, now %s)",
                    lang,
                    _REGISTRY[lang].__name__,
                    cls.__name__,
                )
            _REGISTRY[lang] = cls
            logger.debug("registry: registered %s for language '%s'", cls.__name__, lang)
        return cls
    return decorator


def get_analyser(language: str) -> LanguageAnalyser | None:
    """
    Return a fresh instance of the registered analyser for this language.

    Returns None if no analyser is registered — the caller is responsible
    for handling the unknown-language case (log + return empty ToolFindings).
    Never raises.
    """
    cls = _REGISTRY.get(language)
    if cls is None:
        return None
    return cls()


def registered_languages() -> list[str]:
    """Return the list of languages currently in the registry. Used in tests and health checks."""
    return list(_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Trigger registration.
# Each import below executes the @register decorator in that package's __init__.py.
# Add one line here when you add support for a new language.
# ---------------------------------------------------------------------------
import app.tools.python      # noqa: F401  — registers "python"
import app.tools.javascript  # noqa: F401  — registers "javascript", "typescript"