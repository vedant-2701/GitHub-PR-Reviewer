# tests/test_github_poster.py
"""
Tests for app/services/github_poster.py.

All PyGitHub calls are mocked — never touches real GitHub API.
Tests verify:
- Inline comment posted for each passed issue
- Summary comment posted exactly once
- GithubException on a single inline comment is caught and does not abort
- PostResult counts are accurate
- Auth path (GithubIntegration) is called with correct credentials
"""
import pytest
from dataclasses import dataclass, field
from typing import List
from unittest.mock import MagicMock, patch, call

from github import GithubException

from app.services.github_poster import post_review_comments, PostResult
from app.services.guardrail import GuardrailResult


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_issue(
    line_number: int = 10,
    file_path: str = "app/main.py",
    severity: str = "HIGH",
    type: str = "security",
    message: str = "SQL injection risk",
    suggestion: str = "Use parameterised queries",
    evidence_from_tool: str = "bandit: B608 hardcoded SQL",
):
    """Return a mock issue object with file_path attribute (IssueWithPath-like)."""
    issue = MagicMock()
    issue.line_number = line_number
    issue.file_path = file_path
    issue.severity = severity
    issue.type = type
    issue.message = message
    issue.suggestion = suggestion
    issue.evidence_from_tool = evidence_from_tool
    return issue


def _make_guardrail_result(passed_issues=None, filtered_issues=None):
    result = MagicMock(spec=GuardrailResult)
    result.passed_issues = passed_issues or []
    result.filtered_issues = filtered_issues or []
    result.original_confidence = 0.85
    result.applied_confidence_gating = False
    return result


@pytest.fixture
def mock_github_client():
    """
    Patch _get_github_client() to return a mock Github instance.
    Tests that need to control per-issue behaviour override mock_pr directly.
    """
    mock_gh = MagicMock()
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_pr.head.sha = "abc123deadbeef"

    mock_gh.get_repo.return_value = mock_repo
    mock_repo.get_pull.return_value = mock_pr

    with patch("app.services.github_poster._get_github_client", return_value=mock_gh):
        yield mock_gh, mock_repo, mock_pr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPostReviewComments:

    def test_inline_comment_posted_for_each_passed_issue(self, mock_github_client):
        """One create_review_comment call per passed issue."""
        _, _, mock_pr = mock_github_client
        issues = [_make_issue(line_number=i, file_path="app/main.py") for i in [10, 20, 30]]
        gr = _make_guardrail_result(passed_issues=issues)

        result = post_review_comments(
            repo_full_name="owner/repo",
            pr_number=42,
            file_results=[gr],
            summary="## Summary",
        )

        assert mock_pr.create_review_comment.call_count == 3
        assert result.inline_posted == 3
        assert result.inline_failed == 0

    def test_summary_comment_posted_exactly_once(self, mock_github_client):
        """create_issue_comment called once with the pre-built summary string."""
        _, _, mock_pr = mock_github_client
        gr = _make_guardrail_result(passed_issues=[_make_issue()])

        post_review_comments(
            repo_full_name="owner/repo",
            pr_number=1,
            file_results=[gr],
            summary="## My Summary",
        )

        mock_pr.create_issue_comment.assert_called_once_with(body="## My Summary")

    def test_summary_posted_even_with_no_passed_issues(self, mock_github_client):
        """Summary comment is always posted, even when guardrail filtered everything."""
        _, _, mock_pr = mock_github_client
        gr = _make_guardrail_result(passed_issues=[])

        result = post_review_comments(
            repo_full_name="owner/repo",
            pr_number=5,
            file_results=[gr],
            summary="## All filtered",
        )

        mock_pr.create_review_comment.assert_not_called()
        mock_pr.create_issue_comment.assert_called_once()
        assert result.inline_posted == 0
        assert result.summary_posted is True

    def test_github_exception_on_inline_comment_is_caught_does_not_raise(self, mock_github_client):
        """
        GithubException on create_review_comment is caught per-issue.
        Remaining issues and summary are still posted.
        """
        _, _, mock_pr = mock_github_client
        exc = GithubException(status=422, data={"message": "Unprocessable"}, headers={})
        # First call raises, second succeeds
        mock_pr.create_review_comment.side_effect = [exc, None]

        issues = [_make_issue(line_number=10), _make_issue(line_number=20)]
        gr = _make_guardrail_result(passed_issues=issues)

        # Must not raise
        result = post_review_comments(
            repo_full_name="owner/repo",
            pr_number=7,
            file_results=[gr],
            summary="## Summary",
        )

        assert result.inline_posted == 1
        assert result.inline_failed == 1
        assert len(result.failed_issues) == 1
        # Summary still posted despite one failure
        mock_pr.create_issue_comment.assert_called_once()

    def test_all_inline_comments_fail_summary_still_posts(self, mock_github_client):
        """Even if every inline comment fails, summary comment is still attempted."""
        _, _, mock_pr = mock_github_client
        exc = GithubException(status=422, data={"message": "Unprocessable"}, headers={})
        mock_pr.create_review_comment.side_effect = exc

        issues = [_make_issue(line_number=10), _make_issue(line_number=20)]
        gr = _make_guardrail_result(passed_issues=issues)

        result = post_review_comments(
            repo_full_name="owner/repo",
            pr_number=8,
            file_results=[gr],
            summary="## Summary",
        )

        assert result.inline_posted == 0
        assert result.inline_failed == 2
        assert result.summary_posted is True

    def test_summary_failure_is_logged_does_not_raise(self, mock_github_client):
        """GithubException on summary comment is caught — does not abort the task."""
        _, _, mock_pr = mock_github_client
        exc = GithubException(status=500, data={"message": "Internal Server Error"}, headers={})
        mock_pr.create_issue_comment.side_effect = exc

        gr = _make_guardrail_result(passed_issues=[_make_issue()])

        # Must not raise
        result = post_review_comments(
            repo_full_name="owner/repo",
            pr_number=9,
            file_results=[gr],
            summary="## Summary",
        )

        assert result.summary_posted is False
        # inline still posted
        assert result.inline_posted == 1

    def test_multiple_file_results_all_issues_posted(self, mock_github_client):
        """Issues from multiple GuardrailResult objects are all posted."""
        _, _, mock_pr = mock_github_client
        gr1 = _make_guardrail_result(passed_issues=[
            _make_issue(line_number=5, file_path="app/a.py"),
            _make_issue(line_number=6, file_path="app/a.py"),
        ])
        gr2 = _make_guardrail_result(passed_issues=[
            _make_issue(line_number=99, file_path="app/b.py"),
        ])

        result = post_review_comments(
            repo_full_name="owner/repo",
            pr_number=10,
            file_results=[gr1, gr2],
            summary="## Summary",
        )

        assert result.inline_posted == 3
        assert mock_pr.create_review_comment.call_count == 3

    def test_correct_commit_sha_used_for_inline_comments(self, mock_github_client):
        """Inline comments use PR head SHA, not a hardcoded value."""
        _, _, mock_pr = mock_github_client
        mock_pr.head.sha = "deadbeef1234"
        issue = _make_issue(line_number=10, file_path="app/main.py")
        gr = _make_guardrail_result(passed_issues=[issue])

        post_review_comments(
            repo_full_name="owner/repo",
            pr_number=11,
            file_results=[gr],
            summary="## S",
        )

        call_kwargs = mock_pr.create_review_comment.call_args.kwargs
        assert call_kwargs["commit_id"] == "deadbeef1234"

    def test_auth_uses_app_id_and_private_key_from_settings(self):
        """
        GithubIntegration is called with the App ID and private key from settings.
        Verifies no hardcoded credentials in the service.
        """
        mock_installation = MagicMock()
        mock_gh = MagicMock()
        mock_repo = MagicMock()
        mock_pr = MagicMock()
        mock_pr.head.sha = "abc"
        mock_repo.get_pull.return_value = mock_pr
        mock_gh.get_repo.return_value = mock_repo
        mock_installation.get_github_for_installation.return_value = mock_gh

        with patch("app.services.github_poster.GithubIntegration") as mock_integration_cls, \
             patch("app.services.github_poster.get_settings") as mock_get_settings:

            mock_settings = MagicMock()
            mock_settings.GITHUB_APP_ID = 12345
            mock_settings.GITHUB_PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----"
            mock_get_settings.return_value = mock_settings

            mock_integration = MagicMock()
            mock_integration.get_installations.return_value = iter([mock_installation])
            mock_integration_cls.return_value = mock_integration

            post_review_comments(
                repo_full_name="owner/repo",
                pr_number=1,
                file_results=[],
                summary="## S",
            )

        mock_integration_cls.assert_called_once_with(
            integration_id=12345,
            private_key="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
        )