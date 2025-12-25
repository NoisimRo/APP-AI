"""ExpertAP FastAPI Application Entry Point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import init_db, is_db_available, close_db

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    # Startup
    setup_logging()
    logger.info(
        "starting_application",
        app_name=settings.app_name,
        environment=settings.environment,
    )

    # Initialize database (optional - app runs without it)
    db_initialized = await init_db()
    if db_initialized:
        logger.info("database_initialized")
    else:
        logger.warning("running_without_database")

    yield

    # Shutdown
    await close_db()
    logger.info("shutting_down_application")


app = FastAPI(
    title=settings.app_name,
    description="Business Intelligence Platform for Romanian Public Procurement",
    version="0.1.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for Cloud Run
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run."""
    return {
        "status": "healthy",
        "app": settings.app_name,
        "environment": settings.environment,
        "database": "connected" if is_db_available() else "not_configured",
    }


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "app": settings.app_name,
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs" if not settings.is_production else None,
        "health": "/health",
    }
