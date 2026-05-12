"""
Language router - maps file extension → language string
"""

from __future__ import annotations

import os

# Maps file extension → language string used throughout the pipeline.
# "unknown" means no static analysis tool is available for this file type.
_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}


def language_from_filename(filename: str) -> str:
    """
    Return the language string for a given filename based on its extension.

    Returns one of: "python" | "javascript" | "typescript" | "unknown".
    Never raises — unrecognised extensions return "unknown".
    """
    _, ext = os.path.splitext(filename.lower())
    return _EXTENSION_MAP.get(ext, "unknown")