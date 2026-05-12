---
name: project-conventions
description: Use when writing ANY code for the ACE Code Review Agent project. Enforces project-specific patterns, naming, file locations, and non-negotiable rules. Activates automatically on any code generation task.
---

# ACE Code Review Agent — Project Conventions

## Use this skill when
- Writing any new Python or JavaScript file for this project
- Adding a new API endpoint
- Creating a new service or utility
- Modifying the review pipeline

## Do not use this skill when
- Writing standalone scripts outside the project (e.g., one-off migration tools)
- Working on a completely different project

---

## Non-Negotiable Coding Rules

### Python
1. **Type hints on every function signature** — no exceptions
2. **Logging over print** — always `logger = logging.getLogger(__name__)`, never `print()`
3. **Async for all I/O** — all database calls, HTTP calls, file reads must be async
4. **Pydantic for all data** — no raw dicts passed between services
5. **Explicit timeouts** — `httpx.AsyncClient(timeout=30.0)`, never default
6. **No bare except** — always `except SpecificException as e:` with logging

### API Design
1. All endpoints return consistent response shape:
   ```json
   {"data": ..., "error": null, "timestamp": "ISO8601"}
   ```
2. All error responses:
   ```json
   {"data": null, "error": {"code": "ERROR_CODE", "message": "human readable"}, "timestamp": "ISO8601"}
   ```
3. HTTP status codes: 200 (success), 400 (validation), 401 (auth), 422 (unprocessable), 429 (rate limit), 500 (server error)

### File Naming
- Python: `snake_case.py`
- React: `PascalCase.jsx` for components, `camelCase.js` for utilities
- No index files except `src/index.jsx` — named exports, explicit imports

### Secrets and Config
- All config via Pydantic `Settings` in `app/config.py`
- Access config: `from app.config import get_settings; settings = get_settings()`
- Never import `.env` values directly with `os.environ.get()` outside `config.py`

### Database
- All DB access through repository pattern — no raw queries in routers
- Always use async sessions: `async with AsyncSession(engine) as session:`
- Every model must have `created_at` and `updated_at` timestamps

### Testing
- Test files mirror source structure: `app/services/guardrail.py` → `tests/test_guardrail.py`
- Groq API calls: always mock with `pytest-mock` — never call real API in tests
- GitHub API calls: always mock — never call real API in tests
- Test names: `test_<what>_<condition>_<expected_outcome>` e.g. `test_guardrail_empty_evidence_filters_issue`

---

## Review Pipeline — Step Execution Order

Never skip a step. Never reorder steps. If a step fails, log and raise — do not silently continue.

```
1. validate_webhook_signature()   → raises WebhookAuthError on failure
2. parse_pr_diff()               → returns List[FileDiff]
3. filter_skip_files()           → removes lock files, minified, binary
4. for each FileDiff:
   a. run_static_analysis()      → returns ToolFindings
   b. call_review_agent()        → returns ReviewResult (Groq + LangChain)
   c. guardrail_check()          → returns GuardrailResult
   d. log_filtered_issues()      → writes to DB
5. post_github_comments()        → inline + summary (only passed issues)
6. save_review_to_db()           → full review record
```

---

## Guardrail — Never Weaken

The three checks in `guardrail.py` cannot be made optional via config flag or environment variable. They are always on in production. A "bypass" flag is only acceptable for internal testing and must be guarded with:

```python
if settings.ENVIRONMENT == "testing" and settings.BYPASS_GUARDRAILS:
    # only allowed in test environment
```

---

## Error Handling Reference

```python
# Correct pattern for service functions
async def review_file(file_diff: FileDiff) -> ReviewResult:
    try:
        result = await call_groq_with_retry(prompt)
    except GroqRateLimitError:
        logger.warning("Groq rate limit hit for file %s", file_diff.filename)
        raise  # let the Celery task handle retry
    except GroqValidationError as e:
        logger.error("LLM returned invalid schema for %s: %s", file_diff.filename, str(e))
        raise ReviewAgentError(f"Schema validation failed: {e}") from e
    except Exception as e:
        logger.exception("Unexpected error reviewing %s", file_diff.filename)
        raise ReviewAgentError("Unexpected review error") from e
```

---

## What to Flag to the Developer

If you are about to do any of the following, stop and explicitly say so before proceeding:
- Changing the Pydantic schema in `schemas/review.py`
- Removing or weakening a validator
- Adding a new dependency that isn't in requirements.txt
- Bypassing the guardrail layer for any reason
- Changing the LLM model from `llama-3.3-70b-versatile`
- Adding any synchronous blocking call inside an async function