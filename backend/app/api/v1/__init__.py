"""API v1 routes."""

from fastapi import APIRouter

from app.api.v1 import chat, search, decisions

api_router = APIRouter()

api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(decisions.router, prefix="/decisions", tags=["decisions"])
