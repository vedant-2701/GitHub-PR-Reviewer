from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── GitHub App ────────────────────────────────────────────────────────────
    GITHUB_APP_ID: int = 0                          # 0 = not configured yet
    GITHUB_PRIVATE_KEY_PATH: str = "./github-private-key.pem"
    GITHUB_WEBHOOK_SECRET: str = ""

    # ── Groq ──────────────────────────────────────────────────────────────────
    GROQ_API_KEY: str = ""

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///./dev.db"

    # ── Redis / Celery ────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── App ───────────────────────────────────────────────────────────────────
    ENVIRONMENT: str = "development"        # development | production
    LOG_LEVEL: str = "INFO"
    BYPASS_GUARDRAILS: bool = False         # ONLY True in test environment

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()