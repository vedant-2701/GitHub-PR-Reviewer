"""
Integration test — full pipeline against a real PostgreSQL database.

What this tests:
- parse_file_diff, run_static_analysis, guardrail_check, save_review,
  and log_filtered_issues all run for real.
- Only fetch_pr_files (GitHub) and call_groq_with_retry (Groq) are mocked.
- fetch_pr_files is synchronous (PyGitHub is blocking) — patch with MagicMock.
- call_groq_with_retry is async — patch with AsyncMock.

Prerequisites (run before pytest):
    docker compose up -d db
    cd backend && alembic upgrade head

Run:
    DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5434/github_reviewer \
    pytest tests/test_integration.py -v -m integration

Exclude from default runs:
    pytest tests/ -v -m "not integration"
"""
import json
import os

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.filtered_issue import FilteredIssue
from app.models.review import Review, VerdictEnum
from app.pipeline.orchestrator import async_pipeline

# ── Test database URL ─────────────────────────────────────────────────────────
TEST_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5434/github_reviewer",
)

# ── Fake data ─────────────────────────────────────────────────────────────────
FAKE_REPO = "test-org/test-repo"
FAKE_PR = 42
FAKE_JOB_ID = "celery-task-id-integration-test-001"
FAKE_HEAD_SHA = "abc123def456"

# fetch_pr_files returns (List[tuple[raw_diff, file_content, filename]], head_sha)
# raw_diff must be a valid unified diff so parse_file_diff can process it.
# The subprocess call on line 6 triggers bandit B602 — real static analysis
# will find it and give the LLM something to ground evidence against.
FAKE_RAW_DIFF = """\
--- a/app/utils/runner.py
+++ b/app/utils/runner.py
@@ -1,3 +1,12 @@
+import os
+import subprocess
+
 def main():
-    pass
+    user_input = input("Enter command: ")
+    subprocess.call(user_input, shell=True)
+    result = eval(user_input)
+    return result
+
+if __name__ == "__main__":
+    main()
"""

FAKE_FILE_CONTENT = """\
import os
import subprocess

def main():
    user_input = input("Enter command: ")
    subprocess.call(user_input, shell=True)
    result = eval(user_input)
    return result

if __name__ == "__main__":
    main()
"""

FAKE_FILENAME = "app/utils/runner.py"

# Correct shape: (List[tuple[str, str, str]], str)
FAKE_FETCH_RETURN = (
    [(FAKE_RAW_DIFF, FAKE_FILE_CONTENT, FAKE_FILENAME)],
    FAKE_HEAD_SHA,
)

# Valid Groq response — line 6 is within the diff's added lines.
# evidence_from_tool references "bandit" — passes grounding check.
FAKE_GROQ_RESPONSE = json.dumps({
    "issues": [
        {
            "line_number": 6,
            "type": "security",
            "severity": "HIGH",
            "message": "subprocess called with shell=True and user input — command injection risk.",
            "suggestion": "Use subprocess.run() with a list of arguments and shell=False.",
            "evidence_from_tool": "bandit: B602 subprocess_popen_with_shell_equals_true — severity HIGH",
        }
    ],
    "summary": "One high-severity security issue found. Request changes.",
    "overall_verdict": "REQUEST_CHANGES",
    "confidence": 0.92,
    "files_reviewed": [FAKE_FILENAME],
})

# Groq response with a hallucinated line 999 — not in the diff.
# Line validation guardrail must filter this issue.
FAKE_GROQ_RESPONSE_HALLUCINATED = json.dumps({
    "issues": [
        {
            "line_number": 999,
            "type": "security",
            "severity": "HIGH",
            "message": "hallucinated issue on nonexistent line",
            "suggestion": "fix it",
            "evidence_from_tool": "bandit: B101 assert_used — severity LOW",
        }
    ],
    "summary": "Issue found.",
    "overall_verdict": "REQUEST_CHANGES",
    "confidence": 0.85,
    "files_reviewed": [FAKE_FILENAME],
})


# ── Fixtures ──────────────────────────────────────────────────────────────────
# All fixtures are function-scoped (default).
#
# Why not session-scoped engine?
# asyncpg connections are bound to the event loop they were created on.
# pytest-asyncio gives each test function its own event loop by default.
# A session-scoped engine created in loop #1 will fail in loop #2 with:
#   "RuntimeError: Task got Future attached to a different loop"
# Function scope means a fresh engine+connection per test — acceptable cost
# for an integration suite of this size.

@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Yield a clean async session. Rolls back after each test."""
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()

@pytest_asyncio.fixture(autouse=True)
async def reset_db_pool():
    """
    Dispose the global engine's connection pool before each test.
    Forces asyncpg to create fresh connections in the current event loop.
    Without this, connections created in loop N fail in loop N+1.
    """
    from app.database import engine
    await engine.dispose()
    yield
    await engine.dispose()

@pytest_asyncio.fixture(autouse=True)
async def clean_test_rows(db_engine):
    """
    Delete rows written by integration tests before and after each test.
    Matches on job_id prefix — never touches real data.
    Uses its own session separate from db_session to avoid connection conflicts.
    """
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def _delete() -> None:
        async with session_factory() as session:
            await session.execute(
                text(
                    "DELETE FROM filtered_issues WHERE review_id IN "
                    "(SELECT id FROM reviews WHERE job_id LIKE 'celery-task-id-integration-test%')"
                )
            )
            await session.execute(
                text("DELETE FROM reviews WHERE job_id LIKE 'celery-task-id-integration-test%'")
            )
            await session.commit()

    await _delete()
    yield
    await _delete()


# ── Shared mock builder ───────────────────────────────────────────────────────
def pipeline_mocks(groq_response: str, inline_posted: int = 1) -> list:
    """
    Return patch context managers for one pipeline run.

    fetch_pr_files       — sync (PyGitHub is blocking) → MagicMock
    call_groq_with_retry — async                       → AsyncMock
    post_review_comments — async                       → AsyncMock
    """
    PostResult = type("PostResult", (), {
        "inline_posted": inline_posted,
        "inline_failed": 0,
        "summary_posted": True,
    })
    return [
        patch(
            "app.pipeline.orchestrator.fetch_pr_files",
            new_callable=MagicMock,
            return_value=FAKE_FETCH_RETURN,
        ),
        patch(
            "app.services.review_agent.call_groq_with_retry",
            new_callable=AsyncMock,
            return_value=groq_response,
        ),
        patch(
            "app.pipeline.orchestrator.post_review_comments",
            new_callable=MagicMock,
            return_value=PostResult(),
        ),
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────
async def fetch_review(session: AsyncSession, job_id: str) -> Review | None:
    result = await session.execute(select(Review).where(Review.job_id == job_id))
    return result.scalar_one_or_none()


async def fetch_filtered_issues(session: AsyncSession, review_id: int) -> list[FilteredIssue]:
    result = await session.execute(
        select(FilteredIssue).where(FilteredIssue.review_id == review_id)
    )
    return list(result.scalars().all())


# ── Tests ─────────────────────────────────────────────────────────────────────
@pytest.mark.integration
@pytest.mark.asyncio
async def test_pipeline_writes_review_row(db_session):
    """
    Happy path: Review row is written to DB with correct fields.
    GitHub (sync) and Groq (async) are mocked. Everything else runs for real.
    """
    mocks = pipeline_mocks(FAKE_GROQ_RESPONSE, inline_posted=1)
    with mocks[0], mocks[1], mocks[2]:
        await async_pipeline(
            repo_full_name=FAKE_REPO,
            pr_number=FAKE_PR,
            job_id=FAKE_JOB_ID,
        )

    review = await fetch_review(db_session, FAKE_JOB_ID)

    assert review is not None, "Review row was not written to the database"
    assert review.repo == FAKE_REPO
    assert review.pr_number == FAKE_PR
    assert review.job_id == FAKE_JOB_ID
    assert review.verdict == VerdictEnum.REQUEST_CHANGES
    assert review.confidence is not None
    assert 0.0 <= review.confidence <= 1.0
    assert FAKE_FILENAME in review.files_reviewed
    assert isinstance(review.files_reviewed, list)
    assert isinstance(review.files_skipped, list)
    assert review.created_at is not None
    assert review.total_issues_posted >= 0
    assert review.total_issues_filtered >= 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_filtered_issues_have_real_review_id(db_session):
    """
    FilteredIssue rows must reference the real Review.id — not null, not zero.
    This specifically tests the flush-before-commit ordering in db_writer.py.

    Uses a Groq response with line 999 (not in diff) to guarantee at least one
    issue is filtered by the line validation guardrail check.
    """
    job_id = "celery-task-id-integration-test-002"

    mocks = pipeline_mocks(FAKE_GROQ_RESPONSE_HALLUCINATED, inline_posted=0)
    with mocks[0], mocks[1], mocks[2]:
        await async_pipeline(
            repo_full_name=FAKE_REPO,
            pr_number=FAKE_PR,
            job_id=job_id,
        )

    review = await fetch_review(db_session, job_id)
    assert review is not None, "Review row was not written"
    assert review.id is not None
    assert review.id > 0

    filtered = await fetch_filtered_issues(db_session, review.id)
    assert len(filtered) >= 1, (
        "Expected at least one filtered issue — line 999 should be caught by "
        "line validation guardrail, but got none. Check that guardrail_check() "
        "is running and all_filtered_issues is being accumulated in orchestrator."
    )

    for fi in filtered:
        assert fi.review_id == review.id, (
            f"FilteredIssue.review_id={fi.review_id} does not match "
            f"Review.id={review.id} — flush-before-commit ordering is broken"
        )
        assert fi.review_id > 0
        assert fi.filter_reason, "filter_reason must not be empty"
        assert fi.file == FAKE_FILENAME


@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_id_unique_constraint(db_session):
    """
    Running the pipeline twice with the same job_id must raise a unique
    constraint violation. Guards against duplicate Celery task execution.
    """
    from sqlalchemy.exc import IntegrityError

    # First run — must succeed
    mocks = pipeline_mocks(FAKE_GROQ_RESPONSE, inline_posted=1)
    with mocks[0], mocks[1], mocks[2]:
        await async_pipeline(
            repo_full_name=FAKE_REPO,
            pr_number=FAKE_PR,
            job_id=FAKE_JOB_ID,
        )

    # Second run with same job_id — must fail on unique constraint.
    # SQLAlchemy wraps asyncpg UniqueViolationError as IntegrityError.
    mocks2 = pipeline_mocks(FAKE_GROQ_RESPONSE, inline_posted=1)
    with pytest.raises(IntegrityError):
        with mocks2[0], mocks2[1], mocks2[2]:
            await async_pipeline(
                repo_full_name=FAKE_REPO,
                pr_number=FAKE_PR,
                job_id=FAKE_JOB_ID,
            )