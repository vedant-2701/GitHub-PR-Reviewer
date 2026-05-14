# alembic/env.py
import asyncio
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
import app.models  # noqa: F401 — registers Review + FilteredIssue on Base.metadata
from app.database import Base

logger = logging.getLogger(__name__)

# Alembic Config object — gives access to alembic.ini values
config = context.config

# Wire Python logging from alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# This is what --autogenerate diffs against. Must be set before run_migrations.
target_metadata = Base.metadata

# Override sqlalchemy.url from your app settings — never read from alembic.ini
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Required for SQLite ALTER TABLE support (batch mode).
        # Harmless on PostgreSQL — Alembic ignores it there.
        render_as_batch=True,
        # Emit the correct type comparison for Enum columns.
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# Offline mode (generate SQL script without a live DB) — not used in this project
# but Alembic requires the function to exist.
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()