"""
Locked Pydantic schema for LLM review output.

This is the contract between the Groq LLM and the rest of the pipeline.
Do not add fields, remove fields, or relax validators without a documented
decision in CLAUDE.md and an explicit PR description explaining the reason.

The evidence_from_tool validator is the backbone of the guardrail system.
Weakening it means hallucinated issues reach GitHub.
"""

from __future__ import annotations

from enum import Enum
from typing import List

from pydantic import BaseModel, field_validator


class IssueType(str, Enum):
    SECURITY = "security"
    COMPLEXITY = "complexity"
    STYLE = "style"
    SYNTAX = "syntax"


class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Verdict(str, Enum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    COMMENT = "COMMENT"


class Issue(BaseModel):
    line_number: int
    type: IssueType
    severity: Severity
    message: str
    suggestion: str
    evidence_from_tool: str

    @field_validator("evidence_from_tool")
    @classmethod
    def evidence_must_not_be_empty(cls, v: str) -> str:
        """
        Reject empty, whitespace-only, or vague evidence strings.

        The minimum length of 10 characters is intentionally low — the guardrail
        grounding check does the real work (must reference a tool name). This
        validator is the first line of defence: it catches the LLM returning
        empty string or a throwaway phrase like "see code".
        """
        if not v or not v.strip() or len(v.strip()) < 10:
            raise ValueError(
                "evidence_from_tool must contain a specific tool finding. "
                "Empty strings and vague phrases are not accepted. "
                f"Got: {v!r}"
            )
        return v.strip()


class ReviewResult(BaseModel):
    issues: List[Issue]
    summary: str
    overall_verdict: Verdict
    confidence: float
    files_reviewed: List[str]

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(
                f"confidence must be between 0.0 and 1.0, got {v}"
            )
        return v
