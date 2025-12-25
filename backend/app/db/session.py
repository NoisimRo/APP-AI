"""Database session and connection management."""

from typing import Optional

from sqlalchemy import text
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
db_available = False


async def init_db() -> bool:
    """Initialize database connection.

    Returns:
        True if database was initialized successfully, False otherwise.
    """
    global engine, async_session_factory, db_available

    settings = get_settings()

    # Check if database is configured
    if not settings.has_database:
        logger.warning(
            "database_not_configured",
            message="No DATABASE_URL configured. Running in demo mode without database."
        )
        db_available = False
        return False

    try:
        db_url = settings.async_database_url

        # Create engine with appropriate settings
        engine_kwargs = {
            "echo": settings.debug,
        }

        # PostgreSQL-specific settings
        if "postgresql" in db_url:
            engine_kwargs.update({
                "pool_pre_ping": True,
                "pool_size": 5,
                "max_overflow": 10,
            })

        engine = create_async_engine(db_url, **engine_kwargs)

        async_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Test connection
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))

        db_available = True
        logger.info(
            "database_connection_initialized",
            url=settings.database_url[:30] + "..." if len(settings.database_url) > 30 else settings.database_url
        )
        return True

    except Exception as e:
        logger.error(
            "database_connection_failed",
            error=str(e),
            message="Failed to connect to database. Running in demo mode."
        )
        engine = None
        async_session_factory = None
        db_available = False
        return False


def is_db_available() -> bool:
    """Check if database is available."""
    return db_available


async def get_session() -> Optional[AsyncSession]:
    """Get a database session for dependency injection."""
    if async_session_factory is None:
        return None

    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def close_db() -> None:
    """Close database connections."""
    global db_available

    if engine:
        await engine.dispose()
        db_available = False
        logger.info("database_connection_closed")
