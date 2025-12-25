"""Database session and connection management."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""

    pass


# Engine and session factory will be initialized on startup
engine = None
async_session_factory = None


async def init_db() -> None:
    """Initialize database connection."""
    global engine, async_session_factory

    settings = get_settings()

    engine = create_async_engine(
        settings.async_database_url,
        echo=settings.debug,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )

    async_session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    logger.info("database_connection_initialized", url=settings.database_url[:50] + "...")


async def get_session() -> AsyncSession:
    """Get a database session for dependency injection."""
    if async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def close_db() -> None:
    """Close database connections."""
    global engine, async_session_factory

    if engine:
        await engine.dispose()
        logger.info("database_connection_closed")
