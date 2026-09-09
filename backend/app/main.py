"""ExpertAP FastAPI Application Entry Point."""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from app.core.config import get_settings
from app.core.middleware import BodySizeLimitMiddleware, security_headers_middleware

settings = get_settings()

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


def resolve_static_file(static_root: Path, requested: str) -> Path | None:
    """Map a URL path onto a file inside ``static_root``, or None.

    The raw request path can carry ``..`` segments (``/..%2f..%2fapp/core/config.py``);
    anything that resolves outside the static directory is refused so the
    SPA catch-all can never serve application source or system files.
    """
    try:
        candidate = (static_root / requested).resolve()
    except (OSError, ValueError):
        return None
    if candidate == static_root or not candidate.is_relative_to(static_root):
        return None
    return candidate if candidate.is_file() else None


# Create FastAPI app
app = FastAPI(
    title="ExpertAP",
    description="Business Intelligence Platform for Romanian Public Procurement",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — the SPA is served from this same origin, so only the explicitly
# configured dev/preview origins may make cross-origin credentialed calls.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers on every response + hard cap on request body size.
app.middleware("http")(security_headers_middleware)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)


@app.exception_handler(HTTPException)
async def _mask_server_errors(request: Request, exc: HTTPException) -> JSONResponse:
    """Hide internal exception text from 5xx responses in production.

    Many handlers raise ``HTTPException(500, detail=str(e))``; in production
    that leaks stack details, DB errors and file paths to the caller. The
    full error is still logged where it was raised.
    """
    detail = exc.detail
    if exc.status_code >= 500 and settings.is_production:
        detail = "Eroare internă. Încercați din nou mai târziu."
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": detail},
        headers=getattr(exc, "headers", None),
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
        print(f"[HEALTH] database check failed: {e}", flush=True)
        components["database"] = {"status": "error"}

    # Redis check
    try:
        from app.core.redis import health_check as redis_health
        redis_status = await redis_health()
        redis_status.pop("error", None)
        components["redis"] = redis_status
    except Exception as e:
        print(f"[HEALTH] redis check failed: {e}", flush=True)
        components["redis"] = {"status": "error"}

    # LLM provider check
    try:
        from app.services.llm.factory import get_active_llm_provider
        from app.db.session import async_session_factory, is_db_available as is_db_ok
        if is_db_ok() and async_session_factory:
            async with async_session_factory() as llm_session:
                provider = await get_active_llm_provider(llm_session)
                if provider:
                    components["llm"] = {
                        "status": "configured",
                        "provider": provider.provider_name,
                        "model": provider.model_name,
                    }
                else:
                    components["llm"] = {"status": "not_configured"}
        else:
            components["llm"] = {"status": "unavailable", "reason": "no_database"}
    except Exception as e:
        print(f"[HEALTH] llm check failed: {e}", flush=True)
        components["llm"] = {"status": "error"}

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

    STATIC_ROOT = STATIC_DIR.resolve()

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve SPA - return index.html for all non-API routes."""
        # Skip API routes - let FastAPI handle them
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")

        file_path = resolve_static_file(STATIC_ROOT, full_path)
        if file_path is not None:
            return FileResponse(file_path)
        # Return index.html for SPA routing
        return FileResponse(STATIC_ROOT / "index.html")

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
