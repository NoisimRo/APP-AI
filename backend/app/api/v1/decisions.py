"""Decisions API endpoints."""

import re

from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session, is_db_available
from app.models.decision import DecizieCNSC, NomenclatorCPV

router = APIRouter()
logger = get_logger(__name__)


class DecisionMetadata(BaseModel):
    """Metadata for a CNSC decision."""

    case_number: str | None = None
    date: str | None = None
    bulletin: str | None = None
    year: int | None = None
    cpv_codes: list[str] = Field(default_factory=list)
    criticism_codes: list[str] = Field(default_factory=list)
    ruling: str | None = None  # ADMIS, RESPINS, PARTIAL
    parties: dict = Field(default_factory=dict)


class Decision(BaseModel):
    """A CNSC decision."""

    id: str
    title: str
    content: str
    metadata: DecisionMetadata
    created_at: str
    updated_at: str


class DecisionSummary(BaseModel):
    """Summary of a CNSC decision for list views."""

    id: str
    filename: str
    numar_bo: int
    an_bo: int
    numar_decizie: int | None
    data_decizie: str | None
    tip_contestatie: str
    coduri_critici: list[str]
    cod_cpv: str | None
    cpv_descriere: str | None
    solutie_contestatie: str | None
    contestator: str | None
    autoritate_contractanta: str | None
    rezumat: str | None = None


class DecisionListResponse(BaseModel):
    """Response for decision list endpoint."""

    decisions: list[DecisionSummary]
    total: int
    page: int
    page_size: int


@router.get("/", response_model=DecisionListResponse)
async def list_decisions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ruling: str | None = Query(None, description="Filter by ruling"),
    year: int | None = Query(None, ge=2000, le=2100),
    tip_contestatie: str | None = Query(None, description="Filter by type: documentatie or rezultat"),
    search: str | None = Query(None, description="Search by BO number, contestator, autoritate, CPV, or criticism codes"),
    session: AsyncSession = Depends(get_session),
) -> DecisionListResponse:
    """
    List CNSC decisions with pagination, filters, and search.
    """
    logger.info("list_decisions", page=page, ruling=ruling, year=year, search=search)

    # Check if database is available
    if not is_db_available():
        return DecisionListResponse(
            decisions=[],
            total=0,
            page=page,
            page_size=page_size,
        )

    # Build query
    query = select(DecizieCNSC)

    # Apply filters
    if ruling:
        query = query.where(DecizieCNSC.solutie_contestatie == ruling)
    if year:
        query = query.where(DecizieCNSC.an_bo == year)
    if tip_contestatie:
        query = query.where(DecizieCNSC.tip_contestatie == tip_contestatie)
    if search:
        search_term = f"%{search.strip()}%"

        # Search CPV nomenclator for matching descriptions → get CPV codes
        cpv_subquery = (
            select(NomenclatorCPV.cod_cpv)
            .where(NomenclatorCPV.descriere.ilike(search_term))
        )

        query = query.where(
            or_(
                cast(DecizieCNSC.numar_bo, String).ilike(search_term),
                DecizieCNSC.contestator.ilike(search_term),
                DecizieCNSC.autoritate_contractanta.ilike(search_term),
                DecizieCNSC.cod_cpv.ilike(search_term),
                DecizieCNSC.cpv_descriere.ilike(search_term),
                DecizieCNSC.cpv_categorie.ilike(search_term),
                DecizieCNSC.cpv_clasa.ilike(search_term),
                DecizieCNSC.filename.ilike(search_term),
                # Match decisions whose CPV code appears in nomenclator
                # entries matching the search term
                DecizieCNSC.cod_cpv.in_(cpv_subquery),
            )
        )

    # Order by date descending
    query = query.order_by(DecizieCNSC.data_decizie.desc().nullslast())

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    # Execute query
    result = await session.execute(query)
    decisions_db = result.scalars().all()

    # Map to response model
    decisions = [
        DecisionSummary(
            id=d.id,
            filename=d.filename,
            numar_bo=d.numar_bo,
            an_bo=d.an_bo,
            numar_decizie=d.numar_decizie,
            data_decizie=d.data_decizie.isoformat() if d.data_decizie else None,
            tip_contestatie=d.tip_contestatie,
            coduri_critici=d.coduri_critici or [],
            cod_cpv=d.cod_cpv,
            cpv_descriere=d.cpv_descriere,
            solutie_contestatie=d.solutie_contestatie,
            contestator=d.contestator,
            autoritate_contractanta=d.autoritate_contractanta,
            rezumat=(d.text_integral[:300] + "...") if d.text_integral and len(d.text_integral) > 300 else d.text_integral,
        )
        for d in decisions_db
    ]

    return DecisionListResponse(
        decisions=decisions,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{decision_id}", response_model=Decision)
async def get_decision(
    decision_id: str,
    session: AsyncSession = Depends(get_session),
) -> Decision:
    """
    Get a specific CNSC decision by ID (UUID) or external ID (BO{year}_{number}).
    """
    logger.info("get_decision", decision_id=decision_id)

    # Check if database is available
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    # Try external ID format first (BO2025_1000)
    bo_match = re.match(r'^BO(\d{4})[_\-](\d+)$', decision_id, re.IGNORECASE)
    if bo_match:
        an_bo = int(bo_match.group(1))
        numar_bo = int(bo_match.group(2))
        query = (
            select(DecizieCNSC)
            .where(DecizieCNSC.an_bo == an_bo)
            .where(DecizieCNSC.numar_bo == numar_bo)
        )
    else:
        # Query by UUID
        query = select(DecizieCNSC).where(DecizieCNSC.id == decision_id)

    result = await session.execute(query)
    decision_db = result.scalar_one_or_none()

    if not decision_db:
        raise HTTPException(status_code=404, detail="Decision not found")

    # Map to response model
    return Decision(
        id=decision_db.id,
        title=f"Decizie {decision_db.numar_decizie or 'N/A'} - BO{decision_db.an_bo}/{decision_db.numar_bo}",
        content=decision_db.text_integral or "",
        metadata=DecisionMetadata(
            case_number=f"BO{decision_db.an_bo}_{decision_db.numar_bo}",
            date=decision_db.data_decizie.isoformat() if decision_db.data_decizie else None,
            bulletin=f"{decision_db.numar_bo}/{decision_db.an_bo}",
            year=decision_db.an_bo,
            cpv_codes=[decision_db.cod_cpv] if decision_db.cod_cpv else [],
            criticism_codes=decision_db.coduri_critici or [],
            ruling=decision_db.solutie_contestatie,
            parties={
                "contestator": decision_db.contestator,
                "autoritate_contractanta": decision_db.autoritate_contractanta,
            },
        ),
        created_at=decision_db.created_at.isoformat() if decision_db.created_at else "",
        updated_at=decision_db.updated_at.isoformat() if decision_db.updated_at else "",
    )


@router.post("/upload")
async def upload_decision(
    file: UploadFile = File(..., description="Decision file (.txt or .pdf)"),
):
    """
    Upload a new CNSC decision for processing.

    The decision will be parsed, metadata extracted, and indexed for search.
    """
    logger.info("upload_decision", filename=file.filename, content_type=file.content_type)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    if not file.filename.endswith((".txt", ".pdf")):
        raise HTTPException(
            status_code=400,
            detail="Only .txt and .pdf files are supported",
        )

    # TODO: Implement file processing
    # 1. Save file to storage
    # 2. Parse content
    # 3. Extract metadata
    # 4. Generate embeddings
    # 5. Store in database

    return {
        "status": "accepted",
        "filename": file.filename,
        "message": "Decision uploaded for processing",
    }


@router.get("/stats/overview")
async def get_stats(
    session: AsyncSession = Depends(get_session),
):
    """
    Get statistics overview for the decision database.
    """
    if not is_db_available():
        return {
            "total_decisions": 0,
            "by_ruling": {},
            "by_type": {},
            "last_updated": None,
        }

    # Total count
    total_result = await session.execute(select(func.count()).select_from(DecizieCNSC))
    total = total_result.scalar() or 0

    # Count by ruling (solutie_contestatie)
    ruling_result = await session.execute(
        select(DecizieCNSC.solutie_contestatie, func.count())
        .group_by(DecizieCNSC.solutie_contestatie)
    )
    by_ruling = {row[0] or "NECUNOSCUT": row[1] for row in ruling_result.all()}

    # Count by type (tip_contestatie)
    type_result = await session.execute(
        select(DecizieCNSC.tip_contestatie, func.count())
        .group_by(DecizieCNSC.tip_contestatie)
    )
    by_type = {row[0] or "necunoscut": row[1] for row in type_result.all()}

    # Last updated
    last_result = await session.execute(
        select(func.max(DecizieCNSC.created_at))
    )
    last_updated = last_result.scalar()

    return {
        "total_decisions": total,
        "by_ruling": by_ruling,
        "by_type": by_type,
        "last_updated": last_updated.isoformat() if last_updated else None,
    }
