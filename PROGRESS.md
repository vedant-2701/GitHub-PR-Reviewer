# ACE Code Review Agent — Progress

## Status: In Progress

## Completed
- [x] FastAPI scaffold, HMAC validation, Celery/Redis, Docker ✓
- [x] ORM models (Review with VerdictEnum, FilteredIssue) ✓
- [x] app/schemas/diff.py — FileDiff ✓
- [x] app/services/diff_parser.py — 58/58 tests passing; imports skip rules from constants.py ✓
- [x] Static analysis tools: PythonAnalyser, JavaScriptAnalyser ✓
- [x] app/utils/groq_client.py ✓
- [x] app/utils/constants.py — VALID_TOOL_NAMES, INTER_FILE_DELAY, SKIP_FILENAMES, SKIP_EXTENSIONS, MIGRATION_PATH_PATTERN ✓
- [x] app/schemas/review.py — locked schema ✓
- [x] app/services/review_agent.py ✓
- [x] app/services/guardrail.py ✓
- [x] app/config.py — GITHUB_PRIVATE_KEY @property ✓
- [x] app/services/github_poster.py ✓
- [x] app/tasks/review_task.py — trimmed to Celery entry point only (~88 lines) ✓
- [x] app/pipeline/ — full pipeline refactored into 6 focused modules (178/178 tests passing) ✓
  - pipeline/types.py — IssueWithPath, FileReviewOutcome dataclasses
  - pipeline/github_fetcher.py — get_installation_client, fetch_pr_files
  - pipeline/summary_builder.py — compute_overall_verdict, build_summary (pure, no I/O)
  - pipeline/file_reviewer.py — review_single_file, review_all_files (step 4a/b/c)
  - pipeline/db_writer.py — save_review (atomic Review + FilteredIssue commit)
  - pipeline/orchestrator.py — async_pipeline (step sequencer, reads like a spec)
- [x] tests/test_github_poster.py — 8 tests ✓
- [x] tests/test_review_task.py — 31/31 passing (patch targets updated to pipeline submodules) ✓
- [ ] PostgreSQL Alembic migrations
- [ ] React dashboard
- [ ] Railway + Vercel deployment

## Decisions Made This Session
- constants.py is the single source of truth for skip rules. diff_parser imports
  SKIP_FILENAMES, SKIP_EXTENSIONS, MIGRATION_PATH_PATTERN — no private definitions.
- _compute_overall_verdict() → compute_overall_verdict() in pipeline/summary_builder.py,
  extracted so verdict is stored in the Review ORM row and queryable independently.
- Review ORM fields: repo (not repo_full_name), job_id (unique, indexed), verdict
  (VerdictEnum), confidence (float). VerdictEnum defined in models/review.py.
- job_id = self.request.id passed through async_pipeline() for full traceability.
- review_task.py refactored into app/pipeline/ package. Task file is now entry-point
  only. All pipeline logic is Celery-independent and testable in isolation.
- flush-before-commit pattern in db_writer.py: db.flush() assigns real review_id,
  then log_filtered_issues() writes FilteredIssue rows, then single db.commit().
- pipeline/ placed at app/ level (not under tasks/) — logic is Celery-agnostic
  and could be triggered by a future API endpoint or CLI tool.

## Known Limitations
- IssueWithPath is not a Pydantic model — manual sync required if Issue schema changes.
- PyGitHub diff reconstruction via get_files().patch — format assumed correct for
  parse_file_diff(); should be verified against a real PR in the smoke test.