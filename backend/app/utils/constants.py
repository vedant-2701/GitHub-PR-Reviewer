"""
Project-wide constants.

Do not import these from anywhere other than this module.
If a value needs to change, change it here — it propagates automatically.
"""

# Tool names that must appear in evidence_from_tool for the grounding check to pass.
# Must stay in sync with the runner names used in app/tools/
#
# If a new language analyser is added, add its tool name(s) here.
# The guardrail grounding check imports this directly — no other copy exists.
VALID_TOOL_NAMES: frozenset[str] = frozenset({"bandit", "radon", "flake8", "eslint"})

# Seconds to sleep between sequential Groq calls within one PR review.
# Groq free tier: 30 RPM / 6000 TPM. Parallel calls are counterproductive.
# Value chosen to stay safely under rate limits for PRs up to ~15 files.
INTER_FILE_DELAY: float = 2.0

# Files/patterns that are never sent to the LLM.
# Checked by filter_skip_files() in review_task.py.
SKIP_PATTERNS: tuple[str, ...] = (
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.pb",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.svg",
    "*.ico",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
)

# Alembic auto-generated migration files are skipped separately
# (checked by prefix match: "migrations/" or "alembic/versions/")
MIGRATION_PREFIXES: tuple[str, ...] = (
    "migrations/",
    "alembic/versions/",
)


# ---------------------------------------------------------------------------
# File skip lists — single source of truth for diff_parser._should_skip()
#
# diff_parser imports these directly. Do not redefine them anywhere else.
# Adding a new skip rule requires ONE edit here.
# ---------------------------------------------------------------------------

# Exact basenames that are never sent to the LLM (lock files etc.)
SKIP_FILENAMES: frozenset[str] = frozenset({
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
})

# File extensions (including compound ones like .min.js) that are never reviewed.
# Checked against the full filename path so .min.js matches before .js would.
SKIP_EXTENSIONS: frozenset[str] = frozenset({
    ".min.js",
    ".min.css",
    ".map",
    ".pb",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
})

# Regex pattern for Alembic/Django auto-generated migration files.
# Stored as a raw string — diff_parser compiles it with re.compile().
# Matches paths like "migrations/0001_initial.py" or "alembic/versions/abc.py".
MIGRATION_PATH_PATTERN: str = r"(^|/)migrations/[^/]+\.py$|alembic/versions/[^/]+\.py$"
