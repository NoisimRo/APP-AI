"""Clarification request generation API endpoint."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.services.llm.gemini import GeminiProvider

router = APIRouter()
logger = get_logger(__name__)


class ClarificationRequest(BaseModel):
    """Request payload for clarification generation."""

    clause: str = Field(..., min_length=1, max_length=50000)


class ClarificationResponse(BaseModel):
    """Response payload for clarification generation."""

    content: str


@router.post("/", response_model=ClarificationResponse)
async def generate_clarification(request: ClarificationRequest) -> ClarificationResponse:
    """Generate a formal clarification request for a problematic clause."""
    logger.info("clarification_request", clause_length=len(request.clause))

    llm = GeminiProvider(model="gemini-2.5-flash")

    prompt = f"""Ești un expert în achiziții publice din România. Clientul vrea să conteste sau clarifice următoarea clauză din documentația de atribuire:

"{request.clause}"

Redactează o Cerere de Clarificare formală către autoritatea contractantă, care:
1. Este politicoasă și profesională
2. Sugerează subtil nelegalitatea sau caracterul restrictiv al cerinței
3. Face referire la legislația aplicabilă (Legea 98/2016, HG 395/2016)
4. Solicită justificarea obiectivă a cerinței
5. Propune formulări alternative mai puțin restrictive

Structură:
- **Antet** - Către: Autoritatea Contractantă, Ref: Cerere de Clarificare
- **Obiectul clarificării** - Identificarea clauzei problematice
- **Întrebări de clarificare** - Întrebări concrete și bine fundamentate
- **Propuneri** - Sugestii de modificare a clauzei
- **Temei legal** - Referințe la articole de lege relevante

Redactează în limba română, limbaj formal și profesionist."""

    try:
        response_text = await llm.complete(
            prompt=prompt,
            temperature=0.3,
            max_tokens=4096,
        )

        logger.info("clarification_generated", length=len(response_text))
        return ClarificationResponse(content=response_text)

    except Exception as e:
        logger.error("clarification_error", error=str(e))
        raise
