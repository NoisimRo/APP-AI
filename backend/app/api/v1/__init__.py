"""API v1 routes."""

from fastapi import APIRouter

from app.api.v1 import chat, search, decisions, documents, redflags, ragmemo, drafter, clarification

api_router = APIRouter()

api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(decisions.router, prefix="/decisions", tags=["decisions"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(redflags.router, prefix="/redflags", tags=["redflags"])
api_router.include_router(ragmemo.router, prefix="/ragmemo", tags=["ragmemo"])
api_router.include_router(drafter.router, prefix="/drafter", tags=["drafter"])
api_router.include_router(clarification.router, prefix="/clarification", tags=["clarification"])
