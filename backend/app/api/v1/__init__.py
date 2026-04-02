"""API v1 routes."""

from fastapi import APIRouter

from app.api.v1 import auth, chat, search, decisions, documents, redflags, ragmemo, drafter, clarification, training, settings, scopes, saved, users, spete_anap

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(decisions.router, prefix="/decisions", tags=["decisions"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(redflags.router, prefix="/redflags", tags=["redflags"])
api_router.include_router(ragmemo.router, prefix="/ragmemo", tags=["ragmemo"])
api_router.include_router(drafter.router, prefix="/drafter", tags=["drafter"])
api_router.include_router(clarification.router, prefix="/clarification", tags=["clarification"])
api_router.include_router(training.router, prefix="/training", tags=["training"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(scopes.router, prefix="/scopes", tags=["scopes"])
api_router.include_router(saved.router, prefix="/saved", tags=["saved"])
api_router.include_router(spete_anap.router, prefix="/spete", tags=["spete"])
