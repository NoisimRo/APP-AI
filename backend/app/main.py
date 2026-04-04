"""ExpertAP FastAPI Application Entry Point."""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Early startup logging for debugging
print(f"[STARTUP] Python: {sys.version}", flush=True)
print(f"[STARTUP] PORT: {os.environ.get('PORT', '8000')}", flush=True)
print(f"[STARTUP] SKIP_DB: {os.environ.get('SKIP_DB', 'false')}", flush=True)
print(f"[STARTUP] ENVIRONMENT: {os.environ.get('ENVIRONMENT', 'development')}", flush=True)

# Static files directory
STATIC_DIR = Path(__file__).parent.parent / "static"
print(f"[STARTUP] Static dir: {STATIC_DIR} (exists: {STATIC_DIR.exists()})", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    print("[LIFESPAN] Starting...", flush=True)

    # Only initialize database if not skipped
    skip_db = os.environ.get("SKIP_DB", "false").lower() == "true"

    if not skip_db:
        try:
            from app.db.session import init_db
            db_ok = await init_db()
            print(f"[LIFESPAN] Database: {'OK' if db_ok else 'SKIPPED'}", flush=True)
        except Exception as e:
            print(f"[LIFESPAN] Database error (non-fatal): {e}", flush=True)
    else:
        print("[LIFESPAN] Database skipped (SKIP_DB=true)", flush=True)

    # Initialize Redis (non-fatal if unavailable)
    try:
        from app.core.redis import init_redis
        redis_ok = await init_redis()
        print(f"[LIFESPAN] Redis: {'OK' if redis_ok else 'UNAVAILABLE (in-memory fallback)'}", flush=True)
    except Exception as e:
        print(f"[LIFESPAN] Redis error (non-fatal): {e}", flush=True)

    print("[LIFESPAN] Ready!", flush=True)
    yield
    print("[LIFESPAN] Shutting down...", flush=True)

    # Shutdown Redis
    try:
        from app.core.redis import close_redis
        await close_redis()
    except Exception:
        pass


# Create FastAPI app
app = FastAPI(
    title="ExpertAP",
    description="Business Intelligence Platform for Romanian Public Procurement",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run."""
    return {"status": "healthy", "version": "0.1.0"}


@app.get("/health/deep")
async def deep_health_check():
    """Deep health check validating DB, Redis, and LLM provider."""
    import time
    components = {}

    # Database check
    try:
        from app.db.session import is_db_available, engine
        if is_db_available() and engine:
            from sqlalchemy import text as sa_text
            start = time.monotonic()
            async with engine.connect() as conn:
                await conn.execute(sa_text("SELECT 1"))
            latency = round((time.monotonic() - start) * 1000, 1)
            components["database"] = {"status": "healthy", "latency_ms": latency}
        else:
            components["database"] = {"status": "unavailable"}
    except Exception as e:
        components["database"] = {"status": "error", "error": str(e)}

    # Redis check
    try:
        from app.core.redis import health_check as redis_health
        components["redis"] = await redis_health()
    except Exception as e:
        components["redis"] = {"status": "error", "error": str(e)}

    # LLM provider check
    try:
        from app.services.llm.factory import get_active_llm_provider
        provider = await get_active_llm_provider()
        if provider:
            components["llm"] = {
                "status": "configured",
                "provider": provider.provider_name,
                "model": provider.model_name,
            }
        else:
            components["llm"] = {"status": "not_configured"}
    except Exception as e:
        components["llm"] = {"status": "error", "error": str(e)}

    all_healthy = all(
        c.get("status") in ("healthy", "configured")
        for c in components.values()
    )

    return {
        "status": "healthy" if all_healthy else "degraded",
        "version": "0.1.0",
        "components": components,
    }


@app.get("/api")
async def api_info():
    """API information endpoint."""
    return {
        "app": "ExpertAP",
        "status": "running",
        "version": "0.1.0",
        "docs": "/docs",
    }


# Load API routes
try:
    from app.api.v1 import api_router
    app.include_router(api_router, prefix="/api/v1")
    print("[STARTUP] API routes loaded", flush=True)
except Exception as e:
    print(f"[STARTUP] API routes failed: {e}", flush=True)


# Serve frontend static files if they exist
if STATIC_DIR.exists():
    # Mount static assets (js, css, images)
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
    print("[STARTUP] Static assets mounted at /assets", flush=True)

    @app.get("/")
    async def serve_frontend():
        """Serve the frontend index.html."""
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve SPA - return index.html for all non-API routes."""
        # Skip API routes - let FastAPI handle them
        if full_path.startswith("api/"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not Found")

        # Check if file exists in static dir
        file_path = STATIC_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        # Return index.html for SPA routing
        return FileResponse(STATIC_DIR / "index.html")

    print("[STARTUP] Frontend routes configured", flush=True)
else:
    @app.get("/")
    async def root():
        """Root endpoint when no frontend is deployed."""
        return {
            "app": "ExpertAP",
            "status": "running",
            "version": "0.1.0",
            "message": "API only mode - no frontend deployed",
            "docs": "/docs",
        }

    print("[STARTUP] No static files - API only mode", flush=True)


print("[STARTUP] FastAPI app ready", flush=True)
