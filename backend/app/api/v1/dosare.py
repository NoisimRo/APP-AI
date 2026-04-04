"""Dosare Digitale API — CRUD for case management.

A dosar (digital case file) groups conversations, documents, red flags,
and training materials under a single legal case. Requires authentication.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_active_user, get_optional_user, require_feature
from app.core.logging import get_logger
from app.db.session import get_session, is_db_available
from app.models.decision import (
    Dosar, Conversatie, DocumentGenerat, RedFlagsSalvate, TrainingMaterial, User,
)

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
