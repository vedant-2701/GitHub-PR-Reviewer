import logging
import sys
from app.config import get_settings


def setup_logging() -> None:
    """
    Configure structured logging for the application.

    - All output goes to stdout (Docker/Railway friendly).
    - Format includes timestamp, level, logger name, and message.
    - Log level is driven by settings.LOG_LEVEL.
    - Third-party library noise is suppressed to WARNING.
    """
    settings = get_settings()
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()
    root.addHandler(handler)

    # Suppress noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "httpcore", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialised — level=%s environment=%s",
        settings.LOG_LEVEL,
        settings.ENVIRONMENT,
    )