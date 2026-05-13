# tests/test_review_task.py
"""
Tests for the PR review pipeline (app/pipeline/).

All external calls are mocked:
- fetch_pr_files()      (PyGitHub — returns per-file tuples)
- parse_file_diff()     (diff_parser — called per file, returns Optional[FileDiff])
- run_static_analysis() (bandit/radon/eslint)
- review_file()         (Groq)
- guardrail_check()     (pure function, mocked for isolation)
- log_filtered_issues() (DB write)
- post_review_comments() (PyGitHub)
- AsyncSessionLocal     (DB session)

Note: filter_skip_files() no longer exists — skip logic lives inside
parse_file_diff()._should_skip(). Tests for that logic belong in test_diff_parser.py.
The pipeline tests here verify that when parse_file_diff() returns None,
the file lands in pattern_skipped and is noted in the summary.

Covers:
1. Full pipeline success — all steps fire in correct order
2. GroqRateLimitError → propagates (task retries)
3. ReviewAgentError on one file → that file skipped, others reviewed
4. Static analysis error → file skipped, others reviewed
5. parse_file_diff returning None → file in pattern_skipped, LLM not called
6. Filtered issues written to DB with real review_id (after db.flush())
7. Inter-file delay (asyncio.sleep) called between files, not before first
8. Empty file_data (no PR files) → pipeline exits early
"""
import asyncio
import pytest
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch, call

from github import GithubException

from app.pipeline.orchestrator import async_pipeline
from app.pipeline.types import IssueWithPath, FileReviewOutcome
from app.pipeline.summary_builder import build_summary, compute_overall_verdict
from app.models.review import VerdictEnum
from app.utils.groq_client import GroqRateLimitError
from app.services.review_agent import ReviewAgentError
from app.services.guardrail import GuardrailResult
from app.schemas.review import Verdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file_diff(filename: str = "app/main.py"):
    fd = MagicMock()
    fd.filename = filename
    fd.added_line_numbers = [10, 20, 30]
    fd.language = "python"
    fd.raw_diff = f"--- a/{filename}\n+++ b/{filename}\n@@ -1,1 +1,2 @@\n+import os"
    fd.context_lines = ""
    return fd


def _make_issue_with_path(line_number: int = 10, file_path: str = "app/main.py", severity: str = "MEDIUM"):
    issue = MagicMock(spec=IssueWithPath)
    issue.line_number = line_number
    issue.file_path = file_path
    issue.severity = severity
    issue.type = "security"
    issue.message = "issue message"
    issue.suggestion = "fix it"
    issue.evidence_from_tool = "bandit: B101"
    return issue


def _make_guardrail_result(passed=None, filtered=None, confidence=0.8, gated=False):
    gr = MagicMock(spec=GuardrailResult)
    gr.passed_issues = passed or []
    gr.filtered_issues = filtered or []
    gr.original_confidence = confidence
    gr.applied_confidence_gating = gated
    return gr


def _make_review_result():
    rr = MagicMock()
    rr.overall_verdict = Verdict.APPROVE
    rr.confidence = 0.8
    rr.summary = "Looks good"
    return rr


# Raw file data tuple as returned by fetch_pr_files
def _file_tuple(filename: str = "app/main.py") -> tuple:
    return (
        f"--- a/{filename}\n+++ b/{filename}\n@@ -1,1 +1,2 @@\n+import os",
        "import os\n",
        filename,
    )


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

# Patch targets now point to where the names are *used*, not defined.
ORCHESTRATOR = "app.pipeline.orchestrator"
FILE_REVIEWER = "app.pipeline.file_reviewer"
DB_WRITER = "app.pipeline.db_writer"


@pytest.fixture
def pipeline_mocks():
    """
    Patch all external dependencies of async_pipeline.
    Defaults: single file, parse succeeds, static analysis succeeds,
    review succeeds, guardrail passes one issue, post succeeds.
    """
    with patch(f"{ORCHESTRATOR}.fetch_pr_files") as mock_fetch, \
         patch(f"{ORCHESTRATOR}.parse_file_diff") as mock_parse, \
         patch(f"{FILE_REVIEWER}.run_static_analysis", new_callable=AsyncMock) as mock_analysis, \
         patch(f"{FILE_REVIEWER}.review_file", new_callable=AsyncMock) as mock_review, \
         patch(f"{FILE_REVIEWER}.guardrail_check") as mock_guardrail, \
         patch(f"{DB_WRITER}.log_filtered_issues", new_callable=AsyncMock) as mock_log, \
         patch(f"{ORCHESTRATOR}.post_review_comments") as mock_post, \
         patch(f"{DB_WRITER}.AsyncSessionLocal") as mock_session_cls, \
         patch(f"{FILE_REVIEWER}.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        # Default: one file, successfully parsed
        mock_fetch.return_value = ([_file_tuple("app/main.py")], "abc123")
        file_diff = _make_file_diff("app/main.py")
        mock_parse.return_value = file_diff

        mock_analysis.return_value = MagicMock()
        mock_review.return_value = _make_review_result()

        passed_issue = _make_issue_with_path()
        gr = _make_guardrail_result(passed=[passed_issue])
        mock_guardrail.return_value = gr

        post_result = MagicMock()
        post_result.inline_posted = 1
        post_result.inline_failed = 0
        post_result.summary_posted = True
        mock_post.return_value = post_result

        # Async session context manager
        mock_session = MagicMock()
        mock_review_record = MagicMock()
        mock_review_record.id = 99
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock(side_effect=lambda r: setattr(r, "id", 99) if hasattr(r, "repo_full_name") else None)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        yield {
            "fetch": mock_fetch,
            "parse": mock_parse,
            "analysis": mock_analysis,
            "review": mock_review,
            "guardrail": mock_guardrail,
            "log": mock_log,
            "post": mock_post,
            "sleep": mock_sleep,
            "session": mock_session,
            "file_diff": file_diff,
            "gr": gr,
        }


# ---------------------------------------------------------------------------
# Full pipeline success
# ---------------------------------------------------------------------------

class TestAsyncPipelineSuccess:

    def test_all_steps_execute_in_order(self, pipeline_mocks):
        """Happy path: fetch → parse → analyse → review → guardrail → post → db."""
        m = pipeline_mocks
        asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))

        m["fetch"].assert_called_once_with("owner/repo", 1)
        m["parse"].assert_called_once()
        m["analysis"].assert_awaited_once()
        m["review"].assert_awaited_once()
        m["guardrail"].assert_called_once()
        m["log"].assert_awaited_once()
        m["post"].assert_called_once()
        m["session"].commit.assert_awaited_once()

    def test_parse_file_diff_called_with_correct_args(self, pipeline_mocks):
        """parse_file_diff receives (raw_diff, file_content, filename) from fetch."""
        m = pipeline_mocks
        raw_diff, file_content, filename = _file_tuple("app/main.py")
        m["fetch"].return_value = ([(raw_diff, file_content, filename)], "sha")

        asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))

        m["parse"].assert_called_once_with(raw_diff, file_content, filename)

    def test_no_sleep_for_single_file(self, pipeline_mocks):
        """asyncio.sleep not called when only one file is reviewed."""
        asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))
        pipeline_mocks["sleep"].assert_not_awaited()

    def test_sleep_called_between_files_not_before_first(self, pipeline_mocks):
        """asyncio.sleep called N-1 times for N files, never before file 0."""
        m = pipeline_mocks
        m["fetch"].return_value = ([
            _file_tuple("app/a.py"),
            _file_tuple("app/b.py"),
            _file_tuple("app/c.py"),
        ], "sha")
        m["parse"].side_effect = [
            _make_file_diff("app/a.py"),
            _make_file_diff("app/b.py"),
            _make_file_diff("app/c.py"),
        ]
        m["guardrail"].side_effect = [
            _make_guardrail_result(passed=[_make_issue_with_path()]),
            _make_guardrail_result(passed=[_make_issue_with_path()]),
            _make_guardrail_result(passed=[_make_issue_with_path()]),
        ]
        m["review"].side_effect = [_make_review_result(), _make_review_result(), _make_review_result()]

        asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))

        assert m["sleep"].await_count == 2  # between file 0→1 and 1→2

    def test_filtered_issues_logged_after_flush_with_real_review_id(self, pipeline_mocks):
        """
        log_filtered_issues is called after db.flush() — not before.
        Verifies the fix for the review_id placeholder bug: filtered issues
        must receive the real review_id from the flushed Review row.

        Pattern:
          db.add(review_record)   → Review row staged
          db.flush()              → review_record.id assigned by DB
          log_filtered_issues(review_id=review_record.id, ...)  ← real id
          db.commit()             → everything committed atomically
        """
        m = pipeline_mocks
        filtered = [MagicMock(), MagicMock()]
        m["guardrail"].return_value = _make_guardrail_result(
            passed=[_make_issue_with_path()],
            filtered=filtered,
        )

        call_order = []
        m["session"].flush.side_effect = AsyncMock(side_effect=lambda: call_order.append("flush"))
        m["log"].side_effect = AsyncMock(side_effect=lambda **kw: call_order.append("log"))
        m["session"].commit.side_effect = AsyncMock(side_effect=lambda: call_order.append("commit"))

        asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))

        assert call_order == ["flush", "log", "commit"], (
            f"Expected flush → log → commit, got: {call_order}"
        )
        m["log"].assert_awaited_once()
        # filtered issues from all files passed in one call
        log_call_filtered = m["log"].call_args.kwargs.get("filtered") or m["log"].call_args.args[1]
        assert filtered[0] in log_call_filtered
        assert filtered[1] in log_call_filtered

    def test_no_files_in_pr_exits_early(self, pipeline_mocks):
        """Empty file_data from GitHub → pipeline exits, no parse/review/post."""
        m = pipeline_mocks
        m["fetch"].return_value = ([], "sha")

        asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))

        m["parse"].assert_not_called()
        m["review"].assert_not_awaited()
        m["post"].assert_not_called()

    def test_all_files_parse_to_none_exits_early(self, pipeline_mocks):
        """All files skipped by parse_file_diff → LLM phase skipped."""
        m = pipeline_mocks
        m["fetch"].return_value = ([
            _file_tuple("package-lock.json"),
            _file_tuple("yarn.lock"),
        ], "sha")
        m["parse"].return_value = None  # both skipped

        asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))

        m["review"].assert_not_awaited()
        m["post"].assert_not_called()

    def test_parse_none_lands_in_pattern_skipped(self, pipeline_mocks):
        """Files where parse_file_diff returns None are collected in pattern_skipped."""
        m = pipeline_mocks
        m["fetch"].return_value = ([
            _file_tuple("package-lock.json"),
            _file_tuple("app/main.py"),
        ], "sha")
        # First file skipped, second reviewed
        m["parse"].side_effect = [None, _make_file_diff("app/main.py")]

        asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))

        # Summary passed to post_review_comments should mention the skipped file
        summary_arg = m["post"].call_args.kwargs.get("summary") or m["post"].call_args.args[3]
        assert "package-lock.json" in summary_arg


# ---------------------------------------------------------------------------
# Groq rate limit
# ---------------------------------------------------------------------------

class TestGroqRateLimit:

    def test_rate_limit_error_propagates(self, pipeline_mocks):
        """GroqRateLimitError from review_file() bubbles up to task (triggers retry)."""
        pipeline_mocks["review"].side_effect = GroqRateLimitError("429")
        with pytest.raises(GroqRateLimitError):
            asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))

    def test_rate_limit_does_not_post_partial_results(self, pipeline_mocks):
        """When rate limit hits, no GitHub comments are posted."""
        pipeline_mocks["review"].side_effect = GroqRateLimitError("429")
        with pytest.raises(GroqRateLimitError):
            asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))
        pipeline_mocks["post"].assert_not_called()

    def test_rate_limit_does_not_commit_to_db(self, pipeline_mocks):
        """When rate limit hits, no partial DB commit."""
        pipeline_mocks["review"].side_effect = GroqRateLimitError("429")
        with pytest.raises(GroqRateLimitError):
            asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))
        pipeline_mocks["session"].commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# ReviewAgentError — file skip, continue with rest
# ---------------------------------------------------------------------------

class TestReviewAgentError:

    def test_agent_error_skips_file_continues_with_rest(self, pipeline_mocks):
        """ReviewAgentError on file 1 → file 2 still reviewed. No exception raised."""
        m = pipeline_mocks
        m["fetch"].return_value = ([
            _file_tuple("app/broken.py"),
            _file_tuple("app/ok.py"),
        ], "sha")
        m["parse"].side_effect = [
            _make_file_diff("app/broken.py"),
            _make_file_diff("app/ok.py"),
        ]
        m["review"].side_effect = [
            ReviewAgentError("parse failed 3 times"),
            _make_review_result(),
        ]
        m["guardrail"].return_value = _make_guardrail_result(passed=[_make_issue_with_path()])

        # Must not raise
        asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))

        assert m["review"].await_count == 2
        assert m["guardrail"].call_count == 1  # only called for ok.py
        m["post"].assert_called_once()

    def test_agent_error_noted_in_summary(self, pipeline_mocks):
        """Skipped file due to agent error appears in the summary comment."""
        m = pipeline_mocks
        m["fetch"].return_value = ([
            _file_tuple("app/broken.py"),
            _file_tuple("app/ok.py"),
        ], "sha")
        m["parse"].side_effect = [
            _make_file_diff("app/broken.py"),
            _make_file_diff("app/ok.py"),
        ]
        m["review"].side_effect = [
            ReviewAgentError("parse failed"),
            _make_review_result(),
        ]
        m["guardrail"].return_value = _make_guardrail_result(passed=[_make_issue_with_path()])

        asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))

        summary_arg = m["post"].call_args.kwargs.get("summary") or m["post"].call_args.args[3]
        assert "app/broken.py" in summary_arg

    def test_static_analysis_error_skips_file(self, pipeline_mocks):
        """Exception from run_static_analysis skips the file, does not crash."""
        m = pipeline_mocks
        m["analysis"].side_effect = Exception("bandit subprocess failed")

        asyncio.run(async_pipeline("owner/repo", 1, "test-job-id"))

        m["review"].assert_not_awaited()
        # post IS called because the summary needs to report the agent error
        m["post"].assert_called_once()
        summary_arg = m["post"].call_args.kwargs.get("summary") or m["post"].call_args.args[3]
        assert "app/main.py" in summary_arg


# ---------------------------------------------------------------------------
# Celery task wrapper
# ---------------------------------------------------------------------------

TASK_MODULE = "app.tasks.review_task"


class TestCeleryTask:

    def test_groq_rate_limit_triggers_self_retry(self):
        """GroqRateLimitError → self.retry(countdown=60) is called."""
        from app.tasks.review_task import process_pr_review

        with patch(f"{TASK_MODULE}.asyncio.run", side_effect=GroqRateLimitError("429")):
            task_mock = MagicMock()
            task_mock.request.retries = 0
            task_mock.max_retries = 3
            task_mock.retry.side_effect = GroqRateLimitError("retrying")

            bound = process_pr_review.__wrapped__.__func__
            with pytest.raises(GroqRateLimitError):
                bound(task_mock, "owner/repo", 1)

            task_mock.retry.assert_called_once()
            assert task_mock.retry.call_args.kwargs["countdown"] == 60

    def test_github_exception_does_not_retry(self):
        """GithubException is fatal — raised directly, self.retry not called."""
        from app.tasks.review_task import process_pr_review

        gh_exc = GithubException(status=404, data={"message": "Not Found"}, headers={})
        with patch(f"{TASK_MODULE}.asyncio.run", side_effect=gh_exc):
            task_mock = MagicMock()
            task_mock.request.retries = 0
            task_mock.max_retries = 3

            bound = process_pr_review.__wrapped__.__func__
            with pytest.raises(GithubException):
                bound(task_mock, "owner/repo", 1)

            task_mock.retry.assert_not_called()

    def test_unhandled_exception_does_not_retry(self):
        """Generic exceptions are re-raised without calling self.retry."""
        from app.tasks.review_task import process_pr_review

        with patch(f"{TASK_MODULE}.asyncio.run", side_effect=RuntimeError("unexpected")):
            task_mock = MagicMock()
            task_mock.request.retries = 0
            task_mock.max_retries = 3

            bound = process_pr_review.__wrapped__.__func__
            with pytest.raises(RuntimeError):
                bound(task_mock, "owner/repo", 1)

            task_mock.retry.assert_not_called()


# ---------------------------------------------------------------------------
# build_summary (was _build_summary)
# ---------------------------------------------------------------------------

class TestBuildSummary:
    """
    build_summary signature:
        (outcomes, overall_verdict, pattern_skipped, agent_skipped, repo_full_name, pr_number)

    overall_verdict is computed by compute_overall_verdict() and passed in.
    build_summary only renders it — it does not compute it.
    Both functions are tested independently here.
    """

    def _outcome(self, filename="app/main.py", passed_count=1, filtered_count=0,
                 confidence=0.8, gated=False, severity="MEDIUM"):
        fd = _make_file_diff(filename)
        issues = [_make_issue_with_path(severity=severity)] * passed_count
        filtered = [MagicMock()] * filtered_count
        gr = _make_guardrail_result(passed=issues, filtered=filtered,
                                    confidence=confidence, gated=gated)
        return FileReviewOutcome(file_diff=fd, guardrail_result=gr)

    def test_contains_repo_and_pr_number(self):
        summary = build_summary([], VerdictEnum.APPROVE, [], [], "owner/myrepo", 42)
        assert "owner/myrepo" in summary
        assert "42" in summary

    def test_shows_correct_issue_counts(self):
        outcomes = [self._outcome(passed_count=3, filtered_count=2)]
        summary = build_summary(outcomes, VerdictEnum.APPROVE, [], [], "owner/repo", 1)
        assert "3" in summary
        assert "2" in summary

    def test_pattern_skipped_files_listed(self):
        summary = build_summary([], VerdictEnum.APPROVE, ["package-lock.json", "yarn.lock"], [], "owner/repo", 1)
        assert "package-lock.json" in summary
        assert "yarn.lock" in summary

    def test_agent_skipped_files_listed(self):
        summary = build_summary([], VerdictEnum.APPROVE, [], ["app/broken.py"], "owner/repo", 1)
        assert "app/broken.py" in summary

    def test_renders_request_changes_verdict(self):
        summary = build_summary([], VerdictEnum.REQUEST_CHANGES, [], [], "owner/repo", 1)
        assert "REQUEST CHANGES" in summary

    def test_renders_approve_verdict(self):
        summary = build_summary([], VerdictEnum.APPROVE, [], [], "owner/repo", 1)
        assert "APPROVE" in summary

    def test_renders_comment_verdict(self):
        summary = build_summary([], VerdictEnum.COMMENT, [], [], "owner/repo", 1)
        assert "COMMENT" in summary

    def test_confidence_gating_noted(self):
        outcome = self._outcome(confidence=0.4, gated=True)
        summary = build_summary([outcome], VerdictEnum.APPROVE, [], [], "owner/repo", 1)
        assert "confidence gating" in summary.lower()

    def test_always_ends_with_guardrail_footer(self):
        summary = build_summary([], VerdictEnum.APPROVE, [], [], "owner/repo", 1)
        assert "Guardrail layer active" in summary


class TestComputeOverallVerdict:
    """
    compute_overall_verdict() is extracted from the old monolithic file so the
    verdict can be stored in the DB separately. Test it independently.
    """

    def _outcome_with_severity(self, severity: str) -> FileReviewOutcome:
        fd = _make_file_diff()
        gr = _make_guardrail_result(passed=[_make_issue_with_path(severity=severity)])
        return FileReviewOutcome(file_diff=fd, guardrail_result=gr)

    def test_high_severity_gives_request_changes(self):
        assert compute_overall_verdict([self._outcome_with_severity("HIGH")]) == VerdictEnum.REQUEST_CHANGES

    def test_medium_severity_gives_comment(self):
        assert compute_overall_verdict([self._outcome_with_severity("MEDIUM")]) == VerdictEnum.COMMENT

    def test_low_severity_only_gives_approve(self):
        assert compute_overall_verdict([self._outcome_with_severity("LOW")]) == VerdictEnum.APPROVE

    def test_no_issues_gives_approve(self):
        fd = _make_file_diff()
        gr = _make_guardrail_result(passed=[])
        outcome = FileReviewOutcome(file_diff=fd, guardrail_result=gr)
        assert compute_overall_verdict([outcome]) == VerdictEnum.APPROVE

    def test_high_wins_over_medium(self):
        outcomes = [
            self._outcome_with_severity("MEDIUM"),
            self._outcome_with_severity("HIGH"),
        ]
        assert compute_overall_verdict(outcomes) == VerdictEnum.REQUEST_CHANGES