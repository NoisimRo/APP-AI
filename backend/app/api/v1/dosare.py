"""Dosare Digitale API — CRUD for case management.

A dosar (digital case file) groups conversations, documents, red flags,
and training materials under a single legal case. Requires authentication.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_active_user, get_optional_user, require_feature
from app.core.logging import get_logger
from app.db.session import get_session, is_db_available
from app.models.decision import (
    Dosar, DosarDocument, Conversatie, DocumentGenerat, RedFlagsSalvate, TrainingMaterial, User,
)
from app.services.document_processor import DocumentProcessor

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================

class DosarCreate(BaseModel):
    titlu: str = Field(..., min_length=1, max_length=300)
    descriere: str | None = None
    numar_dosar: str | None = Field(None, max_length=100)
    client: str | None = Field(None, max_length=300)
    autoritate_contractanta: str | None = Field(None, max_length=300)
    numar_procedura: str | None = Field(None, max_length=100)
    cod_cpv: str | None = Field(None, max_length=20)
    valoare_estimata: float | None = None
    tip_procedura: str | None = Field(None, max_length=80)
    status: str = Field("activ", max_length=30)
    termen_depunere: str | None = None  # ISO datetime
    termen_solutionare: str | None = None  # ISO datetime
    note: str | None = None
    metadata: dict = {}


class DosarUpdate(BaseModel):
    titlu: str | None = Field(None, max_length=300)
    descriere: str | None = None
    numar_dosar: str | None = Field(None, max_length=100)
    client: str | None = Field(None, max_length=300)
    autoritate_contractanta: str | None = Field(None, max_length=300)
    numar_procedura: str | None = Field(None, max_length=100)
    cod_cpv: str | None = Field(None, max_length=20)
    valoare_estimata: float | None = None
    tip_procedura: str | None = Field(None, max_length=80)
    status: str | None = Field(None, max_length=30)
    termen_depunere: str | None = None
    termen_solutionare: str | None = None
    note: str | None = None
    metadata: dict | None = None


class ArtifactCounts(BaseModel):
    conversatii: int = 0
    documente: int = 0
    red_flags: int = 0
    training_materials: int = 0


class DosarListItem(BaseModel):
    id: str
    titlu: str
    descriere: str | None
    numar_dosar: str | None
    client: str | None
    autoritate_contractanta: str | None
    status: str
    cod_cpv: str | None
    termen_depunere: str | None
    artifact_counts: ArtifactCounts
    created_at: str
    updated_at: str


class DosarDetail(DosarListItem):
    numar_procedura: str | None
    valoare_estimata: float | None
    tip_procedura: str | None
    termen_solutionare: str | None
    note: str | None
    metadata: dict


class ArtifactRef(BaseModel):
    id: str
    tip: str  # 'conversatie', 'document', 'red_flags', 'training'
    titlu: str
    created_at: str


class DosarFull(DosarDetail):
    """Full dosar with linked artifacts."""
    artifacts: list[ArtifactRef] = []


class LinkArtifactRequest(BaseModel):
    artifact_type: str = Field(..., pattern="^(conversatie|document|red_flags|training)$")
    artifact_id: str


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

VALID_STATUSES = ["activ", "in_lucru", "depus", "finalizat", "arhivat"]


def _parse_iso_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def _dosar_to_list_item(d: Dosar, counts: ArtifactCounts) -> DosarListItem:
    return DosarListItem(
        id=d.id,
        titlu=d.titlu,
        descriere=d.descriere,
        numar_dosar=d.numar_dosar,
        client=d.client,
        autoritate_contractanta=d.autoritate_contractanta,
        status=d.status,
        cod_cpv=d.cod_cpv,
        termen_depunere=d.termen_depunere.isoformat() if d.termen_depunere else None,
        artifact_counts=counts,
        created_at=d.created_at.isoformat(),
        updated_at=d.updated_at.isoformat(),
    )


def _dosar_to_detail(d: Dosar, counts: ArtifactCounts) -> DosarDetail:
    return DosarDetail(
        id=d.id,
        titlu=d.titlu,
        descriere=d.descriere,
        numar_dosar=d.numar_dosar,
        client=d.client,
        autoritate_contractanta=d.autoritate_contractanta,
        status=d.status,
        cod_cpv=d.cod_cpv,
        termen_depunere=d.termen_depunere.isoformat() if d.termen_depunere else None,
        termen_solutionare=d.termen_solutionare.isoformat() if d.termen_solutionare else None,
        numar_procedura=d.numar_procedura,
        valoare_estimata=float(d.valoare_estimata) if d.valoare_estimata else None,
        tip_procedura=d.tip_procedura,
        note=d.note,
        metadata=d.metadata_ or {},
        artifact_counts=counts,
        created_at=d.created_at.isoformat(),
        updated_at=d.updated_at.isoformat(),
    )


async def _get_artifact_counts(session: AsyncSession, dosar_id: str) -> ArtifactCounts:
    """Get counts of linked artifacts for a dosar."""
    counts = ArtifactCounts()

    for model, attr in [
        (Conversatie, "conversatii"),
        (DocumentGenerat, "documente"),
        (RedFlagsSalvate, "red_flags"),
        (TrainingMaterial, "training_materials"),
    ]:
        result = await session.execute(
            select(func.count()).where(model.dosar_id == dosar_id)
        )
        setattr(counts, attr, result.scalar() or 0)

    return counts


async def _get_artifacts(session: AsyncSession, dosar_id: str) -> list[ArtifactRef]:
    """Get all linked artifacts for a dosar."""
    artifacts = []

    # Conversations
    result = await session.execute(
        select(Conversatie).where(Conversatie.dosar_id == dosar_id).order_by(Conversatie.updated_at.desc())
    )
    for c in result.scalars():
        artifacts.append(ArtifactRef(id=c.id, tip="conversatie", titlu=c.titlu, created_at=c.created_at.isoformat()))

    # Documents
    result = await session.execute(
        select(DocumentGenerat).where(DocumentGenerat.dosar_id == dosar_id).order_by(DocumentGenerat.created_at.desc())
    )
    for d in result.scalars():
        artifacts.append(ArtifactRef(id=d.id, tip="document", titlu=d.titlu, created_at=d.created_at.isoformat()))

    # Red Flags
    result = await session.execute(
        select(RedFlagsSalvate).where(RedFlagsSalvate.dosar_id == dosar_id).order_by(RedFlagsSalvate.created_at.desc())
    )
    for r in result.scalars():
        artifacts.append(ArtifactRef(id=r.id, tip="red_flags", titlu=r.titlu, created_at=r.created_at.isoformat()))

    # Training Materials
    result = await session.execute(
        select(TrainingMaterial).where(TrainingMaterial.dosar_id == dosar_id).order_by(TrainingMaterial.created_at.desc())
    )
    for t in result.scalars():
        artifacts.append(ArtifactRef(id=t.id, tip="training", titlu=t.tema, created_at=t.created_at.isoformat()))

    return sorted(artifacts, key=lambda a: a.created_at, reverse=True)


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.post("/", response_model=DosarDetail)
async def create_dosar(
    request: DosarCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Create a new digital case file."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    if request.status and request.status not in VALID_STATUSES:
        raise HTTPException(400, f"Status invalid. Opțiuni: {', '.join(VALID_STATUSES)}")

    dosar = Dosar(
        user_id=user.id,
        titlu=request.titlu,
        descriere=request.descriere,
        numar_dosar=request.numar_dosar,
        client=request.client,
        autoritate_contractanta=request.autoritate_contractanta,
        numar_procedura=request.numar_procedura,
        cod_cpv=request.cod_cpv,
        valoare_estimata=request.valoare_estimata,
        tip_procedura=request.tip_procedura,
        status=request.status or "activ",
        termen_depunere=_parse_iso_datetime(request.termen_depunere),
        termen_solutionare=_parse_iso_datetime(request.termen_solutionare),
        note=request.note,
        metadata_=request.metadata or {},
    )
    session.add(dosar)
    await session.commit()
    await session.refresh(dosar)

    logger.info("dosar_created", dosar_id=dosar.id, titlu=dosar.titlu)
    return _dosar_to_detail(dosar, ArtifactCounts())


@router.get("/", response_model=list[DosarListItem])
async def list_dosare(
    status: str | None = Query(None, description="Filter by status"),
    search: str | None = Query(None, description="Search in titlu/client/AC"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """List user's dosare, newest first. Optional filter by status."""
    if not is_db_available():
        return []

    query = select(Dosar).where(Dosar.user_id == user.id)

    if status:
        query = query.where(Dosar.status == status)
    if search:
        search_filter = f"%{search}%"
        query = query.where(
            Dosar.titlu.ilike(search_filter) |
            Dosar.client.ilike(search_filter) |
            Dosar.autoritate_contractanta.ilike(search_filter) |
            Dosar.numar_dosar.ilike(search_filter)
        )

    query = query.order_by(Dosar.updated_at.desc()).offset(skip).limit(limit)
    result = await session.execute(query)
    dosare = result.scalars().all()

    items = []
    for d in dosare:
        counts = await _get_artifact_counts(session, d.id)
        items.append(_dosar_to_list_item(d, counts))

    return items


@router.get("/stats")
async def dosare_stats(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Get dosar statistics for current user."""
    if not is_db_available():
        return {"total": 0, "by_status": {}}

    # Total count
    total_result = await session.execute(
        select(func.count()).select_from(Dosar).where(Dosar.user_id == user.id)
    )
    total = total_result.scalar() or 0

    # By status
    status_result = await session.execute(
        select(Dosar.status, func.count())
        .where(Dosar.user_id == user.id)
        .group_by(Dosar.status)
    )
    by_status = {row[0]: row[1] for row in status_result}

    return {"total": total, "by_status": by_status}


@router.get("/{dosar_id}", response_model=DosarFull)
async def get_dosar(
    dosar_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Get a dosar with all linked artifacts."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(Dosar).where(Dosar.id == dosar_id)
    )
    dosar = result.scalar_one_or_none()
    if not dosar:
        raise HTTPException(404, "Dosar negăsit")

    if dosar.user_id != user.id and user.rol != "admin":
        raise HTTPException(403, "Nu aveți acces la acest dosar")

    counts = await _get_artifact_counts(session, dosar_id)
    artifacts = await _get_artifacts(session, dosar_id)

    detail = _dosar_to_detail(dosar, counts)
    return DosarFull(**detail.model_dump(), artifacts=artifacts)


@router.put("/{dosar_id}", response_model=DosarDetail)
async def update_dosar(
    dosar_id: str,
    request: DosarUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Update a dosar's metadata."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(Dosar).where(Dosar.id == dosar_id)
    )
    dosar = result.scalar_one_or_none()
    if not dosar:
        raise HTTPException(404, "Dosar negăsit")

    if dosar.user_id != user.id and user.rol != "admin":
        raise HTTPException(403, "Nu aveți acces la acest dosar")

    if request.status and request.status not in VALID_STATUSES:
        raise HTTPException(400, f"Status invalid. Opțiuni: {', '.join(VALID_STATUSES)}")

    update_data = request.model_dump(exclude_unset=True)

    # Handle datetime fields
    for dt_field in ["termen_depunere", "termen_solutionare"]:
        if dt_field in update_data:
            update_data[dt_field] = _parse_iso_datetime(update_data[dt_field])

    # Handle metadata field name mapping
    if "metadata" in update_data:
        update_data["metadata_"] = update_data.pop("metadata")

    for key, value in update_data.items():
        setattr(dosar, key, value)

    await session.commit()
    await session.refresh(dosar)

    logger.info("dosar_updated", dosar_id=dosar_id)
    counts = await _get_artifact_counts(session, dosar_id)
    return _dosar_to_detail(dosar, counts)


@router.delete("/{dosar_id}")
async def delete_dosar(
    dosar_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Delete a dosar. Linked artifacts are unlinked (not deleted)."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(Dosar).where(Dosar.id == dosar_id)
    )
    dosar = result.scalar_one_or_none()
    if not dosar:
        raise HTTPException(404, "Dosar negăsit")

    if dosar.user_id != user.id and user.rol != "admin":
        raise HTTPException(403, "Nu aveți acces la acest dosar")

    await session.delete(dosar)
    await session.commit()

    logger.info("dosar_deleted", dosar_id=dosar_id)
    return {"status": "deleted"}


@router.post("/{dosar_id}/link")
async def link_artifact(
    dosar_id: str,
    request: LinkArtifactRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Link an existing artifact (conversation, document, etc.) to a dosar."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    # Verify dosar exists and user owns it
    result = await session.execute(select(Dosar).where(Dosar.id == dosar_id))
    dosar = result.scalar_one_or_none()
    if not dosar:
        raise HTTPException(404, "Dosar negăsit")
    if dosar.user_id != user.id and user.rol != "admin":
        raise HTTPException(403, "Nu aveți acces la acest dosar")

    # Find and update artifact
    model_map = {
        "conversatie": Conversatie,
        "document": DocumentGenerat,
        "red_flags": RedFlagsSalvate,
        "training": TrainingMaterial,
    }
    model = model_map[request.artifact_type]

    result = await session.execute(
        select(model).where(model.id == request.artifact_id)
    )
    artifact = result.scalar_one_or_none()
    if not artifact:
        raise HTTPException(404, f"Artefact negăsit: {request.artifact_type}")

    artifact.dosar_id = dosar_id
    await session.commit()

    logger.info("artifact_linked", dosar_id=dosar_id, type=request.artifact_type, artifact_id=request.artifact_id)
    return {"status": "linked", "dosar_id": dosar_id, "artifact_type": request.artifact_type}


@router.post("/{dosar_id}/unlink")
async def unlink_artifact(
    dosar_id: str,
    request: LinkArtifactRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Unlink an artifact from a dosar."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    # Verify dosar exists and user owns it
    result = await session.execute(select(Dosar).where(Dosar.id == dosar_id))
    dosar = result.scalar_one_or_none()
    if not dosar:
        raise HTTPException(404, "Dosar negăsit")
    if dosar.user_id != user.id and user.rol != "admin":
        raise HTTPException(403, "Nu aveți acces la acest dosar")

    model_map = {
        "conversatie": Conversatie,
        "document": DocumentGenerat,
        "red_flags": RedFlagsSalvate,
        "training": TrainingMaterial,
    }
    model = model_map[request.artifact_type]

    result = await session.execute(
        select(model).where(model.id == request.artifact_id, model.dosar_id == dosar_id)
    )
    artifact = result.scalar_one_or_none()
    if not artifact:
        raise HTTPException(404, "Artefact negăsit sau nu e linkuit la acest dosar")

    artifact.dosar_id = None
    await session.commit()

    logger.info("artifact_unlinked", dosar_id=dosar_id, type=request.artifact_type, artifact_id=request.artifact_id)
    return {"status": "unlinked"}


# =============================================================================
# DOSAR DOCUMENTS (Attached Source Documents)
# =============================================================================

MAX_DOCS_PER_DOSAR = 30
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt", "md", "markdown"}

_doc_processor = DocumentProcessor()


class DosarDocumentResponse(BaseModel):
    id: str
    filename: str
    mime_type: str | None
    file_size: int | None
    text_preview: str | None
    text_stats: dict | None
    ordine: int
    created_at: str
    updated_at: str


class DosarDocumentText(BaseModel):
    id: str
    filename: str
    extracted_text: str


def _compute_text_stats(text: str) -> dict:
    """Compute basic text statistics."""
    lines = text.split("\n")
    words = text.split()
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    return {
        "characters": len(text),
        "words": len(words),
        "lines": len(lines),
        "paragraphs": len(paragraphs),
    }


def _doc_to_response(doc: DosarDocument) -> DosarDocumentResponse:
    return DosarDocumentResponse(
        id=doc.id,
        filename=doc.filename,
        mime_type=doc.mime_type,
        file_size=doc.file_size,
        text_preview=doc.text_preview,
        text_stats=doc.text_stats,
        ordine=doc.ordine,
        created_at=doc.created_at.isoformat(),
        updated_at=doc.updated_at.isoformat(),
    )


async def _verify_dosar_access(session: AsyncSession, dosar_id: str, user: User) -> Dosar:
    """Verify dosar exists and user has access."""
    result = await session.execute(select(Dosar).where(Dosar.id == dosar_id))
    dosar = result.scalar_one_or_none()
    if not dosar:
        raise HTTPException(404, "Dosar negăsit")
    if dosar.user_id != user.id and user.rol != "admin":
        raise HTTPException(403, "Nu aveți acces la acest dosar")
    return dosar


@router.post("/{dosar_id}/documents", response_model=DosarDocumentResponse)
async def upload_dosar_document(
    dosar_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Upload a source document to a dosar. Extracts text and stores it."""
    if not is_db_available():
        raise HTTPException(503, "Database not available")

    dosar = await _verify_dosar_access(session, dosar_id, user)

    # Validate file extension
    extension = file.filename.lower().split(".")[-1] if file.filename and "." in file.filename else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Tip fișier neacceptat. Tipuri acceptate: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    # Check document count limit
    count_result = await session.execute(
        select(func.count()).where(DosarDocument.dosar_id == dosar_id)
    )
    current_count = count_result.scalar() or 0
    if current_count >= MAX_DOCS_PER_DOSAR:
        raise HTTPException(400, f"Limita de {MAX_DOCS_PER_DOSAR} documente per dosar a fost atinsă")

    # Read file content
    file_content = await file.read()
    if len(file_content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"Fișier prea mare. Maxim {MAX_FILE_SIZE // (1024*1024)}MB")

    # Extract text
    try:
        extracted_text = _doc_processor.extract_text_from_file(
            file_content, file.filename or "document", file.content_type
        )
    except Exception as e:
        logger.error("document_extraction_error", filename=file.filename, error=str(e))
        raise HTTPException(422, f"Nu s-a putut extrage textul din fișier: {str(e)}")

    if not extracted_text or not extracted_text.strip():
        raise HTTPException(422, "Fișierul nu conține text extractibil")

    # Compute stats
    text_stats = _compute_text_stats(extracted_text)

    # Get next ordine
    ordine_result = await session.execute(
        select(func.coalesce(func.max(DosarDocument.ordine), -1)).where(
            DosarDocument.dosar_id == dosar_id
        )
    )
    next_ordine = (ordine_result.scalar() or 0) + 1

    doc = DosarDocument(
        dosar_id=dosar_id,
        filename=file.filename or "document",
        mime_type=file.content_type,
        file_size=len(file_content),
        extracted_text=extracted_text,
        text_preview=extracted_text[:500] if extracted_text else None,
        text_stats=text_stats,
        ordine=next_ordine,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    logger.info("dosar_document_uploaded", dosar_id=dosar_id, filename=file.filename, words=text_stats.get("words"))
    return _doc_to_response(doc)


@router.get("/{dosar_id}/documents", response_model=list[DosarDocumentResponse])
async def list_dosar_documents(
    dosar_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """List documents attached to a dosar (metadata only, no extracted_text)."""
    if not is_db_available():
        return []

    await _verify_dosar_access(session, dosar_id, user)

    result = await session.execute(
        select(DosarDocument)
        .where(DosarDocument.dosar_id == dosar_id)
        .order_by(DosarDocument.ordine)
    )
    docs = result.scalars().all()
    return [_doc_to_response(d) for d in docs]


@router.get("/{dosar_id}/documents/texts", response_model=list[DosarDocumentText])
async def get_dosar_document_texts(
    dosar_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Get all extracted texts for a dosar. Used when activating a dosar."""
    if not is_db_available():
        return []

    await _verify_dosar_access(session, dosar_id, user)

    result = await session.execute(
        select(DosarDocument)
        .where(DosarDocument.dosar_id == dosar_id)
        .order_by(DosarDocument.ordine)
    )
    docs = result.scalars().all()
    return [
        DosarDocumentText(id=d.id, filename=d.filename, extracted_text=d.extracted_text)
        for d in docs
    ]


@router.delete("/{dosar_id}/documents/{doc_id}")
async def delete_dosar_document(
    dosar_id: str,
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Delete a document from a dosar."""
    if not is_db_available():
        raise HTTPException(503, "Database not available")

    await _verify_dosar_access(session, dosar_id, user)

    result = await session.execute(
        select(DosarDocument).where(
            DosarDocument.id == doc_id, DosarDocument.dosar_id == dosar_id
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document negăsit în acest dosar")

    await session.delete(doc)
    await session.commit()

    logger.info("dosar_document_deleted", dosar_id=dosar_id, doc_id=doc_id)
    return {"status": "deleted"}


@router.put("/{dosar_id}/documents/{doc_id}", response_model=DosarDocumentResponse)
async def replace_dosar_document(
    dosar_id: str,
    doc_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Replace a document in a dosar with a new file."""
    if not is_db_available():
        raise HTTPException(503, "Database not available")

    await _verify_dosar_access(session, dosar_id, user)

    result = await session.execute(
        select(DosarDocument).where(
            DosarDocument.id == doc_id, DosarDocument.dosar_id == dosar_id
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document negăsit în acest dosar")

    # Validate
    extension = file.filename.lower().split(".")[-1] if file.filename and "." in file.filename else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Tip fișier neacceptat. Tipuri acceptate: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    file_content = await file.read()
    if len(file_content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"Fișier prea mare. Maxim {MAX_FILE_SIZE // (1024*1024)}MB")

    try:
        extracted_text = _doc_processor.extract_text_from_file(
            file_content, file.filename or "document", file.content_type
        )
    except Exception as e:
        raise HTTPException(422, f"Nu s-a putut extrage textul din fișier: {str(e)}")

    if not extracted_text or not extracted_text.strip():
        raise HTTPException(422, "Fișierul nu conține text extractibil")

    text_stats = _compute_text_stats(extracted_text)

    doc.filename = file.filename or "document"
    doc.mime_type = file.content_type
    doc.file_size = len(file_content)
    doc.extracted_text = extracted_text
    doc.text_preview = extracted_text[:500]
    doc.text_stats = text_stats

    await session.commit()
    await session.refresh(doc)

    logger.info("dosar_document_replaced", dosar_id=dosar_id, doc_id=doc_id, filename=file.filename)
    return _doc_to_response(doc)
