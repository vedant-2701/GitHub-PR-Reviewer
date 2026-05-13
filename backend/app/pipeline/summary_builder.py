# app/pipeline/summary_builder.py
"""
Pure functions for computing the overall PR verdict and building the
GitHub summary comment.

No I/O, no async, no DB — these are deterministic transformations of
pipeline outcome data. They can be unit-tested without mocking anything.

Functions:
    compute_overall_verdict(outcomes) -> VerdictEnum
    build_summary(outcomes, verdict, pattern_skipped, agent_skipped,
                  repo_full_name, pr_number) -> str
"""
import logging
from typing import List

from app.models.review import VerdictEnum
from app.pipeline.types import FileReviewOutcome

logger = logging.getLogger(__name__)


def compute_overall_verdict(outcomes: List[FileReviewOutcome]) -> VerdictEnum:
    """
    Determine the overall PR verdict from the guardrail results of every file.

    Rules (in priority order):
        1. Any HIGH-severity issue  → REQUEST_CHANGES
        2. Any MEDIUM-severity issue → COMMENT
        3. Otherwise               → APPROVE

    Files that were skipped (no guardrail_result) are ignored.
    """
    has_medium = False

    for outcome in outcomes:
        if outcome.guardrail_result and outcome.guardrail_result.passed_issues:
            severities = {i.severity for i in outcome.guardrail_result.passed_issues}
            if "HIGH" in severities:
                return VerdictEnum.REQUEST_CHANGES
            if "MEDIUM" in severities:
                has_medium = True

    return VerdictEnum.COMMENT if has_medium else VerdictEnum.APPROVE


def build_summary(
    outcomes: List[FileReviewOutcome],
    overall_verdict: VerdictEnum,
    pattern_skipped: List[str],
    agent_skipped: List[str],
    repo_full_name: str,
    pr_number: int,
) -> str:
    """
    Build the PR-level GitHub summary comment text.

    All aggregation of outcome data lives here — github_poster.py is I/O only
    and receives the fully-built string.

    Args:
        outcomes:        Per-file outcomes from the review loop.
        overall_verdict: Verdict computed by compute_overall_verdict().
        pattern_skipped: Filenames where parse_file_diff() returned None
                         (lock files, binary, migrations — skipped before LLM).
        agent_skipped:   Filenames where static analysis or review_file() raised
                         (skipped mid-pipeline, noted as ⚠️ in summary).
        repo_full_name:  e.g. "org/repo".
        pr_number:       PR number integer.

    Returns:
        Markdown-formatted string suitable for posting as a GitHub review comment.
    """
    total_passed = sum(
        len(o.guardrail_result.passed_issues)
        for o in outcomes
        if o.guardrail_result
    )
    total_filtered = sum(
        len(o.guardrail_result.filtered_issues)
        for o in outcomes
        if o.guardrail_result
    )
    files_reviewed = sum(1 for o in outcomes if o.guardrail_result)

    confidence_scores = [
        o.guardrail_result.original_confidence
        for o in outcomes
        if o.guardrail_result
    ]
    avg_confidence = (
        sum(confidence_scores) / len(confidence_scores)
        if confidence_scores else 0.0
    )
    confidence_gated = sum(
        1 for o in outcomes
        if o.guardrail_result and o.guardrail_result.applied_confidence_gating
    )

    # Map verdict enum to display string.
    verdict_display = {
        VerdictEnum.REQUEST_CHANGES: "🔴 REQUEST CHANGES",
        VerdictEnum.COMMENT: "🟡 COMMENT",
        VerdictEnum.APPROVE: "🟢 APPROVE",
    }[overall_verdict]

    lines = [
        f"## ACE Code Review — {repo_full_name} PR #{pr_number}",
        "",
        f"**Verdict:** {verdict_display}",
        "",
        "### Review Stats",
        f"- Files reviewed: **{files_reviewed}**",
        f"- Issues posted: **{total_passed}**",
        f"- Issues filtered by guardrail: **{total_filtered}**",
        f"- Average LLM confidence: **{avg_confidence:.0%}**",
    ]

    if confidence_gated:
        lines.append(
            f"- Files with confidence gating applied: **{confidence_gated}** "
            "(severity downgraded, messages prefixed with 'Low confidence:')"
        )

    if pattern_skipped:
        lines += [
            "",
            f"### ⏭ {len(pattern_skipped)} file(s) skipped (lock files / binary / minified)",
            *[f"  - `{f}`" for f in pattern_skipped],
        ]

    if agent_skipped:
        lines += [
            "",
            f"### ⚠️ {len(agent_skipped)} file(s) skipped due to agent error",
            *[f"  - `{f}`" for f in agent_skipped],
            "",
            "_These files were not reviewed. Check worker logs for details._",
        ]

    lines += [
        "",
        "---",
        "_Posted by ACE Code Review Agent · Guardrail layer active_",
    ]

    return "\n".join(lines)
