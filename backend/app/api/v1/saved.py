"""Saved content API — CRUD for conversations, documents, red flags, training materials.

All endpoints use optional auth: authenticated users see only their own content,
anonymous users see only anonymous (user_id=NULL) content.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, true as sa_true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_optional_user
from app.core.logging import get_logger
from app.db.session import get_session, is_db_available
from app.models.decision import (
    Conversatie, MesajConversatie,
    DocumentGenerat, RedFlagsSalvate, TrainingMaterial, User,
)

router = APIRouter()
logger = get_logger(__name__)


def _is_admin(user: Optional[User]) -> bool:
    """Check if user is an admin."""
    return user is not None and user.rol == "admin"


def _ownership_filter(model_class, user: Optional[User]):
    """Return a SQLAlchemy where clause filtering by ownership.

    Admin users bypass the filter and see all records.
    """
    if user and user.rol == "admin":
        return sa_true()  # No filter — admin sees everything
    if user:
        return model_class.user_id == user.id
    return model_class.user_id.is_(None)


def _check_ownership(obj, user: Optional[User]):
    """Raise 403 if user doesn't own the object. Admin bypasses."""
    if user and user.rol == "admin":
        return  # Admin can access everything
    obj_user_id = getattr(obj, "user_id", None)
    if user and obj_user_id != user.id:
        raise HTTPException(403, "Nu aveți acces la această resursă")
    if not user and obj_user_id is not None:
        raise HTTPException(403, "Nu aveți acces la această resursă")


# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================

# --- Conversations ---

class MesajInput(BaseModel):
    rol: str = Field(..., max_length=20)
    continut: str = Field(..., min_length=1)
    citations: list | None = None
    confidence: float | None = None


class SaveConversationRequest(BaseModel):
    titlu: str = Field(..., min_length=1, max_length=200)
    mesaje: list[MesajInput] = Field(..., min_length=1)
    scope_id: str | None = None


class MesajResponse(BaseModel):
    id: str
    rol: str
    continut: str
    citations: list | None = None
    confidence: float | None = None
    ordine: int
    created_at: str


class ConversationListItem(BaseModel):
    id: str
    titlu: str
    primul_mesaj: str | None
    numar_mesaje: int
    scope_id: str | None
    created_at: str
    updated_at: str


class ConversationDetail(ConversationListItem):
    mesaje: list[MesajResponse]


# --- Documents ---

class SaveDocumentRequest(BaseModel):
    tip_document: str = Field(..., max_length=30)  # 'contestatie', 'clarificare', 'rag_memo'
    titlu: str = Field(..., min_length=1, max_length=300)
    continut: str = Field(..., min_length=1)
    referinte_decizii: list[str] = []
    metadata: dict = {}


class DocumentListItem(BaseModel):
    id: str
    tip_document: str
    titlu: str
    referinte_decizii: list[str] | None
    created_at: str
    updated_at: str


class DocumentDetail(DocumentListItem):
    continut: str
    metadata: dict


# --- Red Flags ---

class SaveRedFlagsRequest(BaseModel):
    titlu: str = Field(..., min_length=1, max_length=300)
    text_analizat_preview: str | None = None
    rezultate: list = Field(...)  # Array of red flag objects
    total_flags: int = 0
    critice: int = 0
    medii: int = 0
    scazute: int = 0


class RedFlagsListItem(BaseModel):
    id: str
    titlu: str
    text_analizat_preview: str | None
    total_flags: int
    critice: int
    medii: int
    scazute: int
    created_at: str
    updated_at: str


class RedFlagsDetail(RedFlagsListItem):
    rezultate: list


# --- Training Materials ---

class SaveTrainingRequest(BaseModel):
    tip_material: str = Field(..., max_length=30)
    tema: str = Field(..., min_length=1)
    nivel_dificultate: str = Field(..., max_length=20)
    lungime: str = Field(..., max_length=20)
    full_content: str = Field(..., min_length=1)
    material: str | None = None
    cerinte: str | None = None
    rezolvare: str | None = None
    note_trainer: str | None = None
    legislatie_citata: list[str] = []
    jurisprudenta_citata: list[str] = []
    metadata: dict = {}


class TrainingListItem(BaseModel):
    id: str
    tip_material: str
    tema: str
    nivel_dificultate: str
    lungime: str
    created_at: str
    updated_at: str


class TrainingDetail(TrainingListItem):
    full_content: str
    material: str | None
    cerinte: str | None
    rezolvare: str | None
    note_trainer: str | None
    legislatie_citata: list[str] | None
    jurisprudenta_citata: list[str] | None
    metadata: dict


# =============================================================================
# CONVERSATIONS ENDPOINTS
# =============================================================================

@router.post("/conversations", response_model=ConversationDetail)
async def save_conversation(
    request: SaveConversationRequest,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Save a chat conversation with all messages."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    conv = Conversatie(
        titlu=request.titlu,
        primul_mesaj=request.mesaje[0].continut[:500] if request.mesaje else None,
        numar_mesaje=len(request.mesaje),
        scope_id=request.scope_id,
        user_id=user.id if user else None,
    )
    session.add(conv)
    await session.flush()  # Get conv.id

    for i, msg in enumerate(request.mesaje):
        mesaj = MesajConversatie(
            conversatie_id=conv.id,
            rol=msg.rol,
            continut=msg.continut,
            citations=msg.citations or [],
            confidence=msg.confidence,
            ordine=i,
        )
        session.add(mesaj)

    await session.commit()
    await session.refresh(conv)

    # Reload with messages
    result = await session.execute(
        select(Conversatie)
        .options(selectinload(Conversatie.mesaje))
        .where(Conversatie.id == conv.id)
    )
    conv = result.scalar_one()

    logger.info("conversation_saved", conv_id=conv.id, messages=conv.numar_mesaje)

    return ConversationDetail(
        id=conv.id,
        titlu=conv.titlu,
        primul_mesaj=conv.primul_mesaj,
        numar_mesaje=conv.numar_mesaje,
        scope_id=conv.scope_id,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
        mesaje=[
            MesajResponse(
                id=m.id, rol=m.rol, continut=m.continut,
                citations=m.citations, confidence=m.confidence,
                ordine=m.ordine, created_at=m.created_at.isoformat(),
            )
            for m in conv.mesaje
        ],
    )


@router.get("/conversations", response_model=list[ConversationListItem])
async def list_conversations(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """List saved conversations, newest first. Filtered by ownership."""
    if not is_db_available():
        return []

    result = await session.execute(
        select(Conversatie)
        .where(_ownership_filter(Conversatie, user))
        .order_by(Conversatie.updated_at.desc())
        .offset(skip).limit(limit)
    )
    convs = result.scalars().all()

    return [
        ConversationListItem(
            id=c.id, titlu=c.titlu, primul_mesaj=c.primul_mesaj,
            numar_mesaje=c.numar_mesaje, scope_id=c.scope_id,
            created_at=c.created_at.isoformat(),
            updated_at=c.updated_at.isoformat(),
        )
        for c in convs
    ]


@router.get("/conversations/{conv_id}", response_model=ConversationDetail)
async def get_conversation(
    conv_id: str,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Get a conversation with all messages."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(Conversatie)
        .options(selectinload(Conversatie.mesaje))
        .where(Conversatie.id == conv_id)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversație negăsită")

    _check_ownership(conv, user)

    return ConversationDetail(
        id=conv.id, titlu=conv.titlu, primul_mesaj=conv.primul_mesaj,
        numar_mesaje=conv.numar_mesaje, scope_id=conv.scope_id,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
        mesaje=[
            MesajResponse(
                id=m.id, rol=m.rol, continut=m.continut,
                citations=m.citations, confidence=m.confidence,
                ordine=m.ordine, created_at=m.created_at.isoformat(),
            )
            for m in conv.mesaje
        ],
    )


@router.delete("/conversations/{conv_id}")
async def delete_conversation(
    conv_id: str,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Delete a conversation and all its messages."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(Conversatie).where(Conversatie.id == conv_id)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversație negăsită")

    _check_ownership(conv, user)

    await session.delete(conv)
    await session.commit()
    logger.info("conversation_deleted", conv_id=conv_id)
    return {"status": "deleted"}


# =============================================================================
# DOCUMENTS ENDPOINTS
# =============================================================================

@router.post("/documents", response_model=DocumentDetail)
async def save_document(
    request: SaveDocumentRequest,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Save a generated document (contestație, clarificare, RAG memo)."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    doc = DocumentGenerat(
        tip_document=request.tip_document,
        titlu=request.titlu,
        continut=request.continut,
        referinte_decizii=request.referinte_decizii or [],
        metadata_=request.metadata or {},
        user_id=user.id if user else None,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    logger.info("document_saved", doc_id=doc.id, tip=doc.tip_document)

    return DocumentDetail(
        id=doc.id, tip_document=doc.tip_document, titlu=doc.titlu,
        continut=doc.continut,
        referinte_decizii=doc.referinte_decizii or [],
        metadata=doc.metadata_ or {},
        created_at=doc.created_at.isoformat(),
        updated_at=doc.updated_at.isoformat(),
    )


@router.get("/documents", response_model=list[DocumentListItem])
async def list_documents(
    tip: str | None = Query(None, description="Filter by tip_document"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """List saved documents, newest first. Optional filter by type."""
    if not is_db_available():
        return []

    query = (
        select(DocumentGenerat)
        .where(_ownership_filter(DocumentGenerat, user))
        .order_by(DocumentGenerat.created_at.desc())
    )
    if tip:
        query = query.where(DocumentGenerat.tip_document == tip)
    query = query.offset(skip).limit(limit)

    result = await session.execute(query)
    docs = result.scalars().all()

    return [
        DocumentListItem(
            id=d.id, tip_document=d.tip_document, titlu=d.titlu,
            referinte_decizii=d.referinte_decizii or [],
            created_at=d.created_at.isoformat(),
            updated_at=d.updated_at.isoformat(),
        )
        for d in docs
    ]


@router.get("/documents/{doc_id}", response_model=DocumentDetail)
async def get_document(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Get a saved document with full content."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(DocumentGenerat).where(DocumentGenerat.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document negăsit")

    _check_ownership(doc, user)

    return DocumentDetail(
        id=doc.id, tip_document=doc.tip_document, titlu=doc.titlu,
        continut=doc.continut,
        referinte_decizii=doc.referinte_decizii or [],
        metadata=doc.metadata_ or {},
        created_at=doc.created_at.isoformat(),
        updated_at=doc.updated_at.isoformat(),
    )


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Delete a saved document."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(DocumentGenerat).where(DocumentGenerat.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document negăsit")

    _check_ownership(doc, user)

    await session.delete(doc)
    await session.commit()
    logger.info("document_deleted", doc_id=doc_id)
    return {"status": "deleted"}


# =============================================================================
# RED FLAGS ENDPOINTS
# =============================================================================

@router.post("/redflags", response_model=RedFlagsDetail)
async def save_redflags(
    request: SaveRedFlagsRequest,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Save a red flags analysis."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    rf = RedFlagsSalvate(
        titlu=request.titlu,
        text_analizat_preview=request.text_analizat_preview,
        rezultate=request.rezultate,
        total_flags=request.total_flags,
        critice=request.critice,
        medii=request.medii,
        scazute=request.scazute,
        user_id=user.id if user else None,
    )
    session.add(rf)
    await session.commit()
    await session.refresh(rf)

    logger.info("redflags_saved", rf_id=rf.id, total=rf.total_flags)

    return RedFlagsDetail(
        id=rf.id, titlu=rf.titlu,
        text_analizat_preview=rf.text_analizat_preview,
        rezultate=rf.rezultate,
        total_flags=rf.total_flags, critice=rf.critice,
        medii=rf.medii, scazute=rf.scazute,
        created_at=rf.created_at.isoformat(),
        updated_at=rf.updated_at.isoformat(),
    )


@router.get("/redflags", response_model=list[RedFlagsListItem])
async def list_redflags(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """List saved red flags analyses, newest first."""
    if not is_db_available():
        return []

    result = await session.execute(
        select(RedFlagsSalvate)
        .where(_ownership_filter(RedFlagsSalvate, user))
        .order_by(RedFlagsSalvate.created_at.desc())
        .offset(skip).limit(limit)
    )
    items = result.scalars().all()

    return [
        RedFlagsListItem(
            id=r.id, titlu=r.titlu,
            text_analizat_preview=r.text_analizat_preview,
            total_flags=r.total_flags, critice=r.critice,
            medii=r.medii, scazute=r.scazute,
            created_at=r.created_at.isoformat(),
            updated_at=r.updated_at.isoformat(),
        )
        for r in items
    ]


@router.get("/redflags/{rf_id}", response_model=RedFlagsDetail)
async def get_redflags(
    rf_id: str,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Get a saved red flags analysis with full results."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(RedFlagsSalvate).where(RedFlagsSalvate.id == rf_id)
    )
    rf = result.scalar_one_or_none()
    if not rf:
        raise HTTPException(status_code=404, detail="Analiză negăsită")

    _check_ownership(rf, user)

    return RedFlagsDetail(
        id=rf.id, titlu=rf.titlu,
        text_analizat_preview=rf.text_analizat_preview,
        rezultate=rf.rezultate,
        total_flags=rf.total_flags, critice=rf.critice,
        medii=rf.medii, scazute=rf.scazute,
        created_at=rf.created_at.isoformat(),
        updated_at=rf.updated_at.isoformat(),
    )


@router.delete("/redflags/{rf_id}")
async def delete_redflags(
    rf_id: str,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Delete a saved red flags analysis."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(RedFlagsSalvate).where(RedFlagsSalvate.id == rf_id)
    )
    rf = result.scalar_one_or_none()
    if not rf:
        raise HTTPException(status_code=404, detail="Analiză negăsită")

    _check_ownership(rf, user)

    await session.delete(rf)
    await session.commit()
    logger.info("redflags_deleted", rf_id=rf_id)
    return {"status": "deleted"}


# =============================================================================
# TRAINING MATERIALS ENDPOINTS
# =============================================================================

@router.post("/training", response_model=TrainingDetail)
async def save_training(
    request: SaveTrainingRequest,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Save a training material."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    tm = TrainingMaterial(
        tip_material=request.tip_material,
        tema=request.tema,
        nivel_dificultate=request.nivel_dificultate,
        lungime=request.lungime,
        full_content=request.full_content,
        material=request.material,
        cerinte=request.cerinte,
        rezolvare=request.rezolvare,
        note_trainer=request.note_trainer,
        legislatie_citata=request.legislatie_citata or [],
        jurisprudenta_citata=request.jurisprudenta_citata or [],
        metadata_=request.metadata or {},
        user_id=user.id if user else None,
    )
    session.add(tm)
    await session.commit()
    await session.refresh(tm)

    logger.info("training_saved", tm_id=tm.id, tip=tm.tip_material)

    return TrainingDetail(
        id=tm.id, tip_material=tm.tip_material, tema=tm.tema,
        nivel_dificultate=tm.nivel_dificultate, lungime=tm.lungime,
        full_content=tm.full_content,
        material=tm.material, cerinte=tm.cerinte,
        rezolvare=tm.rezolvare, note_trainer=tm.note_trainer,
        legislatie_citata=tm.legislatie_citata or [],
        jurisprudenta_citata=tm.jurisprudenta_citata or [],
        metadata=tm.metadata_ or {},
        created_at=tm.created_at.isoformat(),
        updated_at=tm.updated_at.isoformat(),
    )


@router.get("/training", response_model=list[TrainingListItem])
async def list_training(
    tip: str | None = Query(None, description="Filter by tip_material"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """List saved training materials, newest first."""
    if not is_db_available():
        return []

    query = (
        select(TrainingMaterial)
        .where(_ownership_filter(TrainingMaterial, user))
        .order_by(TrainingMaterial.created_at.desc())
    )
    if tip:
        query = query.where(TrainingMaterial.tip_material == tip)
    query = query.offset(skip).limit(limit)

    result = await session.execute(query)
    items = result.scalars().all()

    return [
        TrainingListItem(
            id=t.id, tip_material=t.tip_material, tema=t.tema,
            nivel_dificultate=t.nivel_dificultate, lungime=t.lungime,
            created_at=t.created_at.isoformat(),
            updated_at=t.updated_at.isoformat(),
        )
        for t in items
    ]


@router.get("/training/{tm_id}", response_model=TrainingDetail)
async def get_training(
    tm_id: str,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Get a saved training material with full content."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(TrainingMaterial).where(TrainingMaterial.id == tm_id)
    )
    tm = result.scalar_one_or_none()
    if not tm:
        raise HTTPException(status_code=404, detail="Material negăsit")

    _check_ownership(tm, user)

    return TrainingDetail(
        id=tm.id, tip_material=tm.tip_material, tema=tm.tema,
        nivel_dificultate=tm.nivel_dificultate, lungime=tm.lungime,
        full_content=tm.full_content,
        material=tm.material, cerinte=tm.cerinte,
        rezolvare=tm.rezolvare, note_trainer=tm.note_trainer,
        legislatie_citata=tm.legislatie_citata or [],
        jurisprudenta_citata=tm.jurisprudenta_citata or [],
        metadata=tm.metadata_ or {},
        created_at=tm.created_at.isoformat(),
        updated_at=tm.updated_at.isoformat(),
    )


@router.delete("/training/{tm_id}")
async def delete_training(
    tm_id: str,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Delete a saved training material."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(TrainingMaterial).where(TrainingMaterial.id == tm_id)
    )
    tm = result.scalar_one_or_none()
    if not tm:
        raise HTTPException(status_code=404, detail="Material negăsit")

    _check_ownership(tm, user)

    await session.delete(tm)
    await session.commit()
    logger.info("training_deleted", tm_id=tm_id)
    return {"status": "deleted"}
