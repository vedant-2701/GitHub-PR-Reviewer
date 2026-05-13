# ACE Code Review Agent — Progress

## Status: In Progress

## Completed
- [x] FastAPI scaffold (main.py, config.py, database.py, all routers wired)
- [x] GitHub App setup + HMAC validation (hmac_validator.py — 7 tests passing)
- [x] Celery + Redis task queue (celery_worker.py, review_task.py stub)
- [x] Docker compose (local) + Dockerfile
- [x] .env.example, requirements.txt, structured logging
- [x] ORM models (Review, FilteredIssue)
- [x] app/schemas/diff.py — FileDiff
- [x] app/services/diff_parser.py — 58/58 tests passing
- [x] app/schemas/analysis.py — ToolFindings, ToolResult
- [x] app/tools/registry.py — LanguageAnalyser ABC + @register + get_analyser()
- [x] app/tools/python/ — PythonAnalyser + bandit, radon, flake8 runners
- [x] app/tools/javascript/ — JavaScriptAnalyser + eslint runner
- [x] app/utils/language.py — Language enum + detect_language()
- [x] app/services/static_analysis.py — registry-driven (46/46 tests passing)
- [x] app/utils/groq_client.py — retry logic (46/46 tests passing)
- [x] app/utils/constants.py — VALID_TOOL_NAMES frozenset
- [x] app/schemas/review.py — locked schema, both validators
- [x] app/services/review_agent.py — Groq call + 3-attempt parse loop
- [x] app/services/guardrail.py — 3 checks + DB logger
- [x] tests/test_review_agent.py — 5/5 passing (all Groq mocked)
- [x] tests/test_guardrail.py — 19/19 passing
- [ ] app/services/github_poster.py
- [ ] review_task.py — full pipeline wired (Celery task)
- [ ] PostgreSQL Alembic migrations
- [ ] React dashboard
- [ ] Railway + Vercel deployment

## Current Session
Session 4 complete: review schema, review agent, guardrail layer, tests.
24/24 tests passing.

## Decisions Made This Session
- Option A (single retry loop in review_file) over separate parse_groq_response()
  wrapper. Fewer indirection layers, same behaviour.
- No LangChain AgentExecutor in review_agent.py yet. Static analysis runs before
  this function is called; findings are passed in as ToolFindings. AgentExecutor
  is reserved for future multi-step reasoning. Documented in review_agent.py docstring.
- VALID_TOOL_NAMES defined as frozenset in app/utils/constants.py, not inline in
  guardrail.py. Single source of truth — adding a new tool requires one edit.
- FilteredIssueData (dataclass in guardrail.py) named to avoid collision with
  FilteredIssue ORM model.
- Added 5th test case (GroqRateLimitError propagation) beyond the 4 specified.
  The early-exit path was untested and handles a real production failure mode.

## Blockers
None