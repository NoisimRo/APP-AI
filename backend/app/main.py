"""ExpertAP FastAPI Application Entry Point."""

import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Early startup logging for debugging
print(f"[STARTUP] Python: {sys.version}", flush=True)
print(f"[STARTUP] PORT: {os.environ.get('PORT', '8000')}", flush=True)
print(f"[STARTUP] SKIP_DB: {os.environ.get('SKIP_DB', 'false')}", flush=True)
print(f"[STARTUP] ENVIRONMENT: {os.environ.get('ENVIRONMENT', 'development')}", flush=True)


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

    print("[LIFESPAN] Ready!", flush=True)
    yield
    print("[LIFESPAN] Shutting down...", flush=True)


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


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "app": "ExpertAP",
        "status": "running",
        "version": "0.1.0",
        "health": "/health",
    }


# Load API routes
try:
    from app.api.v1 import api_router
    app.include_router(api_router, prefix="/api/v1")
    print("[STARTUP] API routes loaded", flush=True)
except Exception as e:
    print(f"[STARTUP] API routes failed: {e}", flush=True)


print("[STARTUP] FastAPI app ready", flush=True)
