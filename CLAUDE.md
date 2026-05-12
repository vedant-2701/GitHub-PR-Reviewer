# CLAUDE.md — ACE Code Review Agent

This file is automatically read by Claude Code and Antigravity at session start.
It is the single source of truth for all architectural decisions, constraints, and conventions.
Do not override instructions in this file without explicit written justification in a PR description.

---

## PROJECT OVERVIEW

**Name**: ACE Code Review Agent
**Purpose**: Automated GitHub PR reviewer that uses an LLM + static analysis tools to post inline code review comments. Includes a guardrail layer that filters hallucinated or ungrounded issues before any comment is posted.
**Status**: Production — this is not a demo. Code must be production-grade.
**Owner**: Solo developer, CPU-only development machine.

---

## ABSOLUTE RULES — VIOLATION IS A BUG

1. **Never post a GitHub comment that bypasses the guardrail layer.** The three guardrail checks (grounding, line validation, confidence gating) are not optional. Any code path that calls the GitHub comment poster without first running `guardrail_check()` is a bug.

2. **Never hardcode secrets.** All API keys, tokens, webhook secrets go in `.env`. The `.env` file is gitignored. There is an `.env.example` that contains key names with empty values.

3. **Never call the Groq API without retry logic.** The free tier returns 429 errors. All Groq calls use the `call_groq_with_retry()` utility in `app/utils/groq_client.py`.

4. **Never accept a ReviewResult where `evidence_from_tool` is empty.** This field is mandatory. A Pydantic validator enforces it. Do not remove or weaken this validator.

5. **Never use `print()` in production code.** Use `logging.getLogger(__name__)`.

6. **Never skip input validation on webhook payloads.** All `/webhook` calls must have HMAC-SHA256 signature verified before any processing begins.

---

## LOCKED TECH STACK

### Backend
- **Language**: Python 3.11+
- **Framework**: FastAPI (async)
- **Task Queue**: Celery + Redis
- **Database**: PostgreSQL (prod), SQLite (local dev only)
- **ORM**: SQLAlchemy 2.0 (async)
- **Migrations**: Alembic
- **Validation**: Pydantic v2
- **Server**: Uvicorn

### LLM / Agent
- **LLM**: `llama-3.3-70b-versatile` via Groq API — no other model without documented decision
- **Agent Framework**: LangChain AgentExecutor
- **Structured Output**: `response_format={"type": "json_object"}` + Pydantic validation
- **Fallback**: If Pydantic validation fails 3 times, log raw response and raise `ReviewAgentError`

### Static Analysis Tools
- **Python**: `bandit` (security), `radon` (complexity), `ast` (syntax)
- **JS/TS**: ESLint via subprocess
- **Language routing**: file extension detection in `app/tools/language_router.py`

### GitHub Integration
- **Library**: PyGitHub
- **Auth**: GitHub App (private key in `.env`) — not OAuth
- **Webhook validation**: HMAC-SHA256 via `python-jose`

### Frontend
- **Framework**: React 18 + Vite
- **Styling**: Tailwind CSS
- **Data fetching**: Polling every 5s via `useInterval` hook (no websockets for now)
- **State**: React context (no Redux unless complexity demands it)

### Infrastructure
- **Backend hosting**: Railway (production)
- **Frontend hosting**: Vercel
- **Dev tunnel**: ngrok (development only, never in production)
- **Containerisation**: Docker + docker-compose (for local full-stack dev)

### Not Using (Do Not Add Without Discussion)
- No OpenAI / Anthropic / any paid LLM
- No vector database / embeddings
- No GraphQL
- No Next.js (plain React + Vite)
- No Firebase / Supabase

---

## PROJECT STRUCTURE

```
ace-code-review-agent/
├── CLAUDE.md                          ← you are here
├── .claude/
│   └── skills/
│       ├── project-conventions/SKILL.md
│       ├── groq-agent/SKILL.md
│       ├── fastapi-production/SKILL.md
│       └── guardrails/SKILL.md
├── backend/
│   ├── app/
│   │   ├── main.py                    ← FastAPI app init, middleware
│   │   ├── config.py                  ← Pydantic Settings from .env
│   │   ├── database.py                ← SQLAlchemy async engine + session
│   │   ├── models/                    ← SQLAlchemy ORM models
│   │   │   ├── review.py
│   │   │   └── filtered_issue.py
│   │   ├── schemas/                   ← Pydantic schemas (API + LLM)
│   │   │   ├── review.py              ← ReviewResult, Issue (LOCKED)
│   │   │   └── webhook.py
│   │   ├── routers/
│   │   │   ├── webhook.py             ← POST /webhook
│   │   │   ├── reviews.py             ← GET /reviews, GET /reviews/{id}
│   │   │   ├── logs.py                ← GET /logs (filtered issues)
│   │   │   └── health.py             ← GET /health
│   │   ├── services/
│   │   │   ├── diff_parser.py         ← PR diff → List[FileDiff]
│   │   │   ├── review_agent.py        ← LangChain agent orchestration
│   │   │   ├── guardrail.py           ← 3 guardrail checks
│   │   │   ├── github_poster.py       ← Post inline + summary comments
│   │   │   └── static_analysis.py    ← bandit/radon/ast/eslint runners
│   │   ├── tools/                     ← LangChain Tool wrappers
│   │   │   ├── complexity_tool.py
│   │   │   ├── security_tool.py
│   │   │   ├── syntax_tool.py
│   │   │   ├── style_tool.py
│   │   │   └── language_router.py
│   │   ├── tasks/
│   │   │   └── review_task.py         ← Celery task: full review pipeline
│   │   └── utils/
│   │       ├── groq_client.py         ← Groq API client + retry logic
│   │       ├── hmac_validator.py      ← Webhook signature check
│   │       └── logging_config.py     ← Structured logging setup
│   ├── alembic/                       ← Database migrations
│   ├── tests/
│   │   ├── test_diff_parser.py
│   │   ├── test_guardrail.py
│   │   ├── test_schemas.py
│   │   └── test_review_agent.py       ← Mocked Groq calls
│   ├── .env.example
│   ├── requirements.txt
│   ├── Dockerfile
│   └── celery_worker.py
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/
│   │   │   ├── PRList.jsx
│   │   │   ├── ReviewDetail.jsx
│   │   │   └── GuardrailStats.jsx
│   │   ├── hooks/
│   │   │   └── useInterval.js
│   │   └── api/
│   │       └── client.js
│   ├── package.json
│   └── vite.config.js
├── docker-compose.yml                 ← Local: FastAPI + Redis + PostgreSQL
└── README.md
```

---

## PYDANTIC SCHEMA (LOCKED)

Location: `backend/app/schemas/review.py`

```python
from pydantic import BaseModel, field_validator
from typing import List
from enum import Enum

class IssueType(str, Enum):
    SECURITY = "security"
    COMPLEXITY = "complexity"
    STYLE = "style"
    SYNTAX = "syntax"

class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class Verdict(str, Enum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    COMMENT = "COMMENT"

class Issue(BaseModel):
    line_number: int
    type: IssueType
    severity: Severity
    message: str
    suggestion: str
    evidence_from_tool: str

    @field_validator("evidence_from_tool")
    @classmethod
    def evidence_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip() or len(v.strip()) < 10:
            raise ValueError("evidence_from_tool must contain a specific tool finding, not empty or vague")
        return v

class ReviewResult(BaseModel):
    issues: List[Issue]
    summary: str
    overall_verdict: Verdict
    confidence: float
    files_reviewed: List[str]

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v
```

**This schema is the contract between the LLM and the rest of the system. Do not relax the validators.**

---

## GUARDRAIL LAYER (LOCKED LOGIC)

Location: `backend/app/services/guardrail.py`

Three checks, in order:

```
1. Grounding check
   - evidence_from_tool must be populated AND meaningful (validator handles this at parse time)
   - Additional runtime check: evidence string must reference tool name (bandit/radon/ast/eslint)

2. Line number validation
   - flagged line_number must exist in the parsed diff's added/changed line numbers
   - If not found: filter the issue, log reason="line_not_in_diff"

3. Confidence gating
   - If ReviewResult.confidence < 0.6:
     - Downgrade each issue severity by one level (HIGH→MEDIUM, MEDIUM→LOW, LOW→LOW)
     - Prepend "Low confidence: " to each message
     - Do NOT suppress — post with caveat
   - Log confidence score regardless
```

All filtered issues stored in `filtered_issues` table with: `review_id`, `issue_data`, `filter_reason`, `timestamp`.

---

## GROQ RATE LIMIT STRATEGY

- 30 RPM / 6000 TPM on free tier
- One file = one LLM call
- Add `await asyncio.sleep(2)` between file reviews in the same PR
- On 429: exponential backoff starting at 5s, max 3 retries
- Large PRs (>10 files): prioritise by risk — Python files with security issues first, lock files skipped entirely

File skip list (never send to LLM):
```
package-lock.json, yarn.lock, poetry.lock, Pipfile.lock,
*.min.js, *.min.css, *.map, migrations/*.py (Alembic auto-generated),
*.pb, *.png, *.jpg, *.svg, *.ico
```

---

## ENVIRONMENT VARIABLES (.env.example)

```
# GitHub App
GITHUB_APP_ID=
GITHUB_PRIVATE_KEY_PATH=./github-private-key.pem
GITHUB_WEBHOOK_SECRET=

# Groq
GROQ_API_KEY=

# Database
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/ace_review

# Redis / Celery
REDIS_URL=redis://localhost:6379/0

# App
ENVIRONMENT=development  # development | production
LOG_LEVEL=INFO
```

---

## TESTING REQUIREMENTS

Minimum test coverage before any PR is merged:

| Module | Required Tests |
|---|---|
| `diff_parser.py` | Parses unified diff correctly, handles edge cases (empty diff, binary file) |
| `guardrail.py` | All 3 checks pass/fail correctly, filtered issues are logged |
| `schemas/review.py` | Pydantic validators reject bad data, accept good data |
| `hmac_validator.py` | Valid and invalid signatures handled correctly |
| `groq_client.py` | Retry logic fires on 429, raises after max retries |

Run tests: `pytest backend/tests/ -v`

---

## DECISIONS LOG

Record all significant decisions here so future Claude sessions don't re-litigate them.

| Date | Decision | Reason |
|---|---|---|
| 2026-05 | Groq over Ollama | Groq is free, 500+ TPS, no CPU burden. Ollama on CPU = 20-60s/file = unacceptable for production |
| 2026-05 | llama-3.3-70b-versatile over 8b | 8B too weak for reliable structured output + tool calling chain |
| 2026-05 | JSON mode over native tool calling | More reliable structured output from Groq free tier models |
| 2026-05 | Celery + Redis over FastAPI BackgroundTasks | BackgroundTasks not reliable for production — no retry, no persistence |
| 2026-05 | React + Vite over Next.js | Dashboard is a simple SPA with polling. SSR/SSG adds complexity with no benefit |
| 2026-05 | No vector store / embeddings | Not needed — agent reviews diff directly, no retrieval step |

---

## SKILLS TO INSTALL (Claude Code / Antigravity)

Run these once after cloning the repo:

```bash
# Official Anthropic frontend design skill
mkdir -p .claude/skills/frontend-design && curl -L -o skill.zip "https://fastmcp.me/Skills/Download/frontend-design" && unzip -o skill.zip -d .claude/skills/frontend-design && rm skill.zip

# FastAPI production templates
mkdir -p .claude/skills/fastapi-templates && curl -L -o skill.zip "https://fastmcp.me/Skills/Download/132" && unzip -o skill.zip -d .claude/skills/fastapi-templates && rm skill.zip

# Security auditor (from antigravity-awesome-skills)
npx antigravity-awesome-skills --claude --skills security-auditor,systematic-debugging,test-driven-development,create-pr
```

The project's own custom skills live in `.claude/skills/` and are already in the repo. Do not re-install them.