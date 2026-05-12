import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,          # set True temporarily for query debugging — never in prod
    pool_pre_ping=True,  # detect stale connections before use
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # objects remain usable after commit
)


class Base(DeclarativeBase):
    """
    Shared declarative base for all ORM models.
    Import this in each model module — never create a second Base.
    """
    pass


async def init_db() -> None:
    """
    Create all tables from ORM models on startup.

    # TODO: Replace with Alembic migrations (planned for a future session).
    #       When Alembic is added, remove this call from main.py lifespan
    #       and run migrations via `alembic upgrade head` in the container
    #       entrypoint instead.
    """
    # Import models here so their classes are registered on Base.metadata
    # before create_all runs. Add new model imports here as they are created.
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables initialised (create_all). Switch to Alembic when migrations are added.")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async database session.

    Commits on clean exit, rolls back on any exception.
    The session is always closed when the request is done.

    Usage in a router:
        async def my_endpoint(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise