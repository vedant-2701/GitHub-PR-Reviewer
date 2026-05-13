"""
Posts review results to GitHub as inline PR comments + one summary comment.

Responsibilities:
- Authenticate via GitHub App (private key from settings, never file I/O here)
- Post one inline comment per passed issue (line_number, message + suggestion)
- Post one PR-level summary comment (verdict, guardrail stats, skipped files)
- Isolate per-issue failures: GithubException on one inline comment is logged
  and skipped — it must not abort the rest of the PR review

NOT responsible for:
- Guardrail checks (done before this is called)
- Building the summary string (done in review_task.py)
- Any filtering or aggregation logic
"""
import logging
from dataclasses import dataclass, field
from typing import List

from github import Github, GithubException, GithubIntegration
from github.PullRequest import PullRequest
from github.Repository import Repository

from app.config import get_settings
from app.schemas.review import Issue
from app.services.guardrail import GuardrailResult

logger = logging.getLogger(__name__)


@dataclass
class PostResult:
    """Outcome of a single post_review_comments() call."""
    inline_posted: int = 0
    inline_failed: int = 0
    summary_posted: bool = False
    failed_issues: List[dict] = field(default_factory=list)


def _get_github_client() -> Github:
    """
    Authenticate as GitHub App and return an installation-scoped Github client.

    Reads private key via settings.GITHUB_PRIVATE_KEY (property — file I/O in
    config.py, not here). Raises FileNotFoundError if the .pem is missing,
    GithubException if auth fails. Both are fatal at task start — caller handles.
    """
    settings = get_settings()
    integration = GithubIntegration(
        integration_id=settings.GITHUB_APP_ID,
        private_key=settings.GITHUB_PRIVATE_KEY,
    )
    # get_installations() returns all installations; we use the first one.
    # For a single-org app this is always correct. Multi-org requires
    # matching repo to installation — document if scope expands.
    installations = integration.get_installations()
    installation = next(iter(installations), None)
    if installation is None:
        raise GithubException(
            status=401,
            data={"message": "No GitHub App installation found. Is the app installed on the repo?"},
        )
    return installation.get_github_for_installation()


def _get_pr(gh: Github, repo_full_name: str, pr_number: int) -> tuple[Repository, PullRequest]:
    """Fetch repo and PR objects. GithubException propagates to caller."""
    repo = gh.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)
    return repo, pr


def _post_inline_comment(
    pr: PullRequest,
    issue: Issue,
    commit_sha: str,
) -> bool:
    """
    Post a single inline review comment.

    Returns True on success, False on GithubException (caller logs, continues).
    The most common failure mode: line_number no longer exists in the latest
    commit (force-push between diff parse and comment post). We catch and
    continue — the summary comment will still post.
    """
    body = f"**[{issue.severity}] {issue.type.upper()}**\n\n{issue.message}"
    if issue.suggestion:
        body += f"\n\n**Suggestion:** {issue.suggestion}"
    if issue.evidence_from_tool:
        body += f"\n\n*Tool finding: {issue.evidence_from_tool}*"

    try:
        pr.create_review_comment(
            body=body,
            commit_id=commit_sha,
            path=issue.file_path,  # set by review_task.py before passing in
            line=issue.line_number,
        )
        return True
    except GithubException as e:
        logger.warning(
            "Inline comment failed for %s:%d — %s (status=%d). Skipping.",
            issue.file_path,
            issue.line_number,
            e.data.get("message", str(e)),
            e.status,
        )
        return False


def post_review_comments(
    repo_full_name: str,
    pr_number: int,
    file_results: List[GuardrailResult],
    summary: str,
) -> PostResult:
    """
    Post all passed issues as inline comments, then post the summary.

    Args:
        repo_full_name: "owner/repo"
        pr_number:      PR number
        file_results:   List of GuardrailResult — one per reviewed file.
                        Only passed_issues are posted. filtered_issues are
                        accounted for in the summary string (built by caller).
        summary:        Pre-built summary string from review_task.py.
                        This function does no aggregation — I/O only.

    Returns:
        PostResult with counts of posted/failed inline comments.

    Raises:
        GithubException: if auth, repo fetch, or PR fetch fails — these are
                         fatal and should propagate to the Celery task.
        FileNotFoundError: if GitHub private key file is missing.
    """
    result = PostResult()

    gh = _get_github_client()
    repo, pr = _get_pr(gh, repo_full_name, pr_number)

    # Use the HEAD commit of the PR for inline comments.
    # All line numbers in GuardrailResult were validated against this diff.
    head_commit_sha = pr.head.sha

    # --- Inline comments ---
    for gr in file_results:
        for issue in gr.passed_issues:
            success = _post_inline_comment(pr, issue, head_commit_sha)
            if success:
                result.inline_posted += 1
            else:
                result.inline_failed += 1
                result.failed_issues.append({
                    "file": getattr(issue, "file_path", "unknown"),
                    "line": issue.line_number,
                    "message": issue.message,
                })

    logger.info(
        "Inline comments for %s PR #%d: %d posted, %d failed",
        repo_full_name, pr_number,
        result.inline_posted, result.inline_failed,
    )

    # --- Summary comment ---
    # Always post, even if all inline comments failed.
    # The summary is the authoritative record of what was reviewed.
    try:
        pr.create_issue_comment(body=summary)
        result.summary_posted = True
        logger.info("Summary comment posted for %s PR #%d", repo_full_name, pr_number)
    except GithubException as e:
        # Summary failure is logged but does NOT raise — inline comments already posted.
        # The Celery task still saves the review to DB.
        logger.error(
            "Failed to post summary comment for %s PR #%d: %s (status=%d)",
            repo_full_name, pr_number,
            e.data.get("message", str(e)),
            e.status,
        )

    return result
