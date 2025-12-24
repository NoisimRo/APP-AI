"""Decisions API endpoints."""

from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel, Field

from app.core.logging import get_logger

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
    title: str
    case_number: str | None
    date: str | None
    ruling: str | None
    cpv_codes: list[str]


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
) -> DecisionListResponse:
    """
    List CNSC decisions with pagination and filters.
    """
    logger.info("list_decisions", page=page, ruling=ruling, year=year)

    # TODO: Implement database query

    return DecisionListResponse(
        decisions=[],
        total=0,
        page=page,
        page_size=page_size,
    )


@router.get("/{decision_id}", response_model=Decision)
async def get_decision(decision_id: str) -> Decision:
    """
    Get a specific CNSC decision by ID.
    """
    logger.info("get_decision", decision_id=decision_id)

    # TODO: Implement database lookup
    raise HTTPException(status_code=404, detail="Decision not found")


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
