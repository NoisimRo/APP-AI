"""Decisions API endpoints."""

import json
import re
from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session, is_db_available
from app.models.decision import DecizieCNSC, ArgumentareCritica, NomenclatorCPV
from app.services.parser import CNSCDecisionParser

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
    categorie: str | None = Query(None, description="Filter by CPV category: Furnizare, Servicii, Lucrări"),
    clasa: str | None = Query(None, description="Filter by CPV product class"),
    session: AsyncSession = Depends(get_session),
) -> DecisionListResponse:
    """
    List CNSC decisions with pagination, filters, and search.
    """
    logger.info("list_decisions", page=page, ruling=ruling, year=year, years=years,
                search=search, coduri_critici=coduri_critici, cpv_codes=cpv_codes,
                categorie=categorie, clasa=clasa)

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

    # Apply filters (ruling supports comma-separated values and __NULL__)
    if ruling:
        ruling_values = [r.strip() for r in ruling.split(",") if r.strip()]
        has_null = "__NULL__" in ruling_values
        non_null_values = [r for r in ruling_values if r != "__NULL__"]
        conditions = []
        if non_null_values:
            if len(non_null_values) == 1:
                conditions.append(DecizieCNSC.solutie_contestatie == non_null_values[0])
            else:
                conditions.append(DecizieCNSC.solutie_contestatie.in_(non_null_values))
        if has_null:
            conditions.append(DecizieCNSC.solutie_contestatie.is_(None))
        if conditions:
            query = query.where(or_(*conditions))

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

    # Filter by CPV codes (prefix match for hierarchy)
    if cpv_codes:
        cpv_list = [c.strip() for c in cpv_codes.split(",") if c.strip()]
        if cpv_list:
            cpv_conditions = [DecizieCNSC.cod_cpv.ilike(f"{cpv}%") for cpv in cpv_list]
            query = query.where(or_(*cpv_conditions))

    # Filter by CPV category (Furnizare, Servicii, Lucrări)
    if categorie:
        query = query.where(DecizieCNSC.cpv_categorie == categorie)

    # Filter by CPV product class
    if clasa:
        query = query.where(DecizieCNSC.cpv_clasa == clasa)

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

    # Use COALESCE to prefer decision's cpv_descriere, fall back to nomenclator
    description_col = func.coalesce(
        func.max(DecizieCNSC.cpv_descriere),
        func.max(NomenclatorCPV.descriere),
    ).label("description")

    query = (
        select(
            DecizieCNSC.cod_cpv,
            description_col,
            func.count().label("count"),
        )
        .outerjoin(
            NomenclatorCPV,
            DecizieCNSC.cod_cpv == NomenclatorCPV.cod_cpv,
        )
        .where(DecizieCNSC.cod_cpv.isnot(None))
        .group_by(DecizieCNSC.cod_cpv)
        .order_by(func.count().desc())
    )

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.having(
            or_(
                DecizieCNSC.cod_cpv.ilike(term),
                func.max(DecizieCNSC.cpv_descriere).ilike(term),
                func.max(NomenclatorCPV.descriere).ilike(term),
            )
        )
        query = query.limit(50)
    else:
        query = query.limit(100)

    result = await session.execute(query)
    return [
        {"code": row.cod_cpv, "description": row.description, "count": row.count}
        for row in result
    ]


@router.get("/filters/categorii")
async def get_categorii_filter_options(
    session: AsyncSession = Depends(get_session),
):
    """Get distinct CPV categories (Furnizare/Servicii/Lucrări) with counts."""
    if not is_db_available():
        return []

    query = (
        select(
            DecizieCNSC.cpv_categorie,
            func.count().label("count"),
        )
        .where(DecizieCNSC.cpv_categorie.isnot(None))
        .group_by(DecizieCNSC.cpv_categorie)
        .order_by(func.count().desc())
    )
    result = await session.execute(query)
    return [
        {"name": row.cpv_categorie, "count": row.count}
        for row in result
    ]


@router.get("/filters/clase")
async def get_clase_filter_options(
    categorie: str | None = Query(None, description="Filter classes by category"),
    session: AsyncSession = Depends(get_session),
):
    """Get distinct product classes with counts, optionally filtered by category."""
    if not is_db_available():
        return []

    query = (
        select(
            DecizieCNSC.cpv_clasa,
            func.count().label("count"),
        )
        .where(DecizieCNSC.cpv_clasa.isnot(None))
    )
    if categorie:
        query = query.where(DecizieCNSC.cpv_categorie == categorie)

    query = query.group_by(DecizieCNSC.cpv_clasa).order_by(func.count().desc()).limit(50)
    result = await session.execute(query)
    return [
        {"name": row.cpv_clasa, "count": row.count}
        for row in result
    ]


@router.get("/filters/cpv-tree")
async def get_cpv_tree(
    categorie: str | None = Query(None, description="Filter by category"),
    session: AsyncSession = Depends(get_session),
):
    """Get CPV codes as a hierarchical tree with decision counts.

    Returns divisions (level 1-2) with their children grouped,
    showing how many decisions each node covers.
    """
    if not is_db_available():
        return []

    # Get all CPV codes used in decisions with their counts
    query = (
        select(
            DecizieCNSC.cod_cpv,
            func.coalesce(DecizieCNSC.cpv_descriere, NomenclatorCPV.descriere).label("descriere"),
            DecizieCNSC.cpv_categorie,
            func.count().label("count"),
        )
        .outerjoin(NomenclatorCPV, DecizieCNSC.cod_cpv == NomenclatorCPV.cod_cpv)
        .where(DecizieCNSC.cod_cpv.isnot(None))
    )
    if categorie:
        query = query.where(DecizieCNSC.cpv_categorie == categorie)

    query = query.group_by(DecizieCNSC.cod_cpv, DecizieCNSC.cpv_descriere, DecizieCNSC.cpv_categorie, NomenclatorCPV.descriere)
    result = await session.execute(query)
    cpv_rows = result.all()

    # Get division-level descriptions from nomenclator
    div_codes = set()
    for row in cpv_rows:
        if row.cod_cpv and len(row.cod_cpv) >= 2:
            div_codes.add(row.cod_cpv[:2] + "000000-" + row.cod_cpv[-1:] if len(row.cod_cpv) > 3 else row.cod_cpv)

    # Build tree: group by first 2 digits (division)
    divisions: dict = {}
    for row in cpv_rows:
        cod = row.cod_cpv
        if not cod or len(cod) < 3:
            continue
        div_key = cod[:2]  # First 2 digits = division

        if div_key not in divisions:
            divisions[div_key] = {
                "code": div_key,
                "description": None,
                "categorie": row.cpv_categorie,
                "count": 0,
                "children": [],
            }
        divisions[div_key]["count"] += row.count
        divisions[div_key]["children"].append({
            "code": cod,
            "description": row.descriere,
            "count": row.count,
        })

    # Fetch division-level descriptions from nomenclator
    if divisions:
        div_patterns = [f"{d}000000%" for d in divisions.keys()]
        div_query = (
            select(NomenclatorCPV.cod_cpv, NomenclatorCPV.descriere)
            .where(or_(*[NomenclatorCPV.cod_cpv.ilike(p) for p in div_patterns]))
            .where(NomenclatorCPV.nivel == 1)
        )
        div_result = await session.execute(div_query)
        for drow in div_result:
            dk = drow.cod_cpv[:2]
            if dk in divisions:
                divisions[dk]["description"] = drow.descriere

    # Sort divisions by count desc, children by count desc
    tree = sorted(divisions.values(), key=lambda x: x["count"], reverse=True)
    for div in tree:
        div["children"] = sorted(div["children"], key=lambda x: x["count"], reverse=True)

    return tree


@router.get("/stats/cpv-top")
async def get_cpv_top_stats(
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    """Get top CPV codes by number of decisions — for Dashboard chart."""
    if not is_db_available():
        return []

    query = (
        select(
            DecizieCNSC.cod_cpv,
            func.coalesce(DecizieCNSC.cpv_descriere, NomenclatorCPV.descriere).label("descriere"),
            DecizieCNSC.cpv_categorie,
            func.count().label("count"),
        )
        .outerjoin(NomenclatorCPV, DecizieCNSC.cod_cpv == NomenclatorCPV.cod_cpv)
        .where(DecizieCNSC.cod_cpv.isnot(None))
        .group_by(DecizieCNSC.cod_cpv, DecizieCNSC.cpv_descriere, DecizieCNSC.cpv_categorie, NomenclatorCPV.descriere)
        .order_by(func.count().desc())
        .limit(limit)
    )
    result = await session.execute(query)
    return [
        {
            "code": row.cod_cpv,
            "description": row.descriere,
            "categorie": row.cpv_categorie,
            "count": row.count,
        }
        for row in result
    ]


@router.get("/stats/categorii")
async def get_categorii_stats(
    session: AsyncSession = Depends(get_session),
):
    """Get decision counts by CPV category — for Dashboard pie chart."""
    if not is_db_available():
        return []

    query = (
        select(
            DecizieCNSC.cpv_categorie,
            func.count().label("count"),
        )
        .where(DecizieCNSC.cpv_categorie.isnot(None))
        .group_by(DecizieCNSC.cpv_categorie)
        .order_by(func.count().desc())
    )
    result = await session.execute(query)
    return [
        {"name": row.cpv_categorie, "count": row.count}
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


@router.get("/stats/win-rate-by-category")
async def get_win_rate_by_category(
    session: AsyncSession = Depends(get_session),
):
    """Win rates broken down by CPV category (Furnizare/Servicii/Lucrari) and tip_contestatie."""
    if not is_db_available():
        return []

    query = (
        select(
            DecizieCNSC.cpv_categorie,
            DecizieCNSC.tip_contestatie,
            DecizieCNSC.solutie_contestatie,
            func.count().label("count"),
        )
        .where(DecizieCNSC.cpv_categorie.isnot(None))
        .group_by(DecizieCNSC.cpv_categorie, DecizieCNSC.tip_contestatie, DecizieCNSC.solutie_contestatie)
        .order_by(DecizieCNSC.cpv_categorie)
    )
    result = await session.execute(query)
    rows = result.all()

    # Aggregate into structured response
    categories: dict = {}
    for row in rows:
        cat = row.cpv_categorie
        tip = row.tip_contestatie or "necunoscut"
        sol = row.solutie_contestatie or "NECUNOSCUT"
        cnt = row.count

        if cat not in categories:
            categories[cat] = {"total": 0, "admis": 0, "admis_partial": 0, "respins": 0, "by_type": {}}
        categories[cat]["total"] += cnt
        if sol == "ADMIS":
            categories[cat]["admis"] += cnt
        elif sol == "ADMIS_PARTIAL":
            categories[cat]["admis_partial"] += cnt
        elif sol == "RESPINS":
            categories[cat]["respins"] += cnt

        if tip not in categories[cat]["by_type"]:
            categories[cat]["by_type"][tip] = {"total": 0, "admis": 0, "admis_partial": 0, "respins": 0}
        categories[cat]["by_type"][tip]["total"] += cnt
        if sol == "ADMIS":
            categories[cat]["by_type"][tip]["admis"] += cnt
        elif sol == "ADMIS_PARTIAL":
            categories[cat]["by_type"][tip]["admis_partial"] += cnt
        elif sol == "RESPINS":
            categories[cat]["by_type"][tip]["respins"] += cnt

    return [
        {
            "category": cat,
            "total": data["total"],
            "admis": data["admis"],
            "admis_partial": data["admis_partial"],
            "respins": data["respins"],
            "win_rate": round((data["admis"] + data["admis_partial"]) / data["total"] * 100, 1) if data["total"] > 0 else 0,
            "by_type": [
                {
                    "type": tip,
                    "total": tdata["total"],
                    "admis": tdata["admis"],
                    "admis_partial": tdata["admis_partial"],
                    "respins": tdata["respins"],
                    "win_rate": round((tdata["admis"] + tdata["admis_partial"]) / tdata["total"] * 100, 1) if tdata["total"] > 0 else 0,
                }
                for tip, tdata in data["by_type"].items()
            ],
        }
        for cat, data in categories.items()
    ]


@router.get("/stats/win-rate-by-critici")
async def get_win_rate_by_critici(
    session: AsyncSession = Depends(get_session),
):
    """Win rates by canonical criticism codes only (D1-D8, DAL, R1-R8, RAL).

    Uses decizii_cnsc.coduri_critici (same source as filters/critici-codes)
    cross-referenced with argumentare_critica.castigator_critica for win rates.
    """
    if not is_db_available():
        return []

    # Canonical codes only — same set as used in the filter dropdown
    CANONICAL_CODES = {
        "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "DAL",
        "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "RAL",
    }

    query = (
        select(
            ArgumentareCritica.cod_critica,
            ArgumentareCritica.castigator_critica,
            func.count().label("count"),
        )
        .where(
            ArgumentareCritica.cod_critica.isnot(None),
            ArgumentareCritica.cod_critica.in_(CANONICAL_CODES),
        )
        .group_by(ArgumentareCritica.cod_critica, ArgumentareCritica.castigator_critica)
        .order_by(ArgumentareCritica.cod_critica)
    )
    result = await session.execute(query)
    rows = result.all()

    codes: dict = {}
    for row in rows:
        cod = row.cod_critica
        winner = row.castigator_critica or "unknown"
        cnt = row.count

        if cod not in codes:
            codes[cod] = {"total": 0, "contestator": 0, "autoritate": 0, "partial": 0, "unknown": 0}
        codes[cod]["total"] += cnt
        if winner in codes[cod]:
            codes[cod][winner] += cnt

    result_list = [
        {
            "code": cod,
            "total": data["total"],
            "contestator_wins": data["contestator"],
            "autoritate_wins": data["autoritate"],
            "partial": data["partial"],
            "unknown": data["unknown"],
            "contestator_win_rate": round(
                (data["contestator"] + data["partial"]) / data["total"] * 100, 1
            ) if data["total"] > 0 else 0,
        }
        for cod, data in codes.items()
    ]
    # Sort by total volume descending
    result_list.sort(key=lambda x: x["total"], reverse=True)
    return result_list


@router.get("/stats/cpv-top-grouped")
async def get_cpv_top_grouped(
    limit: int = Query(15, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    """Top CPV groups (3-digit prefix) with win rates.

    Groups CPV codes by their first 3 digits (e.g., 331 = medical,
    555 = catering) to aggregate related codes into meaningful categories.
    For each group, finds the best representative description from the
    nomenclator at the highest hierarchy level (nivel=1 or 2).
    """
    if not is_db_available():
        return []

    # Step 1: Get all decisions with CPV codes and their rulings
    query = (
        select(
            DecizieCNSC.cod_cpv,
            DecizieCNSC.cpv_categorie,
            DecizieCNSC.solutie_contestatie,
            func.count().label("count"),
        )
        .where(DecizieCNSC.cod_cpv.isnot(None))
        .group_by(
            DecizieCNSC.cod_cpv,
            DecizieCNSC.cpv_categorie,
            DecizieCNSC.solutie_contestatie,
        )
    )
    result = await session.execute(query)
    rows = result.all()

    # Step 2: Group by 3-digit prefix
    groups: dict = {}
    for row in rows:
        code = row.cod_cpv
        prefix = code[:3] if code and len(code) >= 3 else code or "?"
        sol = row.solutie_contestatie or ""
        cat = row.cpv_categorie

        if prefix not in groups:
            groups[prefix] = {
                "prefix": prefix,
                "categorie": cat,
                "total": 0,
                "admis": 0,
                "admis_partial": 0,
                "respins": 0,
                "cpv_codes": set(),
            }
        groups[prefix]["total"] += row.count
        groups[prefix]["cpv_codes"].add(code)
        if cat and not groups[prefix]["categorie"]:
            groups[prefix]["categorie"] = cat
        if sol == "ADMIS":
            groups[prefix]["admis"] += row.count
        elif sol == "ADMIS_PARTIAL":
            groups[prefix]["admis_partial"] += row.count
        elif sol == "RESPINS":
            groups[prefix]["respins"] += row.count

    # Step 3: Get group descriptions from nomenclator (nivel 1-2 = Diviziune/Grup)
    prefixes = list(groups.keys())
    if prefixes:
        # Match nomenclator entries whose code starts with the 3-digit prefix
        # Prefer lowest nivel (highest hierarchy) for the best group name
        nom_query = (
            select(
                NomenclatorCPV.cod_cpv,
                NomenclatorCPV.descriere,
                NomenclatorCPV.nivel,
            )
            .where(NomenclatorCPV.nivel.in_([1, 2, 3]))
            .order_by(NomenclatorCPV.nivel)
        )
        nom_result = await session.execute(nom_query)
        nom_rows = nom_result.all()

        # Build prefix -> description mapping
        prefix_desc: dict[str, str] = {}
        for nom in nom_rows:
            nom_prefix = nom.cod_cpv[:3] if nom.cod_cpv and len(nom.cod_cpv) >= 3 else ""
            if nom_prefix and nom_prefix in groups and nom_prefix not in prefix_desc:
                prefix_desc[nom_prefix] = nom.descriere

        for prefix, desc in prefix_desc.items():
            groups[prefix]["description"] = desc

    # Step 4: Sort by total and limit
    sorted_groups = sorted(groups.values(), key=lambda x: x["total"], reverse=True)[:limit]

    return [
        {
            "code": g["prefix"],
            "description": g.get("description", f"Cod CPV {g['prefix']}xxx"),
            "categorie": g["categorie"],
            "total": g["total"],
            "admis": g["admis"],
            "admis_partial": g["admis_partial"],
            "respins": g["respins"],
            "cpv_count": len(g["cpv_codes"]),
            "win_rate": round(
                (g["admis"] + g["admis_partial"]) / g["total"] * 100, 1
            ) if g["total"] > 0 else 0,
        }
        for g in sorted_groups
    ]


class ImportResult(BaseModel):
    """Result of a decision import operation."""
    imported: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
    details: list[dict] = Field(default_factory=list)


@router.post("/import", response_model=ImportResult)
async def import_decisions(
    file: UploadFile = File(..., description="Decision file (.json or .txt)"),
    session: AsyncSession = Depends(get_session),
):
    """Import one or more CNSC decisions from a JSON or .txt file.

    **JSON format** — can contain pre-analyzed decisions with ArgumentareCritica:
    ```json
    {
      "decisions": [
        {
          "filename": "BO2025_1234_R2_CPV_55520000-1_A.txt",
          "text_integral": "...",
          "numar_bo": 1234,
          "an_bo": 2025,
          "coduri_critici": ["R2"],
          "cod_cpv": "55520000-1",
          "solutie_contestatie": "ADMIS",
          "tip_contestatie": "rezultat",
          "contestator": "...",
          "autoritate_contractanta": "...",
          "argumentari": [
            {
              "cod_critica": "R2",
              "argumente_contestator": "...",
              "argumente_ac": "...",
              "argumentatie_cnsc": "...",
              "castigator_critica": "contestator"
            }
          ]
        }
      ]
    }
    ```

    **TXT format** — raw decision text, parsed using the standard CNSC parser.
    The filename must follow the convention: BO{AN}_{NR_BO}_{CRITICI}_CPV_{CPV}_{SOLUTIE}.txt
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Numele fișierului este obligatoriu")

    fname = file.filename.lower()
    if not fname.endswith((".json", ".txt")):
        raise HTTPException(
            status_code=400,
            detail="Doar fișiere .json și .txt sunt acceptate",
        )

    if not is_db_available():
        raise HTTPException(status_code=503, detail="Baza de date nu este disponibilă")

    content_bytes = await file.read()
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = content_bytes.decode("latin-1")

    result = ImportResult()

    if fname.endswith(".json"):
        await _import_from_json(content, session, result)
    else:
        await _import_from_txt(content, file.filename, session, result)

    await session.commit()
    logger.info(
        "import_decisions_complete",
        imported=result.imported,
        skipped=result.skipped,
        errors=len(result.errors),
    )
    return result


@router.post("/import/batch", response_model=ImportResult)
async def import_decisions_batch(
    files: list[UploadFile] = File(..., description="Multiple decision files (.json or .txt)"),
    session: AsyncSession = Depends(get_session),
):
    """Import multiple decision files at once (batch upload)."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Baza de date nu este disponibilă")

    result = ImportResult()

    for file in files:
        if not file.filename:
            result.errors.append("Fișier fără nume — ignorat")
            continue

        fname = file.filename.lower()
        if not fname.endswith((".json", ".txt")):
            result.errors.append(f"{file.filename}: format neacceptat (doar .json/.txt)")
            continue

        content_bytes = await file.read()
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            content = content_bytes.decode("latin-1")

        try:
            if fname.endswith(".json"):
                await _import_from_json(content, session, result)
            else:
                await _import_from_txt(content, file.filename, session, result)
        except Exception as e:
            result.errors.append(f"{file.filename}: {str(e)}")

    await session.commit()
    logger.info(
        "import_batch_complete",
        files=len(files),
        imported=result.imported,
        skipped=result.skipped,
        errors=len(result.errors),
    )
    return result


async def _import_from_json(
    content: str,
    session: AsyncSession,
    result: ImportResult,
) -> None:
    """Import decisions from JSON content."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        result.errors.append(f"JSON invalid: {str(e)}")
        return

    # Support both {"decisions": [...]} and direct [...]
    decisions_data = data if isinstance(data, list) else data.get("decisions", [])

    if not decisions_data:
        result.errors.append("Nu s-au găsit decizii în fișierul JSON")
        return

    for i, dec_data in enumerate(decisions_data):
        try:
            await _import_single_decision_json(dec_data, session, result)
        except Exception as e:
            ref = dec_data.get("filename", f"decizia #{i+1}")
            result.errors.append(f"{ref}: {str(e)}")


async def _import_single_decision_json(
    dec_data: dict,
    session: AsyncSession,
    result: ImportResult,
) -> None:
    """Import a single decision from JSON dict."""
    filename = dec_data.get("filename", "")
    numar_bo = dec_data.get("numar_bo")
    an_bo = dec_data.get("an_bo")
    text_integral = dec_data.get("text_integral", "")

    if not text_integral:
        result.errors.append(f"{filename}: text_integral lipsește")
        return

    # If filename follows convention, try to parse it for missing fields
    parser = CNSCDecisionParser()
    parsed = None
    if filename and re.match(r"^BO\d{4}_", filename, re.IGNORECASE):
        try:
            parsed = parser.parse_text(text_integral, source_file=filename)
        except Exception:
            pass

    # Determine numar_bo and an_bo
    if not numar_bo and parsed:
        numar_bo = parsed.numar_bo
    if not an_bo and parsed:
        an_bo = parsed.an_bo
    if not numar_bo or not an_bo:
        result.errors.append(f"{filename}: numar_bo și an_bo sunt obligatorii")
        return

    # Check if already exists
    existing = await session.execute(
        select(DecizieCNSC.id).where(
            DecizieCNSC.an_bo == an_bo,
            DecizieCNSC.numar_bo == numar_bo,
        )
    )
    if existing.scalar_one_or_none():
        result.skipped += 1
        result.details.append({"filename": filename, "status": "skipped", "reason": "already_exists"})
        return

    # Build the decision model
    if not filename:
        filename = f"BO{an_bo}_{numar_bo}_import.txt"

    # Parse date
    data_decizie = None
    if dec_data.get("data_decizie"):
        try:
            data_decizie = datetime.fromisoformat(dec_data["data_decizie"])
        except (ValueError, TypeError):
            pass
    elif parsed and parsed.data_decizie:
        data_decizie = parsed.data_decizie

    coduri_critici = dec_data.get("coduri_critici", [])
    if not coduri_critici and parsed:
        coduri_critici = parsed.coduri_critici

    tip_contestatie = dec_data.get("tip_contestatie", "documentatie")
    if not tip_contestatie and parsed:
        tip_contestatie = parsed.tip_contestatie.value

    decision = DecizieCNSC(
        id=str(uuid4()),
        filename=filename,
        numar_bo=numar_bo,
        an_bo=an_bo,
        numar_decizie=dec_data.get("numar_decizie") or (parsed.numar_decizie if parsed else None),
        complet=dec_data.get("complet") or (parsed.complet if parsed else None),
        data_decizie=data_decizie,
        tip_contestatie=tip_contestatie,
        coduri_critici=coduri_critici,
        cod_cpv=dec_data.get("cod_cpv") or (parsed.cod_cpv if parsed else None),
        cpv_descriere=dec_data.get("cpv_descriere"),
        solutie_contestatie=dec_data.get("solutie_contestatie") or (
            parsed.solutie_contestatie.value if parsed and parsed.solutie_contestatie else None
        ),
        contestator=dec_data.get("contestator") or (parsed.contestator if parsed else None),
        autoritate_contractanta=dec_data.get("autoritate_contractanta") or (
            parsed.autoritate_contractanta if parsed else None
        ),
        text_integral=text_integral,
        parse_warnings=dec_data.get("parse_warnings", []),
    )
    session.add(decision)

    # Import ArgumentareCritica if provided
    # Support multiple key names for the analysis array
    argumentari = (
        dec_data.get("argumentari")
        or dec_data.get("analysis")
        or dec_data.get("critici")
        or dec_data.get("argumentare_critica")
        or dec_data.get("chunks")
        or []
    )
    for arg_data in argumentari:
        arg = ArgumentareCritica(
            id=str(uuid4()),
            decizie_id=decision.id,
            cod_critica=arg_data.get("cod_critica", "N/A"),
            ordine_in_decizie=arg_data.get("ordine_in_decizie"),
            argumente_contestator=arg_data.get("argumente_contestator"),
            jurisprudenta_contestator=arg_data.get("jurisprudenta_contestator", []),
            argumente_ac=arg_data.get("argumente_ac"),
            jurisprudenta_ac=arg_data.get("jurisprudenta_ac", []),
            argumente_intervenienti=arg_data.get("argumente_intervenienti"),
            elemente_retinute_cnsc=arg_data.get("elemente_retinute_cnsc"),
            argumentatie_cnsc=arg_data.get("argumentatie_cnsc"),
            jurisprudenta_cnsc=arg_data.get("jurisprudenta_cnsc", []),
            castigator_critica=arg_data.get("castigator_critica", "unknown"),
        )
        session.add(arg)

    result.imported += 1
    result.details.append({
        "filename": filename,
        "status": "imported",
        "external_id": f"BO{an_bo}_{numar_bo}",
        "has_analysis": len(argumentari) > 0,
        "analysis_count": len(argumentari),
    })
    logger.info(
        "import_decision_json",
        external_id=f"BO{an_bo}_{numar_bo}",
        argumentari_count=len(argumentari),
        keys_in_json=list(dec_data.keys()),
    )


async def _import_from_txt(
    content: str,
    filename: str,
    session: AsyncSession,
    result: ImportResult,
) -> None:
    """Import a single decision from raw TXT content using the CNSC parser."""
    parser = CNSCDecisionParser()
    try:
        parsed = parser.parse_text(content, source_file=filename)
    except Exception as e:
        result.errors.append(f"{filename}: eroare la parsare — {str(e)}")
        return

    if not parsed.numar_bo or not parsed.an_bo:
        result.errors.append(f"{filename}: nu s-au putut extrage numar_bo și an_bo")
        return

    # Check if already exists
    existing = await session.execute(
        select(DecizieCNSC.id).where(
            DecizieCNSC.an_bo == parsed.an_bo,
            DecizieCNSC.numar_bo == parsed.numar_bo,
        )
    )
    if existing.scalar_one_or_none():
        result.skipped += 1
        result.details.append({"filename": filename, "status": "skipped", "reason": "already_exists"})
        return

    decision = DecizieCNSC(
        id=str(uuid4()),
        filename=parsed.filename,
        numar_bo=parsed.numar_bo,
        an_bo=parsed.an_bo,
        numar_decizie=parsed.numar_decizie,
        complet=parsed.complet,
        data_decizie=parsed.data_decizie,
        tip_contestatie=parsed.tip_contestatie.value,
        coduri_critici=parsed.coduri_critici,
        cod_cpv=parsed.cod_cpv,
        cpv_source=parsed.cpv_source,
        solutie_filename=parsed.solutie_filename.value if parsed.solutie_filename else None,
        solutie_contestatie=parsed.solutie_contestatie.value if parsed.solutie_contestatie else None,
        motiv_respingere=parsed.motiv_respingere,
        contestator=parsed.contestator,
        autoritate_contractanta=parsed.autoritate_contractanta,
        intervenienti=parsed.intervenienti,
        text_integral=parsed.text_integral,
        parse_warnings=parsed.parse_warnings,
    )
    session.add(decision)
    result.imported += 1
    result.details.append({
        "filename": filename,
        "status": "imported",
        "external_id": parsed.external_id,
        "has_analysis": False,
    })


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
