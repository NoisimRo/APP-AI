"""Red flags detection service for procurement documents.

Two-pass architecture to eliminate hallucinations:

Pass 1 — Dynamic Detection:
    LLM reads the full document and identifies potentially problematic clauses.
    No predefined categories, no legal references requested — pure detection.

Pass 2 — Grounding per Red Flag:
    For EACH detected issue, performs vector search against:
    a) legislatie_fragmente — real articles/alineats/litere from Legea 98/2016, HG 395/2016
    b) argumentare_critica — real CNSC decisions/jurisprudence
    Then composes a final LLM call with REAL context to produce the grounded
    red flag with verified legal references and recommendations.
"""

import asyncio
import json
import re
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.decision import ArgumentareCritica, LegislatieFragment, ActNormativ, DecizieCNSC
from app.services.embedding import EmbeddingService
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider, get_embedding_provider

logger = get_logger(__name__)

# Max concurrent grounding tasks (avoid overwhelming the API)
MAX_CONCURRENT_GROUNDING = 5

# Document chunking thresholds
CHUNK_THRESHOLD = 15000  # chars — documents larger than this get chunked
CHUNK_SIZE = 10000       # chars per chunk
CHUNK_OVERLAP = 1500     # overlap between chunks for context continuity

# Max flags to ground in Pass 2 (to keep total time reasonable)
MAX_FLAGS_TO_GROUND = 15

# Timeout for individual LLM calls (seconds)
LLM_CALL_TIMEOUT = 120


class RedFlagsAnalyzer:
    """Service for analyzing procurement documents for red flags.

    Uses a two-pass approach:
    1. Detection: LLM identifies problematic clauses dynamically
    2. Grounding: Each clause is grounded with real legislation + jurisprudence
    """

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm = llm_provider or get_llm_provider()
        self.embedding_service = EmbeddingService(llm_provider=get_embedding_provider())

    # =========================================================================
    # PASS 1: Dynamic clause detection
    # =========================================================================

    @staticmethod
    def _split_into_chunks(text: str) -> list[str]:
        """Split large document into overlapping chunks for parallel analysis.

        Splits on paragraph boundaries (double newlines) to avoid cutting
        mid-sentence. Each chunk gets CHUNK_OVERLAP chars of overlap with
        the previous chunk for context continuity.

        Args:
            text: Full document text.

        Returns:
            List of text chunks.
        """
        if len(text) <= CHUNK_THRESHOLD:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + CHUNK_SIZE

            if end >= len(text):
                chunks.append(text[start:])
                break

            # Try to split on paragraph boundary (double newline)
            split_zone = text[end - 500:end + 500]
            best_split = split_zone.rfind("\n\n")
            if best_split != -1:
                end = (end - 500) + best_split + 2
            else:
                # Fallback: split on single newline
                best_split = split_zone.rfind("\n")
                if best_split != -1:
                    end = (end - 500) + best_split + 1

            chunks.append(text[start:end])
            start = end - CHUNK_OVERLAP  # overlap for context

        logger.info(
            "document_chunked",
            total_chars=len(text),
            num_chunks=len(chunks),
            chunk_sizes=[len(c) for c in chunks],
        )
        return chunks

    def _get_detection_system_prompt(self) -> str:
        """Return the system prompt for Pass 1 detection."""
        return """Ești un expert în achiziții publice din România cu experiență vastă în contestații CNSC.

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

    async def _detect_single_chunk(self, chunk_text: str, chunk_idx: int, total_chunks: int) -> list[dict]:
        """Run Pass 1 detection on a single chunk with timeout.

        Args:
            chunk_text: Text of the chunk to analyze.
            chunk_idx: Index of this chunk (for logging).
            total_chunks: Total number of chunks (for logging).

        Returns:
            List of detected clause dicts.
        """
        system_prompt = self._get_detection_system_prompt()

        if total_chunks > 1:
            prompt = (
                f"Analizează următoarea SECȚIUNE ({chunk_idx + 1} din {total_chunks}) "
                "dintr-o documentație de achiziție publică "
                "și identifică clauzele problematice:\n\n"
                f"=== SECȚIUNE DOCUMENTAȚIE ===\n{chunk_text}\n=== SFÂRȘIT SECȚIUNE ==="
            )
        else:
            prompt = (
                "Analizează integral următoarea documentație de achiziție publică "
                "și identifică toate clauzele problematice:\n\n"
                f"=== DOCUMENTAȚIE ACHIZIȚIE ===\n{chunk_text}\n=== SFÂRȘIT DOCUMENTAȚIE ==="
            )

        try:
            response = await asyncio.wait_for(
                self.llm.complete(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=0.1,
                    max_tokens=8192,
                ),
                timeout=LLM_CALL_TIMEOUT,
            )

            clauses = self._parse_detection_response(response)
            logger.info("chunk_clauses_detected", chunk=chunk_idx + 1, count=len(clauses))
            return clauses

        except asyncio.TimeoutError:
            logger.error("chunk_detection_timeout", chunk=chunk_idx + 1, timeout=LLM_CALL_TIMEOUT)
            raise
        except Exception as e:
            logger.error("chunk_detection_error", chunk=chunk_idx + 1, error=str(e))
            raise

    @staticmethod
    def _deduplicate_clauses(all_clauses: list[dict]) -> list[dict]:
        """Deduplicate clauses detected across multiple chunks.

        Uses clause text similarity to remove near-duplicates that may
        appear due to chunk overlap.

        Args:
            all_clauses: Combined list of clauses from all chunks.

        Returns:
            Deduplicated list of clauses.
        """
        if len(all_clauses) <= 1:
            return all_clauses

        unique = []
        seen_clauses: list[str] = []

        for clause in all_clauses:
            clause_text = clause.get("clause", "").strip().lower()
            if not clause_text:
                continue

            # Check for substantial overlap with already-seen clauses
            is_duplicate = False
            for seen in seen_clauses:
                # If one clause contains >60% of the other, consider duplicate
                shorter = min(clause_text, seen, key=len)
                longer = max(clause_text, seen, key=len)
                if shorter in longer or (
                    len(shorter) > 50 and shorter[:50] in longer
                ):
                    is_duplicate = True
                    break

            if not is_duplicate:
                unique.append(clause)
                seen_clauses.append(clause_text)

        logger.info(
            "clauses_deduplicated",
            before=len(all_clauses),
            after=len(unique),
        )
        return unique

    async def _detect_clauses(self, document_text: str) -> list[dict]:
        """Pass 1: LLM detects problematic clauses without legal references.

        For large documents (>15K chars), splits into overlapping chunks
        and analyzes each in parallel, then deduplicates results.

        Args:
            document_text: Full text of the procurement document.

        Returns:
            List of detected clause dicts with keys:
            - clause: exact text from the document
            - issue: description of why it's problematic
            - search_query: short query optimized for vector search
            - severity: CRITICĂ / MEDIE / SCĂZUTĂ
        """
        chunks = self._split_into_chunks(document_text)

        if len(chunks) == 1:
            # Small document — single call
            clauses = await self._detect_single_chunk(chunks[0], 0, 1)
            logger.info("clauses_detected", count=len(clauses))
            return clauses

        # Large document — parallel chunk analysis
        logger.info("large_document_parallel_detection", num_chunks=len(chunks))
        tasks = [
            self._detect_single_chunk(chunk, idx, len(chunks))
            for idx, chunk in enumerate(chunks)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect all clauses, skip failed chunks
        all_clauses = []
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("chunk_failed", chunk=idx + 1, error=str(result))
                continue
            all_clauses.extend(result)

        # Deduplicate clauses from overlapping chunks
        clauses = self._deduplicate_clauses(all_clauses)

        logger.info("clauses_detected", count=len(clauses), from_chunks=len(chunks))
        return clauses

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
        query_vector=None,
    ) -> list[tuple[LegislatieFragment, str]]:
        """Search legislation fragments by vector similarity.

        Args:
            query_text: Description of the issue to find relevant articles for.
            session: Database session.
            limit: Maximum fragments to return.
            query_vector: Pre-computed embedding vector (avoids redundant API call).

        Returns:
            List of (LegislatieFragment, act_denumire) tuples.
        """
        if query_vector is None:
            query_vector = await self.embedding_service.embed_query(query_text)

        stmt = (
            select(
                LegislatieFragment,
                ActNormativ,
                LegislatieFragment.embedding.cosine_distance(query_vector).label("distance"),
            )
            .join(ActNormativ, LegislatieFragment.act_id == ActNormativ.id)
            .where(LegislatieFragment.embedding.isnot(None))
            .order_by("distance")
            .limit(limit)
        )

        result = await session.execute(stmt)
        rows = result.all()

        relevant = [
            (row.LegislatieFragment, row.ActNormativ.denumire)
            for row in rows if row.distance < 0.6
        ]

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
        query_vector=None,
    ) -> tuple[list[DecizieCNSC], list[tuple[ArgumentareCritica, float]]]:
        """Search CNSC decisions by vector similarity.

        Args:
            query_text: Description of the issue to find jurisprudence for.
            session: Database session.
            limit: Maximum chunks to return.
            query_vector: Pre-computed embedding vector (avoids redundant API call).

        Returns:
            Tuple of (decisions, matched_chunks with distances).
        """
        if query_vector is None:
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

    async def _fetch_context_for_flag(
        self,
        clause_info: dict,
        session: AsyncSession,
    ) -> dict:
        """Fetch DB context (legislation + jurisprudence) for a single flag.

        IMPORTANT: Must be called sequentially — AsyncSession is NOT safe
        for concurrent use from multiple coroutines.

        Args:
            clause_info: Dict from Pass 1 with clause, issue, search_query.
            session: Database session.

        Returns:
            Dict with legal_articles, decisions, matched_chunks.
        """
        search_query = clause_info.get("search_query", clause_info.get("issue", ""))

        # Embed ONCE, reuse for both searches (avoids 2x Gemini API calls per flag)
        query_vector = await self.embedding_service.embed_query(search_query)

        # Run searches SEQUENTIALLY — AsyncSession cannot handle concurrent queries
        legal_articles = await self._search_legislation(search_query, session, limit=3, query_vector=query_vector)
        decisions, matched_chunks = await self._search_jurisprudence(search_query, session, limit=5, query_vector=query_vector)

        return {
            "legal_articles": legal_articles,
            "decisions": decisions,
            "matched_chunks": matched_chunks,
        }

    async def _ground_single_flag(
        self,
        clause_info: dict,
        context: dict,
    ) -> dict:
        """Ground a single detected clause with pre-fetched context.

        This method only calls the LLM (no DB access), so it's safe to
        run multiple instances in parallel.

        Args:
            clause_info: Dict from Pass 1 with clause, issue, search_query.
            context: Pre-fetched DB context from _fetch_context_for_flag.

        Returns:
            Grounded red flag dict.
        """
        legal_articles = context["legal_articles"]
        decisions = context["decisions"]
        matched_chunks = context["matched_chunks"]

        # Build legislation context with full citation info
        legislation_context = ""
        if legal_articles:
            parts = []
            for frag, act_name in legal_articles:
                header = f"--- {act_name}, {frag.citare} ---"
                # Use articol_complet for richer context if available
                body = frag.articol_complet or frag.text_fragment
                parts.append(f"{header}\n{body}")
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
- Pentru legal_references: folosește DOAR articolele/alineatele furnizate mai jos. NU inventa alte articole.
- Folosește citarea exactă furnizată (ex: "art. 2 alin. (2)"). Poți specifica și litere relevante (ex: "art. 2 alin. (2) lit. a) și b)").
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
      "citare": "art. 2 alin. (2) lit. a) și b)",
      "act_normativ": "Legea 98/2016",
      "text_extras": "textul relevant citat din articol/alineat (max 200 caractere)"
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
            response = await asyncio.wait_for(
                self.llm.complete(
                    prompt="\n".join(prompt_parts),
                    system_prompt=system_prompt,
                    temperature=0.1,
                    max_tokens=2048,
                ),
                timeout=LLM_CALL_TIMEOUT,
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
            # Build lookup: act_name → set of citare strings
            valid_citari = {
                (act_name, frag.citare) for frag, act_name in legal_articles
            }
            # Also index by article number for flexible matching
            valid_art_nums = {
                (act_name, frag.numar_articol) for frag, act_name in legal_articles
            }
            legal_refs = grounded.get("legal_references", [])
            if isinstance(legal_refs, list):
                verified_refs = []
                for ref in legal_refs:
                    if isinstance(ref, dict):
                        act = ref.get("act_normativ", "")
                        citare = ref.get("citare", "")
                        # Exact match on citare
                        if any(citare in db_citare or db_citare in citare
                               for db_act, db_citare in valid_citari
                               if act in db_act or db_act in act):
                            verified_refs.append(ref)
                        else:
                            # Fallback: match by article number
                            art_num_match = re.search(r'art\.\s*(\d+)', citare)
                            if art_num_match:
                                art_num = int(art_num_match.group(1))
                                if any(art_num == db_num
                                       for db_act, db_num in valid_art_nums
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
        import time
        t0 = time.monotonic()

        logger.info(
            "analyzing_red_flags",
            text_length=len(document_text),
            use_jurisprudence=use_jurisprudence,
        )

        # Pass 1: Dynamic detection (with chunking for large documents)
        detected_clauses = await self._detect_clauses(document_text)
        t_detect = time.monotonic()
        logger.info("timing_pass1_detection", duration_s=round(t_detect - t0, 2), clauses=len(detected_clauses))

        if not detected_clauses:
            logger.info("no_red_flags_detected")
            return []

        logger.info("pass1_complete", detected=len(detected_clauses))

        # Cap flags to ground — prioritize by severity
        if len(detected_clauses) > MAX_FLAGS_TO_GROUND:
            severity_order = {"CRITICĂ": 0, "MEDIE": 1, "SCĂZUTĂ": 2}
            detected_clauses.sort(
                key=lambda c: severity_order.get(c.get("severity", "MEDIE"), 1)
            )
            logger.info(
                "flags_capped",
                total_detected=len(detected_clauses),
                capped_to=MAX_FLAGS_TO_GROUND,
            )
            detected_clauses = detected_clauses[:MAX_FLAGS_TO_GROUND]

        # Pass 2: Grounding per flag (two phases to avoid concurrent session use)
        if use_jurisprudence and session:
            # Phase 2a: Fetch all DB context SEQUENTIALLY (AsyncSession is not
            # safe for concurrent use from multiple coroutines)
            logger.info("pass2_fetching_context", count=len(detected_clauses))
            contexts = []
            for clause in detected_clauses:
                ctx = await self._fetch_context_for_flag(clause, session)
                contexts.append(ctx)

            t_context = time.monotonic()
            logger.info("timing_pass2_context_fetch", duration_s=round(t_context - t_detect, 2), flags=len(detected_clauses))

            # Phase 2b: Run LLM grounding calls in PARALLEL (no DB needed)
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_GROUNDING)

            async def ground_with_limit(clause: dict, ctx: dict) -> dict:
                async with semaphore:
                    return await self._ground_single_flag(clause, ctx)

            grounded_flags = await asyncio.gather(
                *[
                    ground_with_limit(clause, ctx)
                    for clause, ctx in zip(detected_clauses, contexts)
                ]
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

        t_end = time.monotonic()
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
        logger.info("timing_redflags_total", duration_s=round(t_end - t0, 2))

        return grounded_flags
