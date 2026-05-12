# ACE Code Review Agent — Progress

## Status: In Progress

## Completed
- [x] FastAPI scaffold (main.py, config.py, database.py, all routers wired)
- [x] GitHub App setup + HMAC validation (hmac_validator.py — 7 tests passing)
- [x] Celery + Redis task queue (celery_worker.py, review_task.py stub with job_id return)
- [x] Docker compose (local) — FastAPI + PostgreSQL + Redis + Celery worker
- [x] Dockerfile (backend)
- [x] .env.example
- [x] requirements.txt (pinned versions)
- [x] Structured logging (logging_config.py)
- [x] ORM models (Review, FilteredIssue) with relationships + cascade delete
- [x] app/schemas/diff.py — FileDiff Pydantic schema
- [x] app/services/diff_parser.py — unified diff → FileDiff (58/58 tests passing)
- [ ] Static analysis tools (bandit/radon/ast/ESLint)
- [ ] Groq client + retry logic
- [ ] LangChain AgentExecutor
- [ ] Guardrail layer + tests
- [ ] GitHub comment poster
- [ ] PostgreSQL models + Alembic migrations (TODO marked in database.py)
- [ ] React dashboard
- [ ] Railway deployment (backend)
- [ ] Vercel deployment (frontend)

## Current Session
Session 2 complete: ORM models + diff parser.
58/58 tests passing. All files verified present.

## Decisions Made This Session
- verdict stored as PostgreSQL native Enum (VerdictEnum) — DB-level enforcement,
  Alembic ALTER TYPE migration required if values change. Accepted trade-off.
- context_lines built from file_content (full file at HEAD), not from diff context lines.
  Gives controlled ±20 lines independent of GitHub's default ±3 diff context.
- Empty diff returns FileDiff with empty added_line_numbers (not None).
  Guardrail will filter all issues for such files — correct behaviour, not parser's concern.
- Blank context lines in unified diff (bare "" with no prefix) are correctly ignored
  by the parser and do not advance the line counter. Documented in test comments.

## Blockers
None