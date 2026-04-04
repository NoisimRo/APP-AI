"""Strategy API endpoints — Contestation strategy generation."""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limiter import require_rate_limit, increment_usage
from app.core.deps import require_feature
from app.db.session import get_session, is_db_available
from app.models.decision import User
from app.services.llm.factory import get_active_llm_provider
from app.services.strategy_generator import StrategyGenerator

router = APIRouter()
logger = get_logger(__name__)

ENDPOINT_TIMEOUT = 300  # 5 min — strategy generation is multi-step


class StrategyRequest(BaseModel):
    """Request for generating a contestation strategy."""
    description: str = Field(..., min_length=20, max_length=10000,
                             description="Descrierea situației / obiectului contestației")
    coduri_critici: list[str] = Field(..., min_length=1, max_length=10,
                                      description="Coduri critică: D1-D8, R1-R8, DAL, RAL")
    cod_cpv: Optional[str] = Field(None, description="Cod CPV (ex: 45310000-3)")
    tip_procedura: Optional[str] = Field(None, description="Tip procedură")
    complet: Optional[str] = Field(None, description="Complet CNSC (ex: C5)")
    tip_contestatie: Optional[str] = Field(None, description="documentatie sau rezultat")
    valoare_estimata: Optional[float] = Field(None, description="Valoare estimată contract (RON)")


@router.post("/generate")
async def generate_strategy(
    request: StrategyRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
    _feature: Optional[User] = Depends(require_feature("strategy")),
):
    """Generate a full contestation strategy with per-criticism recommendations.

    Combines historical statistics, RAG-grounded legislation and jurisprudence,
    and LLM reasoning into actionable advice.
    """
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Baza de date indisponibilă")

    logger.info(
        "strategy_request",
        codes=request.coduri_critici,
        cpv=request.cod_cpv,
        complet=request.complet,
    )

    try:
        llm = await get_active_llm_provider(session)
        generator = StrategyGenerator(llm_provider=llm)

        result = await asyncio.wait_for(
            generator.generate_strategy(
                session=session,
                description=request.description,
                coduri_critici=request.coduri_critici,
                cod_cpv=request.cod_cpv,
                tip_procedura=request.tip_procedura,
                complet=request.complet,
                tip_contestatie=request.tip_contestatie,
                valoare_estimata=request.valoare_estimata,
            ),
            timeout=ENDPOINT_TIMEOUT,
        )

        await increment_usage(rate_user, http_request)
        return result

    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Timeout la generarea strategiei. Reîncearcă cu mai puține critici.",
        )
    except Exception as e:
        logger.error("strategy_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Eroare: {str(e)}")
