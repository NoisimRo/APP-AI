"""Training material generation API endpoints.

Generates didactic materials for public procurement training,
grounded in real legislation and CNSC jurisprudence via RAG.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from typing import Literal

from app.core.logging import get_logger
from app.db.session import get_session
from app.services.training_generator import (
    TrainingGenerator,
    MATERIAL_TYPES,
    DIFFICULTY_LEVELS,
    LENGTH_OPTIONS,
)
from app.services.export_service import export_markdown, export_docx, export_pdf
from app.services.llm.gemini import GeminiProvider
from app.services.llm.streaming import create_sse_response
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
logger = get_logger(__name__)


class TrainingGenerateRequest(BaseModel):
    """Request payload for training material generation."""

    tema: str = Field(..., min_length=3, max_length=20000)
    tip_material: Literal[
        "speta", "studiu_caz", "situational", "palarii", "dezbatere",
        "quiz", "joc_rol", "erori", "comparativ", "cronologie",
        "program_formare"
    ] = "speta"
    nivel_dificultate: Literal["usor", "mediu", "dificil", "foarte_dificil"] = "mediu"
    lungime: Literal["scurt", "mediu", "lung", "extins"] = "mediu"
    context_suplimentar: str = Field(default="", max_length=50000)
    public_tinta: str = Field(default="", max_length=5000, description="Target audience")
    program_plan: str = Field(default="", max_length=50000, description="Training program plan for program_formare mode")
    batch_index: int | None = Field(default=None, description="Current batch index (1-based)")
    batch_total: int | None = Field(default=None, description="Total batch count")


class TrainingExportRequest(BaseModel):
    """Request payload for exporting training materials."""

    content: str = Field(..., min_length=1)
    format: Literal["docx", "pdf", "md"] = "docx"
    titlu: str = Field(default="Material Didactic TrainingAP", max_length=200)
    metadata: dict | None = None


class TrainingGenerateResponse(BaseModel):
    """Response payload for training material generation."""

    material: str
    cerinte: str
    rezolvare: str
    note_trainer: str
    full_content: str
    legislatie_citata: list[str] = Field(default_factory=list)
    jurisprudenta_citata: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


@router.get("/types")
async def get_material_types():
    """Return available material types, difficulty levels, and length options."""
    return {
        "material_types": MATERIAL_TYPES,
        "difficulty_levels": DIFFICULTY_LEVELS,
        "length_options": LENGTH_OPTIONS,
    }


@router.post("/generate", response_model=TrainingGenerateResponse)
async def generate_material(
    request: TrainingGenerateRequest,
    session: AsyncSession = Depends(get_session),
) -> TrainingGenerateResponse:
    """Generate a training material using LLM with RAG grounding."""
    logger.info(
        "training_generate_request",
        tema=request.tema[:80],
        tip=request.tip_material,
        nivel=request.nivel_dificultate,
        lungime=request.lungime,
    )

    generator = TrainingGenerator()

    try:
        result = await generator.generate(
            tema=request.tema,
            tip_material=request.tip_material,
            nivel_dificultate=request.nivel_dificultate,
            lungime=request.lungime,
            context_suplimentar=request.context_suplimentar,
            public_tinta=request.public_tinta,
            program_plan=request.program_plan,
            batch_index=request.batch_index,
            batch_total=request.batch_total,
            session=session,
        )

        logger.info(
            "training_material_generated",
            tip=request.tip_material,
            full_content_length=len(result["full_content"]),
            jurisprudenta_count=len(result["jurisprudenta_citata"]),
            legislatie_count=len(result["legislatie_citata"]),
        )

        return TrainingGenerateResponse(**result)

    except Exception as e:
        logger.error("training_generate_error", error=str(e))
        raise HTTPException(status_code=500, detail=f"Eroare la generare: {str(e)}")


@router.post("/generate/stream")
async def generate_material_stream(
    request: TrainingGenerateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Stream a training material generation via SSE."""
    logger.info(
        "training_generate_stream_request",
        tema=request.tema[:80],
        tip=request.tip_material,
    )

    generator = TrainingGenerator()

    try:
        user_prompt, system_prompt, metadata = await generator.prepare_for_streaming(
            tema=request.tema,
            tip_material=request.tip_material,
            nivel_dificultate=request.nivel_dificultate,
            lungime=request.lungime,
            context_suplimentar=request.context_suplimentar,
            public_tinta=request.public_tinta,
            program_plan=request.program_plan,
            batch_index=request.batch_index,
            batch_total=request.batch_total,
            session=session,
        )

        # Token budget per length (4 sections × words per section × ~1.5 tokens/word)
        token_budgets = {
            "scurt": 4096,     # 4 × ~200 words
            "mediu": 8192,     # 4 × ~400 words
            "lung": 16384,     # 4 × ~800 words
            "extins": 24576,   # 4 × ~1500 words
        }
        max_tokens = token_budgets.get(request.lungime, 8192)
        # Program formare needs much more tokens — it's a full multi-module program
        if request.tip_material == "program_formare":
            max_tokens = 65536

        return await create_sse_response(
            llm=generator.llm,
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.4,
            max_tokens=max_tokens,
            metadata=metadata,
            strip_preamble=True,
        )

    except Exception as e:
        logger.error("training_stream_error", error=str(e))
        raise HTTPException(status_code=500, detail=f"Eroare la generare: {str(e)}")


@router.post("/export")
async def export_material(request: TrainingExportRequest):
    """Export a training material to DOCX, PDF, or MD format."""
    logger.info(
        "training_export_request",
        format=request.format,
        content_length=len(request.content),
    )

    try:
        if request.format == "md":
            data = export_markdown(request.content, request.titlu)
            return Response(
                content=data,
                media_type="text/markdown",
                headers={
                    "Content-Disposition": f'attachment; filename="{request.titlu}.md"',
                },
            )

        elif request.format == "docx":
            data = export_docx(request.content, request.titlu, request.metadata)
            return Response(
                content=data,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={
                    "Content-Disposition": f'attachment; filename="{request.titlu}.docx"',
                },
            )

        elif request.format == "pdf":
            data = export_pdf(request.content, request.titlu, request.metadata)
            return Response(
                content=data,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="{request.titlu}.pdf"',
                },
            )

        else:
            raise HTTPException(status_code=400, detail=f"Format necunoscut: {request.format}")

    except ImportError as e:
        logger.error("training_export_import_error", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Librăria de export nu este instalată: {str(e)}",
        )
    except Exception as e:
        logger.error("training_export_error", error=str(e))
        raise HTTPException(status_code=500, detail=f"Eroare la export: {str(e)}")
