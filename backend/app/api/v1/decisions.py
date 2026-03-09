"""Decisions API endpoints."""

import re

from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session, is_db_available
from app.models.decision import DecizieCNSC, ArgumentareCritica, NomenclatorCPV

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
    argumentatie_cnsc_snippet: str | None = None
    # Status flags for semaphore indicator
    has_analysis: bool = False
    has_embeddings: bool = False


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
    year: int | None = Query(None, ge=2000, le=2100, description="Filter by single year (legacy)"),
    years: str | None = Query(None, description="Comma-separated years to filter by (e.g. 2025,2026)"),
    tip_contestatie: str | None = Query(None, description="Filter by type: documentatie or rezultat"),
    search: str | None = Query(None, description="Search by BO number, contestator, autoritate, CPV, or criticism codes"),
    coduri_critici: str | None = Query(None, description="Comma-separated critique codes to filter by (e.g. D1,R2,R4)"),
    cpv_codes: str | None = Query(None, description="Comma-separated CPV codes to filter by (e.g. 45000000-7,71000000-8)"),
    session: AsyncSession = Depends(get_session),
) -> DecisionListResponse:
    """
    List CNSC decisions with pagination, filters, and search.
    """
    logger.info("list_decisions", page=page, ruling=ruling, year=year, years=years,
                search=search, coduri_critici=coduri_critici, cpv_codes=cpv_codes)

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

    # Multi-year support: 'years' param takes precedence over 'year'
    if years:
        year_list = [int(y.strip()) for y in years.split(",") if y.strip().isdigit()]
        if len(year_list) == 1:
            query = query.where(DecizieCNSC.an_bo == year_list[0])
        elif year_list:
            query = query.where(DecizieCNSC.an_bo.in_(year_list))
    elif year:
        query = query.where(DecizieCNSC.an_bo == year)

    if tip_contestatie:
        query = query.where(DecizieCNSC.tip_contestatie == tip_contestatie)

    # Filter by critique codes (array overlap: decision must contain at least one of the given codes)
    if coduri_critici:
        codes = [c.strip() for c in coduri_critici.split(",") if c.strip()]
        if codes:
            # PostgreSQL array overlap operator: && checks if arrays share any element
            query = query.where(DecizieCNSC.coduri_critici.overlap(codes))

    # Filter by CPV codes (exact prefix match)
    if cpv_codes:
        cpv_list = [c.strip() for c in cpv_codes.split(",") if c.strip()]
        if cpv_list:
            cpv_conditions = [DecizieCNSC.cod_cpv.ilike(f"{cpv}%") for cpv in cpv_list]
            query = query.where(or_(*cpv_conditions))

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

    # Fetch analysis and embedding status for each decision (batch)
    decision_ids = [d.id for d in decisions_db]
    arg_snippets: dict[str, str] = {}
    analyzed_ids: set[str] = set()
    embedded_ids: set[str] = set()

    if decision_ids:
        # Get CNSC argumentation snippets + check analysis status
        arg_query = (
            select(
                ArgumentareCritica.decizie_id,
                func.min(ArgumentareCritica.argumentatie_cnsc).label("snippet"),
            )
            .where(
                ArgumentareCritica.decizie_id.in_(decision_ids),
                ArgumentareCritica.argumentatie_cnsc.isnot(None),
            )
            .group_by(ArgumentareCritica.decizie_id)
        )
        arg_result = await session.execute(arg_query)
        for row in arg_result:
            text = row.snippet or ""
            arg_snippets[row.decizie_id] = (text[:250] + "...") if len(text) > 250 else text

        # Check which decisions have ArgumentareCritica entries (= analyzed)
        analyzed_query = (
            select(ArgumentareCritica.decizie_id)
            .where(ArgumentareCritica.decizie_id.in_(decision_ids))
            .distinct()
        )
        analyzed_result = await session.execute(analyzed_query)
        analyzed_ids = {row[0] for row in analyzed_result}

        # Check which decisions have at least one embedded ArgumentareCritica
        embedded_query = (
            select(ArgumentareCritica.decizie_id)
            .where(
                ArgumentareCritica.decizie_id.in_(decision_ids),
                ArgumentareCritica.embedding.isnot(None),
            )
            .distinct()
        )
        embedded_result = await session.execute(embedded_query)
        embedded_ids = {row[0] for row in embedded_result}

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
            argumentatie_cnsc_snippet=arg_snippets.get(d.id),
            has_analysis=d.id in analyzed_ids,
            has_embeddings=d.id in embedded_ids,
        )
        for d in decisions_db
    ]

    return DecisionListResponse(
        decisions=decisions,
        total=total,
        page=page,
        page_size=page_size,
    )


class AnalysisChunk(BaseModel):
    """A single analysis chunk from ArgumentareCritica."""
    cod_critica: str | None
    argumente_contestator: str | None
    argumente_ac: str | None
    argumente_intervenienti: list | None = None
    elemente_retinute_cnsc: str | None
    argumentatie_cnsc: str | None
    castigator_critica: str | None
    jurisprudenta_contestator: list[str] | None = None
    jurisprudenta_ac: list[str] | None = None
    jurisprudenta_cnsc: list[str] | None = None


class DecisionAnalysisResponse(BaseModel):
    """Response with LLM analysis for a decision."""
    decision_id: str
    external_id: str
    chunks: list[AnalysisChunk]


# --- Static path endpoints MUST come before /{decision_id} ---


@router.get("/filters/cpv-codes")
async def get_cpv_filter_options(
    search: str | None = Query(None, description="Search term to filter CPV codes"),
    session: AsyncSession = Depends(get_session),
):
    """Get distinct CPV codes used in decisions, with optional search filtering."""
    if not is_db_available():
        return []

    query = (
        select(
            DecizieCNSC.cod_cpv,
            DecizieCNSC.cpv_descriere,
            func.count().label("count"),
        )
        .where(DecizieCNSC.cod_cpv.isnot(None))
        .group_by(DecizieCNSC.cod_cpv, DecizieCNSC.cpv_descriere)
        .order_by(func.count().desc())
    )

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                DecizieCNSC.cod_cpv.ilike(term),
                DecizieCNSC.cpv_descriere.ilike(term),
            )
        )
        query = query.limit(50)
    else:
        query = query.limit(100)

    result = await session.execute(query)
    return [
        {"code": row.cod_cpv, "description": row.cpv_descriere, "count": row.count}
        for row in result
    ]


@router.get("/filters/critici-codes")
async def get_critici_filter_options(
    session: AsyncSession = Depends(get_session),
):
    """Get distinct critique codes used in decisions with counts."""
    if not is_db_available():
        return []

    # Unnest the coduri_critici array and count occurrences
    query = (
        select(
            func.unnest(DecizieCNSC.coduri_critici).label("cod"),
            func.count().label("count"),
        )
        .where(DecizieCNSC.coduri_critici.isnot(None))
        .group_by("cod")
        .order_by("cod")
    )

    result = await session.execute(query)
    return [
        {"code": row.cod, "count": row.count}
        for row in result
    ]


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
    return {
        "status": "accepted",
        "filename": file.filename,
        "message": "Decision uploaded for processing",
    }


# --- Dynamic path endpoints ---


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


@router.get("/{decision_id}/analysis", response_model=DecisionAnalysisResponse)
async def get_decision_analysis(
    decision_id: str,
    session: AsyncSession = Depends(get_session),
) -> DecisionAnalysisResponse:
    """Get the LLM analysis (ArgumentareCritica) for a decision."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    # Resolve decision by external ID or UUID
    bo_match = re.match(r'^BO(\d{4})[_\-](\d+)$', decision_id, re.IGNORECASE)
    if bo_match:
        an_bo = int(bo_match.group(1))
        numar_bo = int(bo_match.group(2))
        dec_query = select(DecizieCNSC).where(
            DecizieCNSC.an_bo == an_bo, DecizieCNSC.numar_bo == numar_bo
        )
    else:
        dec_query = select(DecizieCNSC).where(DecizieCNSC.id == decision_id)

    dec_result = await session.execute(dec_query)
    decision = dec_result.scalar_one_or_none()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")

    # Fetch all ArgumentareCritica for this decision
    arg_query = (
        select(ArgumentareCritica)
        .where(ArgumentareCritica.decizie_id == decision.id)
        .order_by(ArgumentareCritica.cod_critica)
    )
    arg_result = await session.execute(arg_query)
    args = arg_result.scalars().all()

    chunks = [
        AnalysisChunk(
            cod_critica=a.cod_critica,
            argumente_contestator=a.argumente_contestator,
            argumente_ac=a.argumente_ac,
            argumente_intervenienti=a.argumente_intervenienti,
            elemente_retinute_cnsc=a.elemente_retinute_cnsc,
            argumentatie_cnsc=a.argumentatie_cnsc,
            castigator_critica=a.castigator_critica,
            jurisprudenta_contestator=a.jurisprudenta_contestator,
            jurisprudenta_ac=a.jurisprudenta_ac,
            jurisprudenta_cnsc=a.jurisprudenta_cnsc,
        )
        for a in args
    ]

    return DecisionAnalysisResponse(
        decision_id=decision.id,
        external_id=f"BO{decision.an_bo}_{decision.numar_bo}",
        chunks=chunks,
    )
