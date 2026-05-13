# app/pipeline/github_fetcher.py
"""
GitHub I/O helpers (synchronous — PyGitHub is not async).

Responsibilities:
  - Authenticate as the GitHub App installation.
  - Fetch per-file diff data (raw_diff, file_content, filename) for a PR.

Both functions are synchronous and must NOT be called from inside an async
context without an executor. In the review pipeline they are called from
_async_pipeline() which runs inside asyncio.run(), so the event loop is
not blocked at the Python level (the Celery task itself is synchronous).

Error handling:
  - GithubException propagates to the caller (orchestrator.py → task),
    which marks the Celery job FAILED. No silent swallowing here.
  - UnicodeDecodeError on file content is caught per-file; raw_diff is
    rewritten to the "Binary files … differ" sentinel so that
    parse_file_diff()._should_skip() handles it correctly.
"""
import logging
from typing import List

from github import Github, GithubException, GithubIntegration

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def get_installation_client() -> Github:
    """
    Return a GitHub client authenticated as the App installation.

    Raises:
        FileNotFoundError  — missing .pem file (fatal, propagates to task).
        GithubException    — bad credentials or no installation found (fatal).
    """
    integration = GithubIntegration(
        integration_id=settings.GITHUB_APP_ID,
        private_key=settings.GITHUB_PRIVATE_KEY,
    )
    installations = integration.get_installations()
    installation = next(iter(installations), None)
    if installation is None:
        raise GithubException(
            status=401,
            data={"message": "No GitHub App installation found."},
        )
    return installation.get_github_for_installation()


def fetch_pr_files(
    repo_full_name: str,
    pr_number: int,
) -> tuple[List[tuple[str, str, str]], str]:
    """
    Fetch per-file diff data from GitHub for a given PR.

    Returns:
        (file_data, head_sha)

        file_data — list of (raw_diff, file_content, filename) tuples, one
                    per file in the PR.

            raw_diff      Unified diff string with standard --- / +++ headers.
                          parse_file_diff() expects this exact format.
            file_content  Full file content at PR HEAD (used for context_lines).
                          Empty string for deleted files or files we cannot decode.
            filename      File path as reported by GitHub (e.g. "backend/app/main.py").

        head_sha — the commit SHA at the PR HEAD, used for permalink anchors.

    Notes:
        Binary files have no patch attribute. raw_diff is set to the sentinel
        "Binary files … differ" string so parse_file_diff()._should_skip()
        detects and skips them correctly.

        GithubException propagates — caller marks the Celery task FAILED.
    """
    gh = get_installation_client()
    repo = gh.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)
    head_sha = pr.head.sha

    file_data: List[tuple[str, str, str]] = []

    for gh_file in pr.get_files():
        filename = gh_file.filename

        # Build raw_diff in the format parse_file_diff() expects.
        if gh_file.patch:
            raw_diff = (
                f"--- a/{filename}\n"
                f"+++ b/{filename}\n"
                f"{gh_file.patch}"
            )
        elif gh_file.status == "removed":
            # Deleted file — empty added_line_numbers; guardrail filters everything.
            raw_diff = f"--- a/{filename}\n+++ /dev/null\n"
        else:
            # Binary file — triggers _should_skip() in parse_file_diff().
            raw_diff = f"Binary files a/{filename} and b/{filename} differ"

        # Fetch full file content at HEAD for context_lines.
        # Deleted files and binary files get empty string.
        file_content = ""
        if gh_file.status != "removed" and gh_file.patch:
            try:
                content_obj = repo.get_contents(filename, ref=head_sha)
                file_content = content_obj.decoded_content.decode("utf-8", errors="replace")
            except GithubException as e:
                # File in diff but not fetchable (permissions, race condition).
                # Context lines will be empty — acceptable, review still proceeds.
                logger.warning(
                    "Could not fetch content for %s (status=%d): %s — using empty content",
                    filename, e.status, e.data.get("message", str(e)),
                )
            except UnicodeDecodeError:
                # Binary content despite having a patch (edge case).
                logger.warning("UnicodeDecodeError reading %s — treating as binary", filename)
                raw_diff = f"Binary files a/{filename} and b/{filename} differ"

        file_data.append((raw_diff, file_content, filename))

    return file_data, head_sha
