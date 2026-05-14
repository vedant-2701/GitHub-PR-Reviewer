# ACE Code Review Agent — Progress

## Status: In Progress

## Completed
- [x] FastAPI scaffold, HMAC validation, Celery/Redis, Docker ✓
- [x] ORM models (Review with VerdictEnum, FilteredIssue) ✓
- [x] app/schemas/diff.py — FileDiff ✓
- [x] app/services/diff_parser.py — 58/58 tests passing ✓
- [x] Static analysis tools: PythonAnalyser, JavaScriptAnalyser ✓
- [x] app/utils/groq_client.py ✓
- [x] app/utils/constants.py ✓
- [x] app/schemas/review.py — locked schema ✓
- [x] app/services/review_agent.py ✓
- [x] app/services/guardrail.py ✓
- [x] app/config.py — GITHUB_PRIVATE_KEY @property ✓
- [x] app/services/github_poster.py ✓
- [x] app/tasks/review_task.py — Celery entry point only ✓
- [x] app/pipeline/ — 6 focused modules (178/178 tests passing) ✓
- [x] tests/test_github_poster.py — 8 tests ✓
- [x] tests/test_review_task.py — 31/31 passing ✓
- [x] Alembic setup — env.py wired to async engine ✓
- [x] Initial migration — f92e12f20e01, verified against PostgreSQL ✓
- [x] app/main.py — init_db() removed, Alembic note in lifespan ✓
- [x] docker-compose.yml — worker port bug fixed (db:5434 → db:5432) ✓
- [x] tests/test_integration.py — 3/3 passing against real PostgreSQL ✓
- [x] app/pipeline/github_fetcher.py — GithubIntegration deprecated API fixed ✓
- [ ] React dashboard
- [ ] Railway + Vercel deployment

## Decisions Made This Session
- Alembic env.py uses run_async_migrations() pattern — required for asyncpg engine
- render_as_batch=True in env.py — needed for SQLite ALTER TABLE, harmless on PostgreSQL
- compare_type=True — ensures Enum column changes are detected on future migrations
- downgrade() now drops verdict_enum type — autogenerate omits this, causes
  "type already exists" on PostgreSQL if you downgrade and re-upgrade
- init_db() / create_all removed from main.py lifespan — Alembic is now the
  single source of truth for schema
- Integration tests use clean_test_rows fixture (job_id prefix match) — never
  touches real data
- reset_db_pool fixture disposes global engine before each test — forces asyncpg
  to create fresh connections in the current event loop. Test-only concern;
  production has a single event loop for the process lifetime, this cannot happen
- worker DATABASE_URL bug fixed: was @db:5434 (host port), must be @db:5432
  (container port) — Docker service-to-service always uses container port
- GithubIntegration deprecated constructor fixed: now uses
  auth=Auth.AppAuth(app_id=..., private_key=...) per PyGitHub latest API
- patch targets must match where name is looked up, not where defined:
  fetch_pr_files → app.pipeline.orchestrator.fetch_pr_files
  call_groq_with_retry → app.services.review_agent.call_groq_with_retry
  post_review_comments → app.pipeline.orchestrator.post_review_comments
- post_review_comments is sync (PyGitHub blocking) → MagicMock not AsyncMock

## Known Limitations
- IssueWithPath is not a Pydantic model — manual sync required if Issue schema changes
- PyGitHub diff reconstruction via get_files().patch — verified shape is correct
  via integration test; full smoke test against a real PR still recommended
- flake8 not installed locally — syntax tool logs a warning and skips gracefully.
  Install if syntax analysis coverage is needed: pip install flake8