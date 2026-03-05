"""Drafter API endpoint for generating legal complaints."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.services.llm.gemini import GeminiProvider

router = APIRouter()
logger = get_logger(__name__)


class DrafterRequest(BaseModel):
    """Request payload for complaint drafting."""

    facts: str = Field(..., min_length=1, max_length=50000)
    authority_args: str = Field(default="", max_length=50000)
    legal_grounds: str = Field(default="", max_length=5000)


class DrafterResponse(BaseModel):
    """Response payload for complaint drafting."""

    content: str


@router.post("/", response_model=DrafterResponse)
async def draft_complaint(request: DrafterRequest) -> DrafterResponse:
    """Generate a legal complaint draft using LLM."""
    logger.info(
        "draft_complaint_request",
        facts_length=len(request.facts),
        has_authority_args=bool(request.authority_args),
        has_legal_grounds=bool(request.legal_grounds),
    )

    llm = GeminiProvider(model="gemini-2.5-flash")

    prompt = f"""Ești un avocat expert în achiziții publice din România. Redactează o contestație către CNSC (Consiliul Național de Soluționare a Contestațiilor).

Detalii faptice: {request.facts}

Argumente Autoritate Contractantă: {request.authority_args or 'Nu au fost furnizate.'}

Temei legal: {request.legal_grounds or 'Nu a fost specificat.'}

Structura obligatorie a contestației:
1. **Părțile** - Identificarea contestatorului și a autorității contractante
2. **Situația de fapt** - Descrierea cronologică a evenimentelor
3. **Motivele contestației** - Dezvoltare amplă a argumentelor juridice, cu referire la legislația aplicabilă (Legea 98/2016, HG 395/2016, Legea 101/2016)
4. **Solicitare de suspendare** a procedurii de atribuire
5. **Dispozitiv** - Solicitările concrete ale contestatorului

Redactează contestația în limba română, folosind limbaj juridic formal și profesionist.
Fiecare secțiune trebuie să fie clar delimitată cu titluri bold.
Include referințe la articole de lege relevante."""

    try:
        response_text = await llm.complete(
            prompt=prompt,
            temperature=0.3,
            max_tokens=8192,
        )

        logger.info("draft_complaint_generated", length=len(response_text))
        return DrafterResponse(content=response_text)

    except Exception as e:
        logger.error("draft_complaint_error", error=str(e))
        raise
