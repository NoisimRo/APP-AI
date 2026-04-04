"""Compliance checker service for procurement documentation.

Two-pass architecture:

Pass 1 — Identify applicable requirements:
    Based on procedure type and document content, determine which legal
    articles from legislatie_fragmente are applicable.

Pass 2 — Verify compliance per requirement:
    For EACH applicable requirement, check the document against it and
    produce a compliance verdict (CONFORM/NECONFORM/NECLAR) with reasoning.

Output: Compliance matrix with pass/fail per legal article, plus
recommendations for fixing non-compliant items.
"""

import asyncio
import json
import re
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.decision import LegislatieFragment, ActNormativ, ArgumentareCritica, DecizieCNSC
from app.services.embedding import EmbeddingService
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider, get_embedding_provider

logger = get_logger(__name__)

MAX_CONCURRENT_CHECKS = 6
LLM_CALL_TIMEOUT = 120
MAX_REQUIREMENTS = 15  # Cap on number of requirements to check


class ComplianceChecker:
    """Service for checking procurement document compliance against legislation."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm = llm_provider or get_llm_provider()
        self.embedding_service = EmbeddingService(llm_provider=get_embedding_provider())

    async def check_compliance(
        self,
        session: AsyncSession,
        document_text: str,
        tip_procedura: Optional[str] = None,
        tip_document: Optional[str] = None,
    ) -> dict:
        """Check document compliance against applicable legal requirements.

        Args:
            session: Database session.
            document_text: Full text of the procurement document.
            tip_procedura: Procedure type (e.g. "licitație deschisă").
            tip_document: Document type (e.g. "fișa de date", "caiet de sarcini").

        Returns:
            dict with compliance_items, summary, score.
        """
        logger.info(
            "compliance_check_start",
            doc_length=len(document_text),
            tip_procedura=tip_procedura,
            tip_document=tip_document,
        )

        # --- Pass 1: Identify applicable requirements ---
        requirements = await self._identify_requirements(
            session, document_text, tip_procedura, tip_document,
        )

        if not requirements:
            return {
                "compliance_items": [],
                "summary": "Nu s-au identificat cerințe legale aplicabile pentru acest document.",
                "score": None,
                "total_checked": 0,
                "conform": 0,
                "neconform": 0,
                "neclar": 0,
            }

        # --- Pass 2: Check compliance per requirement ---
        # Phase 2a: Fetch jurisprudence context (sequential DB)
        context_map = {}
        for req in requirements:
            query_vector = await self.embedding_service.embed_query(
                f"{req['citare']} {req['descriere'][:200]}"
            )
            # Find relevant CNSC decisions for this requirement
            jurisprudence = await self._search_related_decisions(
                session, query_vector, limit=3,
            )
            context_map[req["citare"]] = jurisprudence

        # Phase 2b: Parallel LLM compliance checks
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
        doc_excerpt = document_text[:12000]  # Cap document for LLM context

        tasks = [
            self._check_single_requirement(
                semaphore, req, doc_excerpt, context_map.get(req["citare"], []),
            )
            for req in requirements
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        compliance_items = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning("compliance_check_failed", requirement=requirements[i]["citare"], error=str(result))
                compliance_items.append({
                    "citare": requirements[i]["citare"],
                    "act": requirements[i].get("act", ""),
                    "text_cerinta": requirements[i].get("text", ""),
                    "descriere": requirements[i].get("descriere", ""),
                    "verdict": "NECLAR",
                    "explicatie": "Nu s-a putut verifica — reîncearcă.",
                    "recomandare": None,
                    "jurisprudenta": [],
                })
            else:
                compliance_items.append(result)

        # Calculate scores
        conform = sum(1 for c in compliance_items if c["verdict"] == "CONFORM")
        neconform = sum(1 for c in compliance_items if c["verdict"] == "NECONFORM")
        neclar = sum(1 for c in compliance_items if c["verdict"] == "NECLAR")
        total = len(compliance_items)
        score = round(conform / total * 100, 1) if total > 0 else 0

        # Generate summary
        summary = await self._generate_summary(
            compliance_items, score, tip_procedura, tip_document,
        )

        result = {
            "compliance_items": compliance_items,
            "summary": summary,
            "score": score,
            "total_checked": total,
            "conform": conform,
            "neconform": neconform,
            "neclar": neclar,
        }

        logger.info(
            "compliance_check_complete",
            total=total, conform=conform, neconform=neconform, score=score,
        )
        return result

    # =========================================================================
    # Pass 1: Identify requirements
    # =========================================================================

    async def _identify_requirements(
        self,
        session: AsyncSession,
        document_text: str,
        tip_procedura: Optional[str],
        tip_document: Optional[str],
    ) -> list[dict]:
        """Identify which legal requirements apply to this document."""
        # Search legislation by document content embedding
        doc_summary = document_text[:3000]
        query = f"cerințe legale obligatorii {tip_document or 'documentație achiziții publice'}"
        if tip_procedura:
            query += f" {tip_procedura}"
        query += f" {doc_summary[:500]}"

        query_vector = await self.embedding_service.embed_query(query)

        # Get relevant legislation fragments
        stmt = (
            select(
                LegislatieFragment,
                ActNormativ,
                LegislatieFragment.embedding.cosine_distance(query_vector).label("distance"),
            )
            .join(ActNormativ, LegislatieFragment.act_id == ActNormativ.id)
            .where(LegislatieFragment.embedding.isnot(None))
            .order_by("distance")
            .limit(30)
        )
        result = await session.execute(stmt)
        rows = result.all()

        candidates = [
            {
                "citare": row.LegislatieFragment.citare,
                "text": row.LegislatieFragment.text_fragment[:600],
                "articol_complet": (row.LegislatieFragment.articol_complet or "")[:800],
                "act": row.ActNormativ.denumire,
                "distance": round(row.distance, 3),
            }
            for row in rows if row.distance < 0.7
        ]

        if not candidates:
            return []

        # Use LLM to filter to truly applicable requirements
        candidates_text = "\n".join(
            f"{i+1}. {c['citare']} ({c['act']}): {c['text'][:300]}"
            for i, c in enumerate(candidates[:20])
        )

        prompt = f"""Ești un expert în achiziții publice din România.

DOCUMENT DE VERIFICAT (extras):
{doc_summary[:2000]}

{f"TIP PROCEDURĂ: {tip_procedura}" if tip_procedura else ""}
{f"TIP DOCUMENT: {tip_document}" if tip_document else ""}

CERINȚE LEGALE CANDIDATE:
{candidates_text}

Din lista de mai sus, selectează DOAR cerințele care sunt EFECTIV APLICABILE documentului prezentat. Pentru fiecare, scrie o descriere scurtă a ce trebuie verificat.

Răspunde în JSON:
[
  {{"index": 1, "descriere": "Documentul trebuie să conțină..."}},
  {{"index": 3, "descriere": "Fișa de date trebuie să specifice..."}},
  ...
]

Selectează maximum {MAX_REQUIREMENTS} cerințe, cele mai relevante."""

        try:
            response = await asyncio.wait_for(
                self.llm.complete(prompt, temperature=0.1, max_tokens=2000),
                timeout=LLM_CALL_TIMEOUT,
            )
            selected = self._extract_json_array(response)
            requirements = []
            for item in selected[:MAX_REQUIREMENTS]:
                idx = item.get("index", 0) - 1
                if 0 <= idx < len(candidates):
                    req = candidates[idx].copy()
                    req["descriere"] = item.get("descriere", req["text"][:200])
                    requirements.append(req)
            return requirements
        except Exception as e:
            logger.warning("compliance_requirements_failed", error=str(e))
            # Fallback: use top candidates directly
            return [
                {**c, "descriere": c["text"][:200]}
                for c in candidates[:MAX_REQUIREMENTS]
            ]

    # =========================================================================
    # Pass 2: Per-requirement checks
    # =========================================================================

    async def _check_single_requirement(
        self,
        semaphore: asyncio.Semaphore,
        requirement: dict,
        document_excerpt: str,
        jurisprudence: list[dict],
    ) -> dict:
        """Check compliance for a single requirement."""
        async with semaphore:
            jur_text = ""
            jur_refs = []
            for j in jurisprudence:
                jur_text += f"- {j['bo_reference']} ({j['solutie']}): {j['argumentatie'][:200]}\n"
                jur_refs.append({"bo_reference": j["bo_reference"], "solutie": j["solutie"]})

            prompt = f"""Ești un verificator de conformitate pentru achiziții publice din România.

CERINȚA LEGALĂ:
{requirement['citare']} ({requirement['act']})
Text complet: {requirement.get('articol_complet', requirement['text'])}

CE TREBUIE VERIFICAT:
{requirement['descriere']}

DOCUMENT DE VERIFICAT (extras):
{document_excerpt[:5000]}

{f"JURISPRUDENȚĂ RELEVANTĂ:{chr(10)}{jur_text}" if jur_text else ""}

Verifică dacă documentul respectă cerința legală de mai sus.

Răspunde în JSON:
{{
  "verdict": "CONFORM" | "NECONFORM" | "NECLAR",
  "explicatie": "Explicație concisă de max 2 propoziții",
  "recomandare": "Ce trebuie corectat" (doar dacă NECONFORM, altfel null),
  "citat_document": "Fragment relevant din document" (max 200 chars, sau null)
}}"""

            try:
                response = await asyncio.wait_for(
                    self.llm.complete(prompt, temperature=0.1, max_tokens=800),
                    timeout=LLM_CALL_TIMEOUT,
                )
                parsed = self._extract_json_object(response)
                return {
                    "citare": requirement["citare"],
                    "act": requirement.get("act", ""),
                    "text_cerinta": requirement.get("text", "")[:300],
                    "descriere": requirement.get("descriere", ""),
                    "verdict": parsed.get("verdict", "NECLAR"),
                    "explicatie": parsed.get("explicatie", ""),
                    "recomandare": parsed.get("recomandare"),
                    "citat_document": parsed.get("citat_document"),
                    "jurisprudenta": jur_refs,
                }
            except asyncio.TimeoutError:
                raise TimeoutError(f"Timeout la verificarea {requirement['citare']}")

    # =========================================================================
    # Helpers
    # =========================================================================

    async def _search_related_decisions(
        self, session: AsyncSession, query_vector, limit: int = 3,
    ) -> list[dict]:
        """Find CNSC decisions related to a legal requirement."""
        stmt = (
            select(
                ArgumentareCritica,
                ArgumentareCritica.embedding.cosine_distance(query_vector).label("distance"),
            )
            .where(ArgumentareCritica.embedding.isnot(None))
            .order_by("distance")
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.all()
        relevant = [(row.ArgumentareCritica, row.distance) for row in rows if row.distance < 0.65]

        if not relevant:
            return []

        from sqlalchemy.orm import defer
        dec_ids = list({a.decizie_id for a, _ in relevant})
        dec_result = await session.execute(
            select(DecizieCNSC).options(defer(DecizieCNSC.text_integral)).where(DecizieCNSC.id.in_(dec_ids))
        )
        dec_map = {str(d.id): d for d in dec_result.scalars().all()}

        return [
            {
                "bo_reference": f"BO{dec_map[str(arg.decizie_id)].an_bo}_{dec_map[str(arg.decizie_id)].numar_bo}",
                "solutie": dec_map[str(arg.decizie_id)].solutie_contestatie,
                "argumentatie": (arg.argumentatie_cnsc or "")[:300],
            }
            for arg, _ in relevant
            if str(arg.decizie_id) in dec_map
        ]

    async def _generate_summary(
        self, items: list[dict], score: float,
        tip_procedura: Optional[str], tip_document: Optional[str],
    ) -> str:
        """Generate a brief summary of compliance results."""
        conform = [i for i in items if i["verdict"] == "CONFORM"]
        neconform = [i for i in items if i["verdict"] == "NECONFORM"]

        if not neconform:
            return f"Documentul respectă toate cele {len(items)} cerințe legale verificate. Scor conformitate: {score}%."

        issues = "\n".join(f"- {i['citare']}: {i['explicatie']}" for i in neconform[:5])
        prompt = f"""Rezumă în 2-3 propoziții rezultatul verificării de conformitate:
- {len(conform)} cerințe conforme, {len(neconform)} neconforme
- Scor: {score}%

Probleme identificate:
{issues}

Scrie un rezumat concis în română."""

        try:
            return await asyncio.wait_for(
                self.llm.complete(prompt, temperature=0.2, max_tokens=300),
                timeout=60,
            )
        except Exception:
            return f"Verificare completă: {len(conform)} conforme, {len(neconform)} neconforme din {len(items)} verificate. Scor: {score}%."

    @staticmethod
    def _extract_json_object(text: str) -> dict:
        """Extract JSON object from LLM response."""
        match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {}

    @staticmethod
    def _extract_json_array(text: str) -> list:
        """Extract JSON array from LLM response."""
        match = re.search(r'```json\s*(\[.*?\])\s*```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return []
