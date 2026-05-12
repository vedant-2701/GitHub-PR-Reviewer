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
- [x] app/tools/language_router.py
- [x] app/services/static_analysis.py — registry-driven, zero language logic (46/46 tests passing)
- [x] app/utils/groq_client.py — retry logic (46/46 tests passing)
- [ ] app/schemas/review.py — locked Pydantic schema
- [ ] app/services/review_agent.py — LangChain AgentExecutor
- [ ] app/services/guardrail.py + tests
- [ ] app/services/github_poster.py
- [ ] PostgreSQL Alembic migrations
- [ ] React dashboard
- [ ] Railway + Vercel deployment

## Current Session
Session 3 complete (extended): registry/factory pattern for tool routing,
flake8 added, all tool runners moved into language subpackages.
46/46 tests passing.

## Decisions Made This Session
- Abstract base class + @register decorator pattern chosen over __init__.py
  convention or auto-discovery. Contract is explicit and enforced at class
  definition time — ABC raises TypeError on import if run() is missing.
- Registration triggered by explicit `import app.tools.<language>` at bottom
  of registry.py — one visible line per language, no magic auto-discovery.
- Old flat tool files (security_tool.py etc.) deleted — do not coexist with
  the new language subpackage structure.
- VALID_TOOL_NAMES in guardrail.py (next session) must include:
  {"bandit", "radon", "flake8", "eslint"}
- JS/TS security and complexity gap documented in javascript/__init__.py —
  ESLint coverage is config-dependent, no universal standalone tools available.

## Blockers
None