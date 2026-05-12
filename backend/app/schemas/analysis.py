"""
Analysis schemas for static analysis pipeline
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel

from app.utils.language import Language


class ToolResult(BaseModel):
    """Output from a single static analysis tool run."""

    tool_name: str  # "bandit" | "radon" | "eslint" | "ast"
    findings: List[str]  # formatted strings ready to cite in evidence_from_tool
    raw_output: str  # full subprocess stdout/stderr for debugging
    error: str | None  # populated when tool failed / not installed; does NOT raise

    @property
    def has_findings(self) -> bool:
        """Return true if this tool run produced findings."""
        return len(self.findings) > 0


class ToolFindings(BaseModel):
    """Aggregated static analysis results for one file."""

    filename: str
    language: Language  # mirrors FileDiff.language
    results: List[ToolResult]

    def has_findings(self) -> bool:
        """Return true if any tool in this run produced findings."""
        return any(r.has_findings for r in self.results)

    def all_findings(self) -> List[str]:
        """Flat list of all finding strings across all tools — for LLM prompt assembly."""
        findings: List[str] = []
        for result in self.results:
            findings.extend(result.findings)
        return findings
