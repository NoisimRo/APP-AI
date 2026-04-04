"""API v1 routes."""

from fastapi import APIRouter

from app.api.v1 import auth, chat, search, decisions, documents, redflags, ragmemo, drafter, clarification, training, settings, scopes, saved, users, spete_anap, analytics, strategy, compliance, multi_document, dosare, alerts, comments

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
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(strategy.router, prefix="/strategy", tags=["strategy"])
api_router.include_router(compliance.router, prefix="/compliance", tags=["compliance"])
api_router.include_router(multi_document.router, prefix="/multi-document", tags=["multi-document"])
api_router.include_router(dosare.router, prefix="/dosare", tags=["dosare"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
api_router.include_router(comments.router, prefix="/documents", tags=["comments"])
