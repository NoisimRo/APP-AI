"""Decisions API endpoints."""

from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session, is_db_available
from app.models.decision import DecizieCNSC

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
    solutie_contestatie: str | None
    autoritate_contractanta: str | None


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
    session: AsyncSession = Depends(get_session),
) -> DecisionListResponse:
    """
    List CNSC decisions with pagination and filters.
    """
    logger.info("list_decisions", page=page, ruling=ruling, year=year)

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
            solutie_contestatie=d.solutie_contestatie,
            autoritate_contractanta=d.autoritate_contractanta,
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
    Get a specific CNSC decision by ID.
    """
    logger.info("get_decision", decision_id=decision_id)

    # Check if database is available
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    # Query decision
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
async def get_stats():
    """
    Get statistics overview for the decision database.
    """
    # TODO: Implement stats calculation

    return {
        "total_decisions": 0,
        "by_ruling": {"ADMIS": 0, "RESPINS": 0, "PARTIAL": 0},
        "by_year": {},
        "by_criticism": {},
        "last_updated": None,
    }
