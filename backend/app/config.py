import logging
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


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

    @property
    def GITHUB_PRIVATE_KEY(self) -> str:
        """
        Read PEM key from disk. Raises at first access if file is missing.
        File I/O stays here — no service should touch the filesystem for this.
        """
        path = Path(self.GITHUB_PRIVATE_KEY_PATH)
        if not path.exists():
            raise FileNotFoundError(
                f"GitHub private key not found at '{self.GITHUB_PRIVATE_KEY_PATH}'. "
                "Ensure GITHUB_PRIVATE_KEY_PATH in .env points to a valid .pem file."
            )
        return path.read_text(encoding="utf-8")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache
def get_settings() -> Settings:
    """
    Returns the singleton settings object, raising FileNotFoundError on first
    access if the private key file is missing. This leverages the lazy
    evaluation of @property so that no file I/O happens until settings are
    actually used.
    """
    return Settings()
