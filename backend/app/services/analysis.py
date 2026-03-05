"""Decision analysis service for ExpertAP.

Uses Gemini LLM to extract structured ArgumentareCritica records
from raw CNSC decision text. This fills the gap between:
  raw text_integral → ArgumentareCritica rows → embeddings → RAG

Each decision is analyzed to extract per-criticism argumentation:
- Contestant arguments
- Authority (AC) arguments
- CNSC analysis and reasoning
- Winner per criticism
"""

import json
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.decision import DecizieCNSC, ArgumentareCritica
from app.services.llm.gemini import GeminiProvider

logger = get_logger(__name__)

ANALYSIS_SYSTEM_PROMPT = """Ești un analist juridic expert în achiziții publice românești, specializat în deciziile CNSC.

Sarcina ta: Analizează textul integral al unei decizii CNSC și extrage argumentația structurată per critică.

Returnează un JSON array cu obiectele de mai jos. Dacă decizia are o singură critică, returnează un singur obiect.

IMPORTANT:
- Extrage TOATE argumentele relevante, nu doar primele rânduri
- Fiecare câmp trebuie să fie un rezumat substanțial (minim 200 de cuvinte dacă informația există)
- Pentru argumente_contestator: include toate motivele de fapt și de drept invocate
- Pentru argumente_ac: include toate apărările formulate
- Pentru elemente_retinute_cnsc: include toate constatările și dovezile analizate
- Pentru argumentatie_cnsc: include raționamentul complet al CNSC cu referiri la articole de lege
- castigator_critica trebuie să fie unul din: "contestator", "autoritate", "partial", "unknown"

Format JSON strict (fără alte texte înainte sau după JSON):
[
  {
    "cod_critica": "R2",
    "ordine_in_decizie": 1,
    "argumente_contestator": "Rezumat detaliat al argumentelor contestatorului...",
    "argumente_ac": "Rezumat detaliat al argumentelor autorității contractante...",
    "elemente_retinute_cnsc": "Elementele de fapt și de drept reținute de CNSC...",
    "argumentatie_cnsc": "Raționamentul și motivarea CNSC, cu referiri la articolele de lege...",
    "castigator_critica": "contestator"
  }
]"""

ANALYSIS_PROMPT_TEMPLATE = """Analizează următoarea decizie CNSC și extrage argumentația structurată.

Decizia: {external_id}
Coduri critici din filename: {coduri_critici}
Tip contestație: {tip_contestatie}
Soluție: {solutie}

TEXT INTEGRAL:
{text_integral}

Returnează DOAR JSON-ul cu argumentația per critică, fără alte explicații."""


class DecisionAnalysisService:
    """Service for extracting structured argumentation from decision text."""

    def __init__(self, llm_provider: Optional[GeminiProvider] = None):
        self.llm = llm_provider or GeminiProvider(model="gemini-3-flash-preview")

    async def analyze_decision(
        self,
        decision: DecizieCNSC,
    ) -> list[dict]:
        """Analyze a single decision and extract argumentation.

        Args:
            decision: The decision to analyze.

        Returns:
            List of dicts with per-criticism argumentation.
        """
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            external_id=decision.external_id,
            coduri_critici=", ".join(decision.coduri_critici) if decision.coduri_critici else "N/A",
            tip_contestatie=decision.tip_contestatie,
            solutie=decision.solutie_contestatie or "N/A",
            text_integral=decision.text_integral[:60000],  # Gemini context limit safety
        )

        try:
            response = await self.llm.complete(
                prompt=prompt,
                system_prompt=ANALYSIS_SYSTEM_PROMPT,
                temperature=0.05,
                max_tokens=8192,
            )

            # Parse JSON from response
            argumentari = self._parse_response(response)

            logger.info(
                "decision_analyzed",
                external_id=decision.external_id,
                critici_found=len(argumentari),
            )
            return argumentari

        except Exception as e:
            logger.error(
                "decision_analysis_failed",
                external_id=decision.external_id,
                error=str(e),
            )
            raise

    def _parse_response(self, response: str) -> list[dict]:
        """Parse LLM response into list of argumentation dicts."""
        # Strip markdown code fences if present
        text = response.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        parsed = json.loads(text)

        if isinstance(parsed, dict):
            parsed = [parsed]

        if not isinstance(parsed, list):
            raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")

        # Validate required fields
        for item in parsed:
            if "cod_critica" not in item:
                raise ValueError("Missing required field: cod_critica")
            # Normalize castigator
            castigator = item.get("castigator_critica", "unknown")
            if castigator not in ("contestator", "autoritate", "partial", "unknown"):
                item["castigator_critica"] = "unknown"

        return parsed

    async def analyze_and_store(
        self,
        session: AsyncSession,
        decision: DecizieCNSC,
        overwrite: bool = False,
    ) -> int:
        """Analyze decision and store ArgumentareCritica records.

        Args:
            session: Database session.
            decision: The decision to analyze.
            overwrite: If True, delete existing records and re-analyze.

        Returns:
            Number of ArgumentareCritica records created.
        """
        # Check if already analyzed
        existing_count = await session.scalar(
            select(func.count())
            .select_from(ArgumentareCritica)
            .where(ArgumentareCritica.decizie_id == decision.id)
        )

        if existing_count and existing_count > 0 and not overwrite:
            logger.info(
                "decision_already_analyzed",
                external_id=decision.external_id,
                existing_count=existing_count,
            )
            return 0

        if overwrite and existing_count:
            # Delete existing
            from sqlalchemy import delete
            await session.execute(
                delete(ArgumentareCritica).where(
                    ArgumentareCritica.decizie_id == decision.id
                )
            )

        # Analyze with LLM
        argumentari = await self.analyze_decision(decision)

        # Create records
        created = 0
        for item in argumentari:
            arg = ArgumentareCritica(
                decizie_id=decision.id,
                cod_critica=item["cod_critica"],
                ordine_in_decizie=item.get("ordine_in_decizie"),
                argumente_contestator=item.get("argumente_contestator"),
                argumente_ac=item.get("argumente_ac"),
                elemente_retinute_cnsc=item.get("elemente_retinute_cnsc"),
                argumentatie_cnsc=item.get("argumentatie_cnsc"),
                castigator_critica=item.get("castigator_critica", "unknown"),
            )
            session.add(arg)
            created += 1

        await session.flush()

        logger.info(
            "argumentari_stored",
            external_id=decision.external_id,
            count=created,
        )
        return created

    async def analyze_all_unprocessed(
        self,
        session: AsyncSession,
        limit: Optional[int] = None,
    ) -> dict:
        """Analyze all decisions that don't have ArgumentareCritica records.

        Args:
            session: Database session.
            limit: Max number of decisions to process.

        Returns:
            Stats dict with counts.
        """
        # Find decisions without argumentari
        subquery = (
            select(ArgumentareCritica.decizie_id)
            .distinct()
        )
        stmt = (
            select(DecizieCNSC)
            .where(DecizieCNSC.id.notin_(subquery))
            .order_by(DecizieCNSC.created_at.desc())
        )
        if limit:
            stmt = stmt.limit(limit)

        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        stats = {
            "total": len(decisions),
            "analyzed": 0,
            "failed": 0,
            "argumentari_created": 0,
            "errors": [],
        }

        logger.info("analyzing_unprocessed_decisions", count=len(decisions))

        for dec in decisions:
            try:
                count = await self.analyze_and_store(session, dec)
                stats["analyzed"] += 1
                stats["argumentari_created"] += count
                # Commit after each decision to avoid losing progress
                await session.commit()
            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append(f"{dec.external_id}: {str(e)}")
                logger.error(
                    "decision_analysis_failed",
                    external_id=dec.external_id,
                    error=str(e),
                )
                await session.rollback()

        logger.info("analysis_completed", **stats)
        return stats
