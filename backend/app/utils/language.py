"""
Language utility — maps file extension → Language enum
"""

from __future__ import annotations

import os
from enum import Enum


class Language(str, Enum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    UNKNOWN = "unknown"


# Maps file extension → Language enum used throughout the pipeline.
# UNKNOWN means no static analysis tool is available for this file type.
_EXTENSION_MAP: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
}


def detect_language(filename: str) -> Language:
    """
    Return the Language enum for a given filename based on its extension.

    Returns one of: Language.PYTHON | Language.JAVASCRIPT | Language.TYPESCRIPT | Language.UNKNOWN.
    Never raises — unrecognised extensions return Language.UNKNOWN.
    """
    _, ext = os.path.splitext(filename.lower())
    return _EXTENSION_MAP.get(ext, Language.UNKNOWN)
