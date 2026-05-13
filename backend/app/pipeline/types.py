# app/pipeline/types.py
"""
Internal dataclasses shared across pipeline submodules.

These types are intentionally kept separate from app/schemas/ because they
are pipeline-internal glue, not API contracts:

- IssueWithPath  — wraps a guardrail-passed Issue with its source file_path.
                   Issue (locked LLM schema) has no file_path; the pipeline
                   knows the path and injects it here for github_poster.py.

- FileReviewOutcome — aggregates the full result for a single reviewed file,
                      used to build the summary and persist the review record.
"""
from dataclasses import dataclass
from typing import Optional

from app.schemas.diff import FileDiff
from app.schemas.review import Issue
from app.services.guardrail import GuardrailResult


@dataclass
class IssueWithPath:
    """
    Guardrail-passed Issue enriched with the source file path.

    Issue (locked LLM schema) does not carry file_path — the LLM returns
    issues scoped to a single file and has no knowledge of the repository
    path. The pipeline knows the path and injects it here so that
    github_poster.py can create inline review comments on the correct file.
    """

    line_number: int
    type: str
    severity: str
    message: str
    suggestion: str
    evidence_from_tool: str
    file_path: str  # injected by the pipeline, never from the LLM

    @classmethod
    def from_issue(cls, issue: Issue, file_path: str) -> "IssueWithPath":
        """Construct from a guardrail-passed Issue and a known file path."""
        return cls(
            line_number=issue.line_number,
            type=issue.type,
            severity=issue.severity,
            message=issue.message,
            suggestion=issue.suggestion,
            evidence_from_tool=issue.evidence_from_tool,
            file_path=file_path,
        )


@dataclass
class FileReviewOutcome:
    """
    Aggregates the full review result for one file.

    Fields:
        file_diff        — the parsed diff that was reviewed.
        guardrail_result — populated after guardrail_check(); None if the file
                           was skipped before reaching the guardrail step.
        skipped          — True when static analysis or the review agent raised.
        skip_reason      — human-readable reason for skip (logged + surfaced in
                           the PR summary comment).
    """

    file_diff: FileDiff
    guardrail_result: Optional[GuardrailResult] = None
    skipped: bool = False
    skip_reason: str = ""
