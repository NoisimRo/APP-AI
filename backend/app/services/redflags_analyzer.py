"""Red flags detection service for procurement documents.

Two-pass architecture to eliminate hallucinations:

Pass 1 — Dynamic Detection:
    LLM reads the full document and identifies potentially problematic clauses.
    No predefined categories, no legal references requested — pure detection.

Pass 2 — Grounding per Red Flag:
    For EACH detected issue, performs vector search against:
    a) articole_legislatie — real articles from Legea 98/2016, HG 395/2016
    b) argumentare_critica — real CNSC decisions/jurisprudence
    Then composes a final LLM call with REAL context to produce the grounded
    red flag with verified legal references and recommendations.
"""

import asyncio
import json
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.decision import ArgumentareCritica, ArticolLegislatie, DecizieCNSC
from app.services.embedding import EmbeddingService
from app.services.llm.gemini import GeminiProvider

logger = get_logger(__name__)

# Max concurrent grounding tasks (avoid overwhelming the API)
MAX_CONCURRENT_GROUNDING = 5


class RedFlagsAnalyzer:
    """Service for analyzing procurement documents for red flags.

    Uses a two-pass approach:
    1. Detection: LLM identifies problematic clauses dynamically
    2. Grounding: Each clause is grounded with real legislation + jurisprudence
    """

    def __init__(self, llm_provider: Optional[GeminiProvider] = None):
        self.llm = llm_provider or GeminiProvider(model="gemini-2.5-flash")
        self.embedding_service = EmbeddingService(llm_provider=self.llm)

    # =========================================================================
    # PASS 1: Dynamic clause detection
    # =========================================================================

    async def _detect_clauses(self, document_text: str) -> list[dict]:
        """Pass 1: LLM detects problematic clauses without legal references.

        The LLM reads the full document and identifies issues dynamically —
        no predefined categories, no legal article references requested.

        Args:
            document_text: Full text of the procurement document.

        Returns:
            List of detected clause dicts with keys:
            - clause: exact text from the document
            - issue: description of why it's problematic
            - search_query: short query optimized for vector search
            - severity: CRITICĂ / MEDIE / SCĂZUTĂ
        """
        system_prompt = """Ești un expert în achiziții publice din România cu experiență vastă în contestații CNSC.

Sarcina ta este să citești integral documentația de achiziție și să identifici TOATE clauzele care ar putea fi:
- Restrictive pentru concurență
- Discriminatorii (favorizează anumiți operatori economici)
- Disproporționate față de obiectul contractului
- Contrare legislației achizițiilor publice (Legea 98/2016, HG 395/2016)
- Neclare sau ambigue în mod care ar putea afecta operatorii economici

IMPORTANT:
- Identifică probleme REALE din document, nu inventa probleme care nu există
- Citează textul EXACT din document pentru fiecare problemă
- NU furniza referințe la articole de lege — acestea vor fi identificate automat ulterior
- Dacă documentul nu conține clauze problematice, returnează o listă goală
- Fii specific: descrie de ce fiecare clauză e problematică
- Pentru search_query: formulează o frază scurtă (10-20 cuvinte) care descrie esența problemei, optimizată pentru căutare semantică în jurisprudența CNSC

Răspunde EXCLUSIV în format JSON:
```json
{
  "detected_clauses": [
    {
      "clause": "textul exact din document",
      "issue": "descrierea detaliată a problemei identificate",
      "search_query": "cerință experiență similară disproporționată restricționare concurență",
      "severity": "CRITICĂ"
    }
  ]
}
```

Severitate:
- CRITICĂ: Încălcare clară a legii, risc foarte mare de anulare
- MEDIE: Clauză discutabilă, potențial restrictivă
- SCĂZUTĂ: Problemă minoră, ar putea fi îmbunătățită"""

        prompt = (
            "Analizează integral următoarea documentație de achiziție publică "
            "și identifică toate clauzele problematice:\n\n"
            f"=== DOCUMENTAȚIE ACHIZIȚIE ===\n{document_text}\n=== SFÂRȘIT DOCUMENTAȚIE ==="
        )

        try:
            response = await self.llm.complete(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.1,
                max_tokens=8192,
            )

            clauses = self._parse_detection_response(response)
            logger.info("clauses_detected", count=len(clauses))
            return clauses

        except Exception as e:
            logger.error("clause_detection_error", error=str(e))
            raise

    @staticmethod
    def _parse_detection_response(response: str) -> list[dict]:
        """Parse Pass 1 JSON response."""
        text = response.strip()

        # Strip markdown code fences
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            result = json.loads(text)
            return result.get("detected_clauses", [])
        except json.JSONDecodeError:
            pass

        # Repair truncated JSON — extract complete objects
        array_start = text.find('"detected_clauses"')
        if array_start == -1:
            logger.error("detection_no_array", response_preview=text[:300])
            return []

        bracket_start = text.find("[", array_start)
        if bracket_start == -1:
            return []

        clauses = []
        i = bracket_start + 1
        while i < len(text):
            obj_start = text.find("{", i)
            if obj_start == -1:
                break
            depth = 0
            obj_end = -1
            for j in range(obj_start, len(text)):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        obj_end = j
                        break
            if obj_end == -1:
                break
            try:
                clause = json.loads(text[obj_start:obj_end + 1])
                clauses.append(clause)
            except json.JSONDecodeError:
                pass
            i = obj_end + 1

        logger.info("detection_json_repaired", count=len(clauses))
        return clauses

    # =========================================================================
    # PASS 2: Grounding per red flag
    # =========================================================================

    async def _search_legislation(
        self,
        query_text: str,
        session: AsyncSession,
        limit: int = 3,
    ) -> list[ArticolLegislatie]:
        """Search legislation articles by vector similarity.

        Args:
            query_text: Description of the issue to find relevant articles for.
            session: Database session.
            limit: Maximum articles to return.

        Returns:
            List of relevant ArticolLegislatie records.
        """
        # Check if any legislation articles exist
        has_articles = await session.scalar(
            select(func.count())
            .select_from(ArticolLegislatie)
            .where(ArticolLegislatie.embedding.isnot(None))
        )

        if not has_articles:
            logger.warning("no_legislation_articles_available")
            return []

        query_vector = await self.embedding_service.embed_query(query_text)

        stmt = (
            select(
                ArticolLegislatie,
                ArticolLegislatie.embedding.cosine_distance(query_vector).label("distance"),
            )
            .where(ArticolLegislatie.embedding.isnot(None))
            .order_by("distance")
            .limit(limit)
        )

        result = await session.execute(stmt)
        rows = result.all()

        # Filter by relevance threshold (cosine distance < 0.6)
        relevant = [row.ArticolLegislatie for row in rows if row.distance < 0.6]

        logger.info(
            "legislation_search",
            query_preview=query_text[:60],
            found=len(relevant),
            top_distance=rows[0].distance if rows else None,
        )

        return relevant

    async def _search_jurisprudence(
        self,
        query_text: str,
        session: AsyncSession,
        limit: int = 5,
    ) -> tuple[list[DecizieCNSC], list[tuple[ArgumentareCritica, float]]]:
        """Search CNSC decisions by vector similarity.

        Args:
            query_text: Description of the issue to find jurisprudence for.
            session: Database session.
            limit: Maximum chunks to return.

        Returns:
            Tuple of (decisions, matched_chunks with distances).
        """
        has_embeddings = await session.scalar(
            select(func.count())
            .select_from(ArgumentareCritica)
            .where(ArgumentareCritica.embedding.isnot(None))
        )

        if not has_embeddings:
            return [], []

        query_vector = await self.embedding_service.embed_query(query_text)

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

        if not rows:
            return [], []

        # Filter by relevance (cosine distance < 0.5 → similarity > 0.5)
        relevant_chunks = [(row.ArgumentareCritica, row.distance) for row in rows if row.distance < 0.5]

        if not relevant_chunks:
            return [], []

        # Load parent decisions
        dec_ids = list({arg.decizie_id for arg, _ in relevant_chunks})
        stmt = select(DecizieCNSC).where(DecizieCNSC.id.in_(dec_ids))
        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        logger.info(
            "jurisprudence_search",
            query_preview=query_text[:60],
            chunks_found=len(relevant_chunks),
            decisions_found=len(decisions),
        )

        return decisions, relevant_chunks

    async def _ground_single_flag(
        self,
        clause_info: dict,
        session: AsyncSession,
    ) -> dict:
        """Ground a single detected clause with real legislation + jurisprudence.

        Performs two vector searches (legislation + CNSC decisions), then
        asks the LLM to compose the final red flag with REAL context.

        Args:
            clause_info: Dict from Pass 1 with clause, issue, search_query.
            session: Database session.

        Returns:
            Grounded red flag dict.
        """
        search_query = clause_info.get("search_query", clause_info.get("issue", ""))

        # Run both searches in parallel
        legislation_task = self._search_legislation(search_query, session, limit=3)
        jurisprudence_task = self._search_jurisprudence(search_query, session, limit=5)
        legal_articles, (decisions, matched_chunks) = await asyncio.gather(
            legislation_task, jurisprudence_task
        )

        # Build legislation context
        legislation_context = ""
        if legal_articles:
            parts = []
            for art in legal_articles:
                parts.append(
                    f"--- {art.act_normativ}, {art.articol} ---\n"
                    f"{art.text_integral}"
                )
            legislation_context = "\n\n".join(parts)

        # Build jurisprudence context
        jurisprudence_context = ""
        available_decisions: dict[str, DecizieCNSC] = {}
        if decisions and matched_chunks:
            available_decisions = {d.external_id: d for d in decisions}
            decision_map = {d.id: d for d in decisions}
            chunks_by_dec: dict[str, list[tuple[ArgumentareCritica, float]]] = {}
            for arg, dist in matched_chunks:
                chunks_by_dec.setdefault(arg.decizie_id, []).append((arg, dist))

            parts = []
            for dec_id, chunks in chunks_by_dec.items():
                dec = decision_map.get(dec_id)
                if not dec:
                    continue
                section = [
                    f"=== Decizia {dec.external_id} ===",
                    f"Soluție: {dec.solutie_contestatie or 'N/A'}",
                ]
                for arg, dist in chunks:
                    similarity = 1.0 - dist
                    section.append(f"\n--- Critica {arg.cod_critica} (relevanță: {similarity:.2f}) ---")
                    if arg.argumentatie_cnsc:
                        section.append(f"Argumentație CNSC: {arg.argumentatie_cnsc[:600]}")
                    if arg.elemente_retinute_cnsc:
                        section.append(f"Elemente reținute: {arg.elemente_retinute_cnsc[:400]}")
                    if arg.castigator_critica and arg.castigator_critica != "unknown":
                        section.append(f"Câștigător: {arg.castigator_critica}")
                parts.append("\n".join(section))
            jurisprudence_context = "\n\n---\n\n".join(parts)

        # Build grounding prompt
        decision_ids = list(available_decisions.keys())

        system_prompt = """Ești un expert în achiziții publice. Ți se dă o clauză problematică detectată într-o documentație de achiziție, împreună cu ARTICOLE REALE din legislație și DECIZII REALE CNSC.

Sarcina ta: compune analiza finală a problemei folosind EXCLUSIV referințele reale furnizate.

REGULI STRICTE:
- Pentru legal_references: folosește DOAR articolele furnizate mai jos. NU inventa alte articole.
- Dacă niciun articol furnizat nu e relevant, lasă legal_references ca listă goală.
- Pentru decision_refs: folosește DOAR ID-urile de decizii furnizate. NU inventa alte decizii.
- Dacă nicio decizie furnizată nu e relevantă, lasă decision_refs ca listă goală.
- recommendation: bazează-te pe articolele reale pentru a face o recomandare concretă.

Răspunde EXCLUSIV în format JSON:
```json
{
  "clause": "textul exact al clauzei",
  "issue": "descrierea problemei",
  "severity": "CRITICĂ/MEDIE/SCĂZUTĂ",
  "legal_references": [
    {
      "articol": "art. 178",
      "act_normativ": "Legea 98/2016",
      "text_extras": "textul relevant din articol (max 200 caractere)"
    }
  ],
  "decision_refs": ["BO2025_1011"],
  "recommendation": "recomandare concretă bazată pe legislație"
}
```"""

        prompt_parts = [
            f"CLAUZĂ PROBLEMATICĂ DETECTATĂ:\n"
            f"Clauza: \"{clause_info.get('clause', '')}\"\n"
            f"Problemă: {clause_info.get('issue', '')}\n"
            f"Severitate inițială: {clause_info.get('severity', 'MEDIE')}\n"
        ]

        if legislation_context:
            prompt_parts.append(
                f"\n=== ARTICOLE DIN LEGISLAȚIE (REALE, din baza de date) ===\n"
                f"{legislation_context}\n"
                f"=== SFÂRȘIT ARTICOLE ===\n"
            )
        else:
            prompt_parts.append(
                "\nNu s-au găsit articole relevante în baza de date legislativă. "
                "Lasă legal_references ca listă goală [].\n"
            )

        if jurisprudence_context:
            prompt_parts.append(
                f"\n=== JURISPRUDENȚĂ CNSC (REALĂ, din baza de date) ===\n"
                f"Decizii disponibile: {', '.join(decision_ids)}\n"
                f"{jurisprudence_context}\n"
                f"=== SFÂRȘIT JURISPRUDENȚĂ ===\n"
            )
        else:
            prompt_parts.append(
                "\nNu s-a găsit jurisprudență CNSC relevantă. "
                "Lasă decision_refs ca listă goală [].\n"
            )

        prompt_parts.append(
            "\nCompune analiza finală a acestui red flag folosind EXCLUSIV referințele reale de mai sus."
        )

        try:
            response = await self.llm.complete(
                prompt="\n".join(prompt_parts),
                system_prompt=system_prompt,
                temperature=0.1,
                max_tokens=2048,
            )

            grounded = self._parse_grounding_response(response)

            # Post-process: verify decision_refs against actual DB IDs
            valid_ids = set(decision_ids)
            refs = grounded.get("decision_refs", [])
            if isinstance(refs, list):
                grounded["decision_refs"] = [r for r in refs if r in valid_ids]
            else:
                grounded["decision_refs"] = []

            # Post-process: verify legal_references against actual articles found
            valid_articles = {
                (art.act_normativ, art.articol) for art in legal_articles
            }
            legal_refs = grounded.get("legal_references", [])
            if isinstance(legal_refs, list):
                verified_refs = []
                for ref in legal_refs:
                    if isinstance(ref, dict):
                        # Check if this article was actually in our search results
                        act = ref.get("act_normativ", "")
                        art = ref.get("articol", "")
                        # Fuzzy match: the LLM might write "art. 178" vs our "art. 178"
                        if any(art in db_art or db_art in art
                               for db_act, db_art in valid_articles
                               if act in db_act or db_act in act):
                            verified_refs.append(ref)
                grounded["legal_references"] = verified_refs
            else:
                grounded["legal_references"] = []

            # Ensure clause and issue from detection are preserved
            if not grounded.get("clause"):
                grounded["clause"] = clause_info.get("clause", "")
            if not grounded.get("issue"):
                grounded["issue"] = clause_info.get("issue", "")
            if not grounded.get("severity"):
                grounded["severity"] = clause_info.get("severity", "MEDIE")

            return grounded

        except Exception as e:
            logger.error(
                "grounding_error",
                clause_preview=clause_info.get("clause", "")[:80],
                error=str(e),
            )
            # Return ungrounded flag on error
            return {
                "clause": clause_info.get("clause", ""),
                "issue": clause_info.get("issue", ""),
                "severity": clause_info.get("severity", "MEDIE"),
                "legal_references": [],
                "decision_refs": [],
                "recommendation": "Nu s-a putut efectua analiza legislativă automată.",
            }

    @staticmethod
    def _parse_grounding_response(response: str) -> dict:
        """Parse Pass 2 JSON response (single object)."""
        text = response.strip()

        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find a complete JSON object
        start = text.find("{")
        if start == -1:
            return {}

        depth = 0
        for j in range(start, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:j + 1])
                    except json.JSONDecodeError:
                        return {}
        return {}

    # =========================================================================
    # Main analyze method
    # =========================================================================

    async def analyze(
        self,
        document_text: str,
        session: Optional[AsyncSession] = None,
        use_jurisprudence: bool = True,
    ) -> list[dict]:
        """Analyze document for red flags with two-pass grounding.

        Pass 1: Detect problematic clauses dynamically (full document).
        Pass 2: Ground each clause with real legislation + jurisprudence.

        Args:
            document_text: Text of procurement document.
            session: Database session for legislation/jurisprudence lookup.
            use_jurisprudence: Whether to ground with real references.

        Returns:
            List of grounded red flags.
        """
        logger.info(
            "analyzing_red_flags",
            text_length=len(document_text),
            use_jurisprudence=use_jurisprudence,
        )

        # Pass 1: Dynamic detection
        detected_clauses = await self._detect_clauses(document_text)

        if not detected_clauses:
            logger.info("no_red_flags_detected")
            return []

        logger.info("pass1_complete", detected=len(detected_clauses))

        # Pass 2: Grounding per flag
        if use_jurisprudence and session:
            # Use semaphore to limit concurrent API calls
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_GROUNDING)

            async def ground_with_limit(clause: dict) -> dict:
                async with semaphore:
                    return await self._ground_single_flag(clause, session)

            grounded_flags = await asyncio.gather(
                *[ground_with_limit(clause) for clause in detected_clauses]
            )
        else:
            # No grounding — return detection results with empty refs
            grounded_flags = [
                {
                    "clause": c.get("clause", ""),
                    "issue": c.get("issue", ""),
                    "severity": c.get("severity", "MEDIE"),
                    "legal_references": [],
                    "decision_refs": [],
                    "recommendation": "",
                }
                for c in detected_clauses
            ]

        logger.info(
            "red_flags_analyzed",
            count=len(grounded_flags),
            has_jurisprudence=use_jurisprudence and session is not None,
            with_legal_refs=sum(
                1 for f in grounded_flags if f.get("legal_references")
            ),
            with_decision_refs=sum(
                1 for f in grounded_flags if f.get("decision_refs")
            ),
        )

        return grounded_flags
