"""Chat API endpoints with persistent memory."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limiter import require_rate_limit, increment_usage
from app.db.session import get_session
from app.models.decision import User, UserContext
from app.core.deps import get_optional_user
from app.services.rag import RAGService
from app.services.llm.factory import get_active_llm_provider
from app.services.llm.streaming import create_sse_response
from app.api.v1.scopes import get_scope_decision_ids

router = APIRouter()
logger = get_logger(__name__)

# Max context entries to load per user
MAX_CONTEXT_ENTRIES = 20


class ChatMessage(BaseModel):
    """A chat message."""

    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Chat request payload."""

    message: str = Field(..., min_length=1, max_length=100000)
    conversation_id: str | None = Field(None, description="Optional conversation ID")
    history: list[ChatMessage] = Field(default_factory=list)
    scope_id: str | None = Field(None, description="Optional scope ID for pre-filtering decisions")
    rerank: bool = Field(False, description="Enable LLM reranking of retrieved chunks")
    expansion: bool = Field(False, description="Enable LLM query expansion for better retrieval")


class Citation(BaseModel):
    """A citation from a CNSC decision."""

    decision_id: str
    text: str
    verified: bool = True


class ChatResponse(BaseModel):
    """Chat response payload."""

    message: str
    conversation_id: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_questions: list[str] = Field(default_factory=list)


async def _load_user_context(session: AsyncSession, user_id: str) -> str:
    """Load persistent user context as text for system prompt injection."""
    result = await session.execute(
        select(UserContext)
        .where(UserContext.user_id == user_id, UserContext.active == True)
        .order_by(UserContext.importance.desc(), UserContext.updated_at.desc())
        .limit(MAX_CONTEXT_ENTRIES)
    )
    entries = list(result.scalars().all())

    if not entries:
        return ""

    context_lines = []
    for entry in entries:
        prefix = {
            "preference": "Preferință utilizator",
            "case_detail": "Detaliu caz",
            "expertise": "Expertiză",
            "frequent_topic": "Temă frecventă",
        }.get(entry.fact_type, "Informație")
        context_lines.append(f"- {prefix}: {entry.content}")

    return "\n\nCONTEXT PERSISTENT UTILIZATOR:\n" + "\n".join(context_lines)


async def _extract_and_save_facts(
    session: AsyncSession, user_id: str, message: str, response: str, llm
):
    """Extract notable facts from the conversation and save to user context.

    Only saves genuinely useful facts (not greetings, generic questions, etc.).
    Runs in background — failures don't affect the chat response.
    """
    try:
        prompt = f"""Analizează următorul schimb de mesaje și extrage DOAR fapte noi despre utilizator care merită reținute pentru conversații viitoare.

Mesaj utilizator: {message[:500]}
Răspuns asistent: {response[:500]}

Extrage fapte precum: domeniu de activitate, tip de achiziții cu care lucrează, preferințe de comunicare, cazuri în curs, expertiză specifică.

NU extrage: salutări, întrebări generice, fapte deja evidente.

Răspunde în JSON:
[
  {{"content": "fapt concis", "type": "preference|case_detail|expertise|frequent_topic|general", "importance": 5}}
]

Dacă nu sunt fapte noi de reținut, returnează [] (array gol)."""

        import asyncio, json, re
        result = await asyncio.wait_for(
            llm.complete(prompt, temperature=0.0, max_tokens=500),
            timeout=30,
        )
        match = re.search(r'\[.*\]', result, re.DOTALL)
        if not match:
            return

        facts = json.loads(match.group(0))
        for fact in facts[:3]:  # Max 3 facts per message
            content = fact.get("content", "").strip()
            if not content or len(content) < 5:
                continue
            new_context = UserContext(
                user_id=user_id,
                content=content,
                fact_type=fact.get("type", "general"),
                importance=min(max(fact.get("importance", 5), 1), 10),
                source="conversation",
            )
            session.add(new_context)
        await session.commit()
    except Exception as e:
        logger.debug("context_extraction_failed", error=str(e))


@router.post("/", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
) -> ChatResponse:
    """Chat with ExpertAP. Includes persistent memory for logged-in users."""
    logger.info(
        "chat_request",
        message_length=len(request.message),
        has_history=len(request.history) > 0,
    )

    try:
        llm = await get_active_llm_provider(session)
        rag = RAGService(llm_provider=llm)

        history = [
            {"role": msg.role, "content": msg.content}
            for msg in request.history
        ] if request.history else None

        scope_ids = None
        if request.scope_id:
            scope_ids = await get_scope_decision_ids(request.scope_id, session)
            if scope_ids is None:
                raise HTTPException(status_code=404, detail="Scope not found")

        # Load persistent user context
        user_context = ""
        current_user = None
        try:
            from app.core.deps import get_optional_user
            # Get user from rate_user (already resolved)
            if rate_user and hasattr(rate_user, 'id'):
                current_user = rate_user
                user_context = await _load_user_context(session, str(rate_user.id))
        except Exception:
            pass

        response_text, citations, confidence, suggested = await rag.generate_response(
            query=request.message,
            session=session,
            conversation_history=history,
            max_decisions=5,
            scope_decision_ids=scope_ids,
            enable_rerank=request.rerank,
            enable_expansion=request.expansion,
            extra_system_context=user_context if user_context else None,
        )

        conversation_id = request.conversation_id or f"conv-{hash(request.message)}"

        # Extract facts in background (non-blocking)
        if current_user:
            import asyncio
            asyncio.create_task(
                _extract_and_save_facts(session, str(current_user.id), request.message, response_text, llm)
            )

        citations_dicts = [
            {"decision_id": c.decision_id, "text": c.text, "verified": c.verified}
            for c in citations
        ]

        await increment_usage(rate_user, http_request)

        return ChatResponse(
            message=response_text,
            conversation_id=conversation_id,
            citations=citations_dicts,
            confidence=confidence,
            suggested_questions=suggested,
        )

    except Exception as e:
        logger.error("chat_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Eroare la procesarea cererii: {str(e)}"
        )


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
):
    """Stream chat response via SSE. Includes persistent memory for logged-in users."""
    logger.info("chat_stream_request", message_length=len(request.message))

    try:
        llm = await get_active_llm_provider(session)
        rag = RAGService(llm_provider=llm)

        history = [
            {"role": msg.role, "content": msg.content}
            for msg in request.history
        ] if request.history else None

        scope_ids = None
        if request.scope_id:
            scope_ids = await get_scope_decision_ids(request.scope_id, session)
            if scope_ids is None:
                raise HTTPException(status_code=404, detail="Scope not found")

        # Load persistent user context
        user_context = ""
        try:
            if rate_user and hasattr(rate_user, 'id'):
                user_context = await _load_user_context(session, str(rate_user.id))
        except Exception:
            pass

        import time
        t_start = time.monotonic()

        query = request.message
        MULTI_CHUNK_THRESHOLD = 5000
        if len(query) > MULTI_CHUNK_THRESHOLD:
            # Large message (user pasted documents) — use multi-chunk for better coverage
            logger.info("chat_using_multi_chunk", message_len=len(query))
            relevant_chunks, leg_fragments = await rag.multi_chunk_search(
                documents=[query],
                session=session,
                max_decisions=5,
                max_legislation=5,
                scope_decision_ids=scope_ids,
            )
            contexts, system_prompt, citations, confidence = await rag._build_context_from_chunks(
                query=query,
                relevant_chunks=relevant_chunks,
                legislation_fragments=leg_fragments,
                session=session,
            )
            suggested = []
        else:
            contexts, system_prompt, citations, confidence, suggested = await rag.prepare_context(
                query=query, session=session, conversation_history=history, max_decisions=5,
                scope_decision_ids=scope_ids,
                enable_rerank=request.rerank,
                enable_expansion=request.expansion,
            )

        # Inject user context into system prompt
        if user_context and system_prompt:
            system_prompt = system_prompt + user_context

        search_duration_s = round(time.monotonic() - t_start, 2)
        logger.info("chat_stream_search_completed", duration_s=search_duration_s)

        if contexts is None:
            raise HTTPException(
                status_code=404,
                detail="Nu am găsit informații relevante. Reformulează întrebarea.",
            )

        status_msgs = []
        n_sources = len(citations)
        if n_sources:
            status_msgs.append(f"Am identificat {n_sources} decizii CNSC relevante")

        await increment_usage(rate_user, http_request)

        return await create_sse_response(
            llm=rag.llm,
            prompt=query,
            context=contexts,
            system_prompt=system_prompt,
            temperature=0.1,
            max_tokens=12288,
            metadata={
                "citations": [{"decision_id": c.decision_id, "text": c.text, "verified": c.verified} for c in citations],
                "confidence": confidence,
                "suggested_questions": suggested,
                "conversation_id": request.conversation_id or f"conv-{hash(request.message)}",
                "search_duration_s": search_duration_s,
            },
            status_messages=status_msgs,
        )

    except Exception as e:
        logger.error("chat_stream_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# User Context Management
# ---------------------------------------------------------------------------

@router.get("/memory")
async def get_user_memory(
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Get the current user's persistent memory entries."""
    if not user:
        return []

    result = await session.execute(
        select(UserContext)
        .where(UserContext.user_id == str(user.id), UserContext.active == True)
        .order_by(UserContext.importance.desc(), UserContext.updated_at.desc())
    )
    entries = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "content": e.content,
            "fact_type": e.fact_type,
            "importance": e.importance,
            "source": e.source,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]


@router.delete("/memory/{entry_id}")
async def delete_memory_entry(
    entry_id: str,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Delete a persistent memory entry."""
    if not user:
        raise HTTPException(status_code=401, detail="Autentificare necesară")

    result = await session.execute(
        select(UserContext).where(
            UserContext.id == entry_id,
            UserContext.user_id == str(user.id),
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Intrare negăsită")

    entry.active = False
    await session.commit()
    return {"status": "deleted"}


@router.delete("/memory")
async def clear_all_memory(
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Clear all persistent memory for the current user."""
    if not user:
        raise HTTPException(status_code=401, detail="Autentificare necesară")

    await session.execute(
        update(UserContext)
        .where(UserContext.user_id == str(user.id))
        .values(active=False)
    )
    await session.commit()
    return {"status": "cleared"}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Get conversation history by ID."""
    # TODO: Implement full conversation retrieval from mesaje_conversatie
    raise HTTPException(status_code=501, detail="Not implemented yet")
