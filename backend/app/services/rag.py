"""RAG (Retrieval-Augmented Generation) service for ExpertAP.

This service handles:
1. Retrieving relevant CNSC decisions via semantic vector search
2. Searching legislation fragments (legislatie_fragmente) for exact legal text
3. Building rich context from ArgumentareCritica chunks + legislation
4. Generating responses with verified citations

The retrieval strategy uses ArgumentareCritica as the primary search unit
(each row is a natural semantic chunk covering one criticism with full
argumentation flow), then loads the parent DecizieCNSC for metadata.
Falls back to keyword ILIKE search when no embeddings are available.

Legislation search is triggered when the query references specific legal
articles (e.g., "art. 2 alin. 3 lit. b din HG 395").
"""

import asyncio
import re
from typing import Optional
from sqlalchemy import select, or_, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.logging import get_logger
from app.models.decision import (
    DecizieCNSC, ArgumentareCritica, NomenclatorCPV,
    LegislatieFragment, ActNormativ,
)
from app.services.embedding import EmbeddingService
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider, get_embedding_provider

logger = get_logger(__name__)

EXPANSION_PROMPT = """Reformulează următoarea întrebare în 3 variante scurte pentru căutare în jurisprudența CNSC (Consiliul Național de Soluționare a Contestațiilor) din România.
Folosește termeni juridici din achizițiile publice din România.
Răspunde DOAR cu cele 3 variante, una per linie, fără numerotare.

Întrebare: {query}"""

RERANK_PROMPT = """Ordonează următoarele fragmente juridice după relevanță pentru întrebarea:
"{query}"

{chunks_text}

Răspunde DOAR cu numerele în ordinea relevanței, separate prin virgulă.
Exemplu: 3,1,5,2,4"""


class Citation(BaseModel):
    """A citation from a CNSC decision."""

    decision_id: str
    text: str
    verified: bool = True


class RAGService:
    """Service for retrieval-augmented generation over CNSC decisions."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        """Initialize RAG service.

        Args:
            llm_provider: Optional LLM provider for chat. If not provided, uses factory default.
        """
        self.llm = llm_provider or get_llm_provider()
        self.embedding_service = EmbeddingService(llm_provider=get_embedding_provider())

    async def search_by_vector(
        self,
        query: str,
        session: AsyncSession,
        limit: int = 10,
        filters: dict | None = None,
    ) -> list[tuple[ArgumentareCritica, float]]:
        """Search ArgumentareCritica by vector cosine similarity.

        Args:
            query: User's search query.
            session: Database session.
            limit: Maximum number of results.
            filters: Optional filters dict for filtered search.

        Returns:
            List of (ArgumentareCritica, distance) tuples ordered by similarity.
        """
        # Embed the query with retrieval_query task type (asymmetric search)
        query_vector = await self.embedding_service.embed_query(query)

        # Cosine distance search on ArgumentareCritica embeddings
        stmt = (
            select(
                ArgumentareCritica,
                ArgumentareCritica.embedding.cosine_distance(query_vector).label("distance"),
            )
            .where(ArgumentareCritica.embedding.isnot(None))
        )

        # Apply filters via JOIN to DecizieCNSC
        if filters:
            stmt = stmt.join(DecizieCNSC, DecizieCNSC.id == ArgumentareCritica.decizie_id)
            if filters.get("ruling"):
                stmt = stmt.where(DecizieCNSC.solutie_contestatie == filters["ruling"])
            if filters.get("tip_contestatie"):
                stmt = stmt.where(DecizieCNSC.tip_contestatie == filters["tip_contestatie"])
            if filters.get("year"):
                stmt = stmt.where(DecizieCNSC.an_bo == int(filters["year"]))
            if filters.get("coduri_critici"):
                stmt = stmt.where(DecizieCNSC.coduri_critici.overlap(filters["coduri_critici"]))
            if filters.get("cpv_codes"):
                cpv_conditions = [DecizieCNSC.cod_cpv.ilike(f"{c}%") for c in filters["cpv_codes"]]
                stmt = stmt.where(or_(*cpv_conditions))

        stmt = stmt.order_by("distance").limit(limit)

        result = await session.execute(stmt)
        rows = result.all()

        logger.info(
            "vector_search_completed",
            query=query[:80],
            results=len(rows),
            top_distance=rows[0].distance if rows else None,
        )

        return [(row.ArgumentareCritica, row.distance) for row in rows]

    async def _trigram_search(
        self,
        query: str,
        session: AsyncSession,
        limit: int = 15,
        filters: dict | None = None,
    ) -> list[tuple[ArgumentareCritica, float]]:
        """Search ArgumentareCritica via parent decision text_integral trigram similarity.

        Uses pg_trgm extension for fuzzy text matching. Works best for
        keyword-heavy queries where exact terms matter.

        Args:
            query: User's search query.
            session: Database session.
            limit: Maximum results.
            filters: Optional filters dict for filtered search.

        Returns:
            List of (ArgumentareCritica, similarity) tuples, higher = better match.
        """
        # Truncate query for trigram (long queries degrade performance)
        search_text = query[:200]

        try:
            # Build the base query with JOIN to decizii_cnsc
            stmt = (
                select(
                    ArgumentareCritica,
                    func.similarity(DecizieCNSC.text_integral, search_text).label("sim"),
                )
                .join(DecizieCNSC, DecizieCNSC.id == ArgumentareCritica.decizie_id)
                .where(func.similarity(DecizieCNSC.text_integral, search_text) > 0.05)
            )

            # Apply filters
            if filters:
                if filters.get("ruling"):
                    stmt = stmt.where(DecizieCNSC.solutie_contestatie == filters["ruling"])
                if filters.get("tip_contestatie"):
                    stmt = stmt.where(DecizieCNSC.tip_contestatie == filters["tip_contestatie"])
                if filters.get("year"):
                    stmt = stmt.where(DecizieCNSC.an_bo == int(filters["year"]))
                if filters.get("coduri_critici"):
                    stmt = stmt.where(DecizieCNSC.coduri_critici.overlap(filters["coduri_critici"]))
                if filters.get("cpv_codes"):
                    cpv_conditions = [DecizieCNSC.cod_cpv.ilike(f"{c}%") for c in filters["cpv_codes"]]
                    stmt = stmt.where(or_(*cpv_conditions))

            stmt = stmt.order_by(text("sim DESC")).limit(limit)

            result = await session.execute(stmt)
            rows = result.all()

            logger.info(
                "trigram_search_completed",
                query=query[:80],
                results=len(rows),
                top_similarity=rows[0].sim if rows else None,
            )

            # Convert similarity to distance-like score (lower = better) for RRF compatibility
            return [(row.ArgumentareCritica, 1.0 - row.sim) for row in rows]

        except Exception as e:
            logger.warning("trigram_search_failed", error=str(e))
            return []

    def _rrf_merge(
        self,
        vector_results: list[tuple[ArgumentareCritica, float]],
        keyword_results: list[tuple[ArgumentareCritica, float]],
        k_vector: int = 60,
        k_keyword: int = 70,
    ) -> list[tuple[ArgumentareCritica, float]]:
        """Reciprocal Rank Fusion merge of two result lists.

        Normalizes scores from different sources (cosine distance vs trigram similarity)
        without manual calibration.

        Args:
            vector_results: Results from vector search (distance: lower = better).
            keyword_results: Results from trigram search (distance: lower = better).
            k_vector: RRF constant for vector results (lower = more weight).
            k_keyword: RRF constant for keyword results.

        Returns:
            Merged and re-ranked list of (ArgumentareCritica, rrf_score) tuples.
        """
        # Build RRF scores keyed by ArgumentareCritica.id
        scores: dict[str, float] = {}
        chunk_map: dict[str, ArgumentareCritica] = {}

        for rank, (arg, _dist) in enumerate(vector_results):
            scores[arg.id] = scores.get(arg.id, 0) + 1.0 / (k_vector + rank)
            chunk_map[arg.id] = arg

        for rank, (arg, _dist) in enumerate(keyword_results):
            scores[arg.id] = scores.get(arg.id, 0) + 1.0 / (k_keyword + rank)
            chunk_map[arg.id] = arg

        # Sort by RRF score descending, convert to distance-like (invert)
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

        # Normalize RRF scores to 0-1 range as pseudo-distance (lower = better)
        max_score = scores[sorted_ids[0]] if sorted_ids else 1.0
        return [
            (chunk_map[chunk_id], 1.0 - scores[chunk_id] / max_score)
            for chunk_id in sorted_ids
        ]

    async def hybrid_search(
        self,
        query: str,
        session: AsyncSession,
        limit: int = 10,
        filters: dict | None = None,
        expand: bool = True,
    ) -> list[tuple[ArgumentareCritica, float]]:
        """Public hybrid search: vector + trigram + RRF + optional query expansion.

        Combines vector cosine search and pg_trgm trigram similarity,
        merges results with Reciprocal Rank Fusion, and optionally
        expands the query into multiple search variants for better recall.

        Args:
            query: Search query.
            session: Database session.
            limit: Maximum results to return.
            filters: Optional filters dict for scoped search.
            expand: Whether to expand query into multiple variants.

        Returns:
            List of (ArgumentareCritica, distance) tuples, sorted by relevance.
        """
        # Check if embeddings exist
        has_embeddings = await session.scalar(
            select(func.count())
            .select_from(ArgumentareCritica)
            .where(ArgumentareCritica.embedding.isnot(None))
        )

        if not has_embeddings:
            return []

        # Query expansion
        if expand:
            expanded_queries = await self._expand_query(query)
        else:
            expanded_queries = [query]

        # Hybrid search: vector + trigram for each expanded query
        all_tasks = []
        for eq in expanded_queries:
            all_tasks.append(self.search_by_vector(eq, session, limit=limit * 2, filters=filters))
            all_tasks.append(self._trigram_search(eq, session, limit=limit * 2, filters=filters))

        all_results = await asyncio.gather(*all_tasks)

        # Separate vector and trigram results
        all_vector = []
        all_trigram = []
        for i, result_list in enumerate(all_results):
            if i % 2 == 0:
                all_vector.extend(result_list)
            else:
                all_trigram.extend(result_list)

        # Deduplicate each list (keep best score)
        def _dedup(results: list[tuple[ArgumentareCritica, float]]) -> list[tuple[ArgumentareCritica, float]]:
            best: dict[str, tuple[ArgumentareCritica, float]] = {}
            for arg, dist in results:
                if arg.id not in best or dist < best[arg.id][1]:
                    best[arg.id] = (arg, dist)
            return sorted(best.values(), key=lambda x: x[1])

        all_vector = _dedup(all_vector)
        all_trigram = _dedup(all_trigram)

        # RRF merge
        merged = self._rrf_merge(all_vector, all_trigram)

        return merged[:limit]

    async def extract_legislation_from_chunks(
        self,
        matched_chunks: list[tuple[ArgumentareCritica, float]],
        session: AsyncSession,
        max_total: int = 8,
    ) -> list[tuple[LegislatieFragment, str]]:
        """Public wrapper: extract legislation references from matched chunks.

        Scans ArgumentareCritica text fields for references to legal articles
        and looks up corresponding legislatie_fragmente.

        Args:
            matched_chunks: Chunks from search.
            session: Database session.
            max_total: Maximum total fragments to return.

        Returns:
            List of (LegislatieFragment, act_name) tuples.
        """
        return await self._extract_legislation_from_chunks(
            matched_chunks, session, existing_fragments=[], max_total=max_total,
        )

    async def _rerank_chunks(
        self,
        query: str,
        chunks: list[tuple[ArgumentareCritica, float]],
        top_k: int = 10,
    ) -> list[tuple[ArgumentareCritica, float]]:
        """LLM-based reranking of retrieved chunks.

        Asks the LLM to re-order chunks by relevance to the query.
        Falls back to original order on any error.

        Args:
            query: Original user query.
            chunks: Retrieved chunks with scores.
            top_k: Number of top results to return.

        Returns:
            Re-ranked list of (ArgumentareCritica, score) tuples.
        """
        if len(chunks) <= top_k:
            return chunks

        # Build compact chunk descriptions for ranking (max 15)
        to_rank = chunks[:15]
        chunks_lines = []
        for i, (arg, _dist) in enumerate(to_rank):
            desc = arg.argumentatie_cnsc or arg.argumente_contestator or arg.elemente_retinute_cnsc or ""
            chunks_lines.append(f"[{i + 1}] {arg.cod_critica}: {desc[:200]}")

        chunks_text = "\n".join(chunks_lines)
        prompt = RERANK_PROMPT.format(query=query, chunks_text=chunks_text)

        try:
            response = await self.llm.complete(
                prompt=prompt,
                temperature=0.0,
                max_tokens=50,
            )

            # Parse order from response
            order = []
            for part in response.strip().split(','):
                part = part.strip()
                if part.isdigit():
                    idx = int(part) - 1  # Convert 1-indexed to 0-indexed
                    if 0 <= idx < len(to_rank) and idx not in order:
                        order.append(idx)

            # Build reranked list
            reranked = [to_rank[i] for i in order]

            # Add any chunks not mentioned in the ranking
            seen = set(order)
            for i, chunk in enumerate(to_rank):
                if i not in seen:
                    reranked.append(chunk)

            # Add remaining chunks beyond the ranked set
            reranked.extend(chunks[15:])

            logger.info(
                "reranking_completed",
                input_count=len(to_rank),
                reranked_order=order[:5],
            )

            return reranked[:top_k]

        except Exception as e:
            logger.warning("reranking_failed", error=str(e))
            return chunks[:top_k]

    async def _expand_query(self, query: str) -> list[str]:
        """Generează variante de căutare folosind LLM.

        Skip-ează queries simple, BO references sau queries foarte scurte.

        Args:
            query: Query-ul original al utilizatorului.

        Returns:
            Lista cu query-ul original + max 3 reformulări.
        """
        # Skip pentru queries simple sau BO references
        if len(query.split()) < 4 or self._extract_bo_references(query):
            return [query]

        try:
            response = await self.llm.complete(
                prompt=EXPANSION_PROMPT.format(query=query),
                temperature=0.3,
                max_tokens=200,
            )
            variants = [
                line.strip().lstrip("0123456789.-) ")
                for line in response.strip().split('\n')
                if line.strip() and len(line.strip()) > 5
            ]

            expanded = [query] + variants[:3]
            logger.info(
                "query_expanded",
                original=query[:80],
                variants=len(variants),
                expanded=[q[:60] for q in expanded],
            )
            return expanded

        except Exception as e:
            logger.warning("query_expansion_failed", error=str(e))
            return [query]

    def _extract_bo_references(self, query: str) -> list[tuple[int, int]]:
        """Extract BO year/number references from query.

        Matches patterns like BO2025_1011, BO2025-1011, BO2025 1011.

        Returns:
            List of (an_bo, numar_bo) tuples found in query.
        """
        pattern = r'BO(\d{4})[_\-\s](\d+)'
        matches = re.findall(pattern, query, re.IGNORECASE)
        return [(int(year), int(num)) for year, num in matches]

    async def _lookup_by_bo(
        self,
        bo_refs: list[tuple[int, int]],
        session: AsyncSession,
    ) -> list[DecizieCNSC]:
        """Directly look up decisions by BO year and number."""
        if not bo_refs:
            return []

        conditions = [
            (DecizieCNSC.an_bo == year) & (DecizieCNSC.numar_bo == num)
            for year, num in bo_refs
        ]

        stmt = select(DecizieCNSC).where(or_(*conditions))
        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        logger.info("bo_lookup_found", refs=bo_refs, count=len(decisions))
        return decisions

    def _extract_legal_references(self, query: str) -> list[str]:
        """Extract legal article references from query.

        Matches patterns like:
        - art. 57 alin. (1) din Legea 98/2016
        - art. 210
        - Hotărârea nr. 506/2023
        - HG 395/2016
        - OUG 34/2006

        Returns:
            List of reference strings found in query for text search.
        """
        patterns = [
            # Full article with law reference
            r'art(?:icolul|\.)\s*\d+(?:\s*alin(?:eat(?:ul)?)?\.?\s*\(?\d+\)?)?'
            r'(?:\s*(?:din|al)\s+(?:Legea|L\.?|HG|OUG|OG)\s*(?:nr\.?\s*)?\d+/\d+)?',
            # Standalone article
            r'art(?:icolul|\.)\s*\d+(?:\s*alin(?:eat(?:ul)?)?\.?\s*\(?\d+\)?)?',
            # Court decisions / HG references
            r'[Hh]otăr[âa]rea\s+(?:nr\.?\s*)?\d+/\d+(?:\s+din\s+\d{2}\.\d{2}\.\d{4})?',
            # Laws by number
            r'(?:Legea|L\.?)\s*(?:nr\.?\s*)?\d+/\d+',
            r'(?:HG|OUG|OG)\s*(?:nr\.?\s*)?\d+/\d+',
        ]

        references = []
        for pattern in patterns:
            matches = re.findall(pattern, query, re.IGNORECASE)
            references.extend(matches)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for ref in references:
            ref_lower = ref.lower().strip()
            if ref_lower not in seen:
                seen.add(ref_lower)
                unique.append(ref.strip())

        logger.debug("extracted_legal_references", references=unique)
        return unique

    def _parse_article_query(self, query: str) -> list[dict]:
        """Parse structured article references from query.

        Extracts article number, alineat, litera, and act info from
        references like "art. 2 alin. (3) lit. b) din HG 395/2016".

        Returns:
            List of dicts with keys: numar_articol, alineat, litera,
            tip_act, numar_act, an_act.
        """
        # Pattern: art. N [alin. (M)] [lit. X)] [din ACT N/YYYY]
        pattern = (
            r'art(?:icolul|\.)\s*(\d+)'
            r'(?:\s*alin(?:eat(?:ul)?)?\.?\s*\(?(\d+)\)?)?'
            r'(?:\s*lit(?:era)?\.?\s*([a-z])\)?)?'
            r'(?:\s*(?:din|al)\s+(?:Legea|L\.?|HG|OUG|OG)'
            r'\s*(?:nr\.?\s*)?(\d+)/(\d+))?'
        )

        matches = re.finditer(pattern, query, re.IGNORECASE)
        results = []

        for m in matches:
            ref = {
                "numar_articol": int(m.group(1)),
                "alineat": int(m.group(2)) if m.group(2) else None,
                "litera": m.group(3).lower() if m.group(3) else None,
            }

            if m.group(4) and m.group(5):
                ref["numar_act"] = int(m.group(4))
                ref["an_act"] = int(m.group(5))
                # Determine act type from the matched text
                act_text = query[m.start():m.end()].upper()
                if "LEGEA" in act_text or "L." in act_text:
                    ref["tip_act"] = "Lege"
                elif "HG" in act_text:
                    ref["tip_act"] = "HG"
                elif "OUG" in act_text:
                    ref["tip_act"] = "OUG"
                elif "OG" in act_text:
                    ref["tip_act"] = "OG"

            results.append(ref)

        logger.debug("parsed_article_query", results=results)
        return results

    async def _search_legislation_fragments(
        self,
        query: str,
        session: AsyncSession,
        limit: int = 5,
    ) -> list[tuple[LegislatieFragment, str]]:
        """Search legislatie_fragmente for relevant legal text.

        Strategy:
        1. Parse exact article references → exact DB lookup
        2. Vector search for semantic matching (fallback)

        Args:
            query: User's query.
            session: Database session.
            limit: Maximum fragments to return.

        Returns:
            List of (LegislatieFragment, act_name) tuples.
        """
        fragments: list[tuple[LegislatieFragment, str]] = []

        # 1. Exact article lookup
        parsed_refs = self._parse_article_query(query)
        for ref in parsed_refs:
            conditions = [
                LegislatieFragment.numar_articol == ref["numar_articol"]
            ]

            if ref.get("alineat") is not None:
                conditions.append(LegislatieFragment.alineat == ref["alineat"])
            if ref.get("litera") is not None:
                conditions.append(LegislatieFragment.litera == ref["litera"])

            # Filter by act if specified
            if ref.get("tip_act") and ref.get("numar_act"):
                act_stmt = select(ActNormativ.id).where(
                    and_(
                        func.upper(ActNormativ.tip_act) == ref["tip_act"].upper(),
                        ActNormativ.numar == ref["numar_act"],
                    )
                )
                if ref.get("an_act"):
                    act_stmt = act_stmt.where(ActNormativ.an == ref["an_act"])

                act_result = await session.execute(act_stmt)
                act_ids = [row[0] for row in act_result.all()]
                if act_ids:
                    conditions.append(LegislatieFragment.act_id.in_(act_ids))
                else:
                    # Act not found in DB, skip this reference
                    continue

            stmt = (
                select(LegislatieFragment)
                .where(and_(*conditions))
                .order_by(
                    LegislatieFragment.numar_articol,
                    LegislatieFragment.alineat.nulls_first(),
                    LegislatieFragment.litera.nulls_first(),
                )
                .limit(limit)
            )

            result = await session.execute(stmt)
            found = list(result.scalars().all())

            for frag in found:
                # Eagerly load act name
                if frag.act_id:
                    act_stmt = select(ActNormativ).where(ActNormativ.id == frag.act_id)
                    act_result = await session.execute(act_stmt)
                    act = act_result.scalar_one_or_none()
                    act_name = act.denumire if act else "N/A"
                else:
                    act_name = "N/A"
                fragments.append((frag, act_name))

        if fragments:
            logger.info(
                "legislation_exact_match",
                count=len(fragments),
                refs=[f.citare for f, _ in fragments],
            )
            return fragments[:limit]

        # 2. Vector search fallback on legislatie_fragmente
        try:
            query_vector = await self.embedding_service.embed_query(query)

            stmt = (
                select(
                    LegislatieFragment,
                    LegislatieFragment.embedding.cosine_distance(query_vector).label("distance"),
                )
                .where(LegislatieFragment.embedding.isnot(None))
                .order_by("distance")
                .limit(limit)
            )

            result = await session.execute(stmt)
            rows = result.all()

            # Only include fragments with reasonable similarity
            for row in rows:
                if row.distance < 0.6:
                    frag = row[0]
                    act_stmt = select(ActNormativ).where(ActNormativ.id == frag.act_id)
                    act_result = await session.execute(act_stmt)
                    act = act_result.scalar_one_or_none()
                    act_name = act.denumire if act else "N/A"
                    fragments.append((frag, act_name))

            if fragments:
                logger.info(
                    "legislation_vector_search",
                    count=len(fragments),
                    top_distance=rows[0].distance if rows else None,
                )
        except Exception as e:
            logger.warning("legislation_vector_search_failed", error=str(e))

        return fragments

    async def _extract_legislation_from_chunks(
        self,
        matched_chunks: list[tuple[ArgumentareCritica, float]],
        session: AsyncSession,
        existing_fragments: list[tuple[LegislatieFragment, str]],
        max_total: int = 8,
    ) -> list[tuple[LegislatieFragment, str]]:
        """Extrage referințe legislative din chunks și adaugă fragmente lipsă.

        Scanează textul ArgumentareCritica pentru referințe la articole de lege
        (art. X din Legea Y) și le caută în legislatie_fragmente.

        Args:
            matched_chunks: Chunks găsite de search.
            session: Database session.
            existing_fragments: Fragmente deja găsite de căutarea legislativă independentă.
            max_total: Număr maxim total de fragmente (existente + noi).

        Returns:
            Lista de fragmente noi (care nu sunt deja în existing_fragments).
        """
        if not matched_chunks:
            return []

        # Concatenăm textul relevant din chunks
        text_parts = []
        for arg, _ in matched_chunks[:10]:  # Cap la 10 chunks
            for field in [arg.argumentatie_cnsc, arg.argumente_contestator, arg.argumente_ac, arg.elemente_retinute_cnsc]:
                if field:
                    text_parts.append(field)

        if not text_parts:
            return []

        combined_text = " ".join(text_parts)

        # Parsăm referințele legislative
        parsed_refs = self._parse_article_query(combined_text)
        if not parsed_refs:
            return []

        # ID-urile fragmentelor deja existente (pentru deduplicare)
        existing_ids = {frag.id for frag, _ in existing_fragments}
        existing_citations = {(frag.act_id, frag.numar_articol, frag.alineat, frag.litera) for frag, _ in existing_fragments}

        remaining_slots = max_total - len(existing_fragments)
        if remaining_slots <= 0:
            return []

        new_fragments: list[tuple[LegislatieFragment, str]] = []

        for ref in parsed_refs:
            if len(new_fragments) >= remaining_slots:
                break

            conditions = [LegislatieFragment.numar_articol == ref["numar_articol"]]

            if ref.get("alineat") is not None:
                conditions.append(LegislatieFragment.alineat == ref["alineat"])
            if ref.get("litera") is not None:
                conditions.append(LegislatieFragment.litera == ref["litera"])

            # Filter by act if specified
            if ref.get("tip_act") and ref.get("numar_act"):
                act_stmt = select(ActNormativ.id).where(
                    and_(
                        func.upper(ActNormativ.tip_act) == ref["tip_act"].upper(),
                        ActNormativ.numar == ref["numar_act"],
                    )
                )
                if ref.get("an_act"):
                    act_stmt = act_stmt.where(ActNormativ.an == ref["an_act"])

                act_result = await session.execute(act_stmt)
                act_ids = [row[0] for row in act_result.all()]
                if act_ids:
                    conditions.append(LegislatieFragment.act_id.in_(act_ids))
                else:
                    continue

            stmt = (
                select(LegislatieFragment)
                .where(and_(*conditions))
                .order_by(
                    LegislatieFragment.numar_articol,
                    LegislatieFragment.alineat.nulls_first(),
                    LegislatieFragment.litera.nulls_first(),
                )
                .limit(3)
            )

            result = await session.execute(stmt)
            found = list(result.scalars().all())

            for frag in found:
                if frag.id in existing_ids:
                    continue
                key = (frag.act_id, frag.numar_articol, frag.alineat, frag.litera)
                if key in existing_citations:
                    continue

                # Load act name
                act_stmt = select(ActNormativ).where(ActNormativ.id == frag.act_id)
                act_result = await session.execute(act_stmt)
                act = act_result.scalar_one_or_none()
                act_name = act.denumire if act else "N/A"

                new_fragments.append((frag, act_name))
                existing_ids.add(frag.id)
                existing_citations.add(key)

                if len(new_fragments) >= remaining_slots:
                    break

        if new_fragments:
            logger.info(
                "legislation_auto_linked",
                count=len(new_fragments),
                refs=[f.citare for f, _ in new_fragments],
            )

        return new_fragments

    def _build_legislation_context(
        self,
        fragments: list[tuple[LegislatieFragment, str]],
    ) -> list[str]:
        """Build context strings from legislation fragments.

        Groups fragments by act for organized context presentation.

        Args:
            fragments: List of (LegislatieFragment, act_name) tuples.

        Returns:
            List of context strings, one per act.
        """
        if not fragments:
            return []

        # Group by act
        by_act: dict[str, list[LegislatieFragment]] = {}
        for frag, act_name in fragments:
            by_act.setdefault(act_name, []).append(frag)

        contexts = []
        for act_name, frags in by_act.items():
            parts = [
                f"=== LEGISLAȚIE: {act_name} ===",
                "",
            ]
            for frag in frags:
                parts.append(f"**{frag.citare}** din {act_name}")
                if frag.capitol:
                    parts.append(f"Capitol: {frag.capitol}")
                if frag.sectiune:
                    parts.append(f"Secțiune: {frag.sectiune}")
                parts.append(f"Text: {frag.text_fragment}")
                if frag.articol_complet and frag.articol_complet != frag.text_fragment:
                    parts.append(f"\nArticolul complet:\n{frag.articol_complet}")
                parts.append("")

            contexts.append("\n".join(parts))

        return contexts

    async def _search_by_legal_reference(
        self,
        references: list[str],
        query: str,
        session: AsyncSession,
        limit: int,
    ) -> tuple[list[DecizieCNSC], list[tuple[ArgumentareCritica, float]]]:
        """Search decisions by legal article references in text_integral.

        Searches text_integral for mentions of specific legal articles
        or court decisions.
        """
        if not references:
            return [], []

        # Search text_integral for any of the references
        conditions = []
        for ref in references:
            conditions.append(DecizieCNSC.text_integral.ilike(f"%{ref}%"))

        stmt = (
            select(DecizieCNSC)
            .where(or_(*conditions))
            .order_by(DecizieCNSC.data_decizie.desc().nulls_last())
            .limit(limit)
        )
        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        if not decisions:
            return [], []

        # Load ArgumentareCritica for matched decisions
        dec_ids = [d.id for d in decisions]
        stmt = (
            select(ArgumentareCritica)
            .where(ArgumentareCritica.decizie_id.in_(dec_ids))
            .order_by(ArgumentareCritica.ordine_in_decizie)
        )
        result = await session.execute(stmt)
        all_args = list(result.scalars().all())

        # Filter chunks that contain the reference text (prioritize relevant chunks)
        relevant_chunks = []
        other_chunks = []
        for arg in all_args:
            arg_text = " ".join(filter(None, [
                arg.argumente_contestator,
                arg.argumente_ac,
                arg.elemente_retinute_cnsc,
                arg.argumentatie_cnsc,
            ]))
            is_relevant = any(ref.lower() in arg_text.lower() for ref in references)
            if is_relevant:
                relevant_chunks.append((arg, 0.0))
            else:
                other_chunks.append((arg, 0.3))

        matched_chunks = relevant_chunks + other_chunks

        logger.info(
            "legal_reference_search_found",
            references=references,
            decisions=len(decisions),
            relevant_chunks=len(relevant_chunks),
            total_chunks=len(matched_chunks),
        )

        return decisions, matched_chunks

    async def _find_cpv_codes_for_query(
        self,
        query: str,
        session: AsyncSession,
    ) -> list[str]:
        """Find CPV codes matching domain keywords in the query.

        Searches nomenclator_cpv.descriere for keywords from the query
        to identify relevant CPV codes. This enables domain-based filtering
        (e.g., "catering" → CPV 55520000, "lucrări de instalații" → CPV 45300000).

        Args:
            query: User's search query.
            session: Database session.

        Returns:
            List of matching CPV codes (may be empty).
        """
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        # Build search conditions for nomenclator
        conditions = []
        for keyword in keywords:
            conditions.append(NomenclatorCPV.descriere.ilike(f"%{keyword}%"))

        if not conditions:
            return []

        stmt = (
            select(NomenclatorCPV.cod_cpv)
            .where(or_(*conditions))
        )
        result = await session.execute(stmt)
        cpv_codes = [row[0] for row in result.all()]

        if cpv_codes:
            logger.info(
                "cpv_codes_from_query",
                query=query[:80],
                keywords=keywords,
                cpv_count=len(cpv_codes),
            )

        return cpv_codes

    async def _search_by_cpv_domain(
        self,
        cpv_codes: list[str],
        session: AsyncSession,
        limit: int,
    ) -> tuple[list[DecizieCNSC], list[tuple[ArgumentareCritica, float]]]:
        """Search decisions by CPV codes (domain-based search).

        Args:
            cpv_codes: CPV codes to filter by.
            session: Database session.
            limit: Maximum number of decisions.

        Returns:
            Tuple of (decisions, matched_chunks).
        """
        if not cpv_codes:
            return [], []

        # Find decisions with matching CPV codes
        stmt = (
            select(DecizieCNSC)
            .where(DecizieCNSC.cod_cpv.in_(cpv_codes))
            .order_by(DecizieCNSC.data_decizie.desc().nulls_last())
            .limit(limit)
        )
        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        if not decisions:
            return [], []

        # Load ArgumentareCritica for these decisions
        dec_ids = [d.id for d in decisions]
        stmt = (
            select(ArgumentareCritica)
            .where(ArgumentareCritica.decizie_id.in_(dec_ids))
            .order_by(ArgumentareCritica.ordine_in_decizie)
        )
        result = await session.execute(stmt)
        args = list(result.scalars().all())
        matched_chunks = [(arg, 0.1) for arg in args]

        logger.info(
            "cpv_domain_search_found",
            cpv_count=len(cpv_codes),
            decisions=len(decisions),
            chunks=len(matched_chunks),
        )

        return decisions, matched_chunks

    async def search_decisions(
        self,
        query: str,
        session: AsyncSession,
        limit: int = 5,
        filters: dict | None = None,
    ) -> tuple[list[DecizieCNSC], list[tuple[ArgumentareCritica, float]]]:
        """Search for relevant decisions based on query.

        Search strategy (in order of priority):
        1. Direct BO references (e.g. BO2025_1011)
        2. Legal article/reference search (e.g. art. 57 din Legea 98/2016)
        3. CPV domain search (e.g. "catering" → find CPV codes → filter decisions)
        4. Hybrid search: Vector + Trigram merged with RRF (boosted by CPV if available)
        5. Keyword ILIKE fallback

        Args:
            query: User's search query.
            session: Database session.
            limit: Maximum number of decisions to return.
            filters: Optional filters dict for scoped search.

        Returns:
            Tuple of (decisions, matched_chunks). matched_chunks is a list of
            (ArgumentareCritica, distance) tuples; empty if using fallback.
        """
        logger.info("searching_decisions", query=query, limit=limit, has_filters=bool(filters))

        # 1. Check for direct BO references first
        bo_refs = self._extract_bo_references(query)
        if bo_refs:
            bo_decisions = await self._lookup_by_bo(bo_refs, session)
            if bo_decisions:
                # Load ArgumentareCritica for these decisions (for rich context)
                bo_ids = [d.id for d in bo_decisions]
                stmt = (
                    select(ArgumentareCritica)
                    .where(ArgumentareCritica.decizie_id.in_(bo_ids))
                    .order_by(ArgumentareCritica.ordine_in_decizie)
                )
                result = await session.execute(stmt)
                args = list(result.scalars().all())
                # Create fake distance 0 (perfect match) for context building
                matched_chunks = [(arg, 0.0) for arg in args]
                logger.info(
                    "bo_direct_lookup_success",
                    refs=bo_refs,
                    decisions=len(bo_decisions),
                    chunks=len(matched_chunks),
                )
                return bo_decisions, matched_chunks

        # 2. Check for legal article/reference queries
        legal_refs = self._extract_legal_references(query)
        if legal_refs:
            ref_decisions, ref_chunks = await self._search_by_legal_reference(
                legal_refs, query, session, limit=limit
            )
            if ref_decisions:
                return ref_decisions, ref_chunks

        # 3. Find CPV codes matching domain keywords in the query
        cpv_codes = await self._find_cpv_codes_for_query(query, session)

        # 4. Check if embeddings exist for vector search
        has_embeddings = await session.scalar(
            select(func.count())
            .select_from(ArgumentareCritica)
            .where(ArgumentareCritica.embedding.isnot(None))
        )

        if has_embeddings and has_embeddings > 0:
            # Query expansion: generate search variants in parallel with first search
            expanded_queries = await self._expand_query(query)

            # Hybrid search: vector + trigram for each expanded query, all in parallel
            all_tasks = []
            for eq in expanded_queries:
                all_tasks.append(self.search_by_vector(eq, session, limit=limit * 2, filters=filters))
                all_tasks.append(self._trigram_search(eq, session, limit=limit * 2, filters=filters))

            all_results = await asyncio.gather(*all_tasks)

            # Separate vector and trigram results
            all_vector_results = []
            all_trigram_results = []
            for i, result_list in enumerate(all_results):
                if i % 2 == 0:  # Even indices = vector
                    all_vector_results.extend(result_list)
                else:  # Odd indices = trigram
                    all_trigram_results.extend(result_list)

            # Deduplicate by chunk ID (keep best score)
            def _dedup(results: list[tuple[ArgumentareCritica, float]]) -> list[tuple[ArgumentareCritica, float]]:
                best: dict[str, tuple[ArgumentareCritica, float]] = {}
                for arg, dist in results:
                    if arg.id not in best or dist < best[arg.id][1]:
                        best[arg.id] = (arg, dist)
                return sorted(best.values(), key=lambda x: x[1])

            vector_results = _dedup(all_vector_results)
            trigram_results = _dedup(all_trigram_results)

            if vector_results or trigram_results:
                # Merge with Reciprocal Rank Fusion
                if vector_results and trigram_results:
                    matched_chunks = self._rrf_merge(vector_results, trigram_results)
                elif vector_results:
                    matched_chunks = vector_results
                else:
                    matched_chunks = trigram_results

                logger.info(
                    "hybrid_search_completed",
                    expanded_queries=len(expanded_queries),
                    vector_count=len(vector_results),
                    trigram_count=len(trigram_results),
                    merged_count=len(matched_chunks),
                )
            else:
                matched_chunks = []

            if matched_chunks:
                # If we have CPV codes, boost chunks from decisions with matching CPV
                if cpv_codes:
                    cpv_set = set(cpv_codes)
                    # Load decision CPV codes for matched chunks
                    chunk_dec_ids = list({arg.decizie_id for arg, _ in matched_chunks})
                    stmt = (
                        select(DecizieCNSC.id, DecizieCNSC.cod_cpv)
                        .where(DecizieCNSC.id.in_(chunk_dec_ids))
                    )
                    result = await session.execute(stmt)
                    dec_cpv_map = {row[0]: row[1] for row in result.all()}

                    # Re-score: reduce distance for CPV-matching decisions
                    boosted_chunks = []
                    for arg, dist in matched_chunks:
                        dec_cpv = dec_cpv_map.get(arg.decizie_id)
                        if dec_cpv and dec_cpv in cpv_set:
                            boosted_chunks.append((arg, dist * 0.5))  # Boost by halving distance
                        else:
                            boosted_chunks.append((arg, dist))
                    matched_chunks = sorted(boosted_chunks, key=lambda x: x[1])

                # Get unique decision IDs from matched chunks
                seen_ids = set()
                unique_decision_ids = []
                for arg, _dist in matched_chunks:
                    if arg.decizie_id not in seen_ids:
                        seen_ids.add(arg.decizie_id)
                        unique_decision_ids.append(arg.decizie_id)
                        if len(unique_decision_ids) >= limit:
                            break

                # Load parent decisions
                stmt = (
                    select(DecizieCNSC)
                    .where(DecizieCNSC.id.in_(unique_decision_ids))
                )
                result = await session.execute(stmt)
                decisions = list(result.scalars().all())

                # Sort decisions to match the order from vector search
                id_order = {did: i for i, did in enumerate(unique_decision_ids)}
                decisions.sort(key=lambda d: id_order.get(d.id, 999))

                logger.info(
                    "vector_search_decisions_found",
                    count=len(decisions),
                    chunks_matched=len(matched_chunks),
                    cpv_boost=bool(cpv_codes),
                )
                return decisions, matched_chunks

        # 5. CPV domain search (when no embeddings or vector search returned nothing)
        if cpv_codes:
            cpv_decisions, cpv_chunks = await self._search_by_cpv_domain(
                cpv_codes, session, limit=limit
            )
            if cpv_decisions:
                return cpv_decisions, cpv_chunks

        # 6. Fallback: keyword ILIKE search
        logger.info("falling_back_to_keyword_search", query=query)
        decisions = await self._keyword_search(query, session, limit)
        return decisions, []

    async def _keyword_search(
        self,
        query: str,
        session: AsyncSession,
        limit: int,
    ) -> list[DecizieCNSC]:
        """Fallback keyword search using ILIKE."""
        keywords = self._extract_keywords(query)

        conditions = []
        for keyword in keywords:
            keyword_pattern = f"%{keyword}%"
            conditions.append(DecizieCNSC.text_integral.ilike(keyword_pattern))
            conditions.append(DecizieCNSC.contestator.ilike(keyword_pattern))
            conditions.append(DecizieCNSC.autoritate_contractanta.ilike(keyword_pattern))
            conditions.append(DecizieCNSC.filename.ilike(keyword_pattern))
            conditions.append(DecizieCNSC.cpv_descriere.ilike(keyword_pattern))
            conditions.append(DecizieCNSC.cpv_clasa.ilike(keyword_pattern))

        if not conditions:
            stmt = (
                select(DecizieCNSC)
                .order_by(DecizieCNSC.data_decizie.desc().nulls_last())
                .limit(limit)
            )
        else:
            stmt = (
                select(DecizieCNSC)
                .where(or_(*conditions))
                .order_by(DecizieCNSC.data_decizie.desc().nulls_last())
                .limit(limit)
            )

        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        logger.info("keyword_search_decisions_found", count=len(decisions))
        return decisions

    def _extract_keywords(self, query: str) -> list[str]:
        """Extract meaningful keywords from query."""
        stop_words = {
            'ce', 'sunt', 'este', 'cum', 'care', 'din', 'la', 'în', 'și', 'sau',
            'pentru', 'cu', 'despre', 'pe', 'de', 'a', 'ai', 'am', 'ma', 'mi',
            'le', 'îmi', 'îți', 'și-a', 'dat', 'dau', 'da', 'spune', 'spune-mi',
            'decizii', 'decizie', 'cnsc', 'avem', 'baza', 'date'
        }

        words = query.lower().split()
        keywords = [
            word.strip('.,?!;:')
            for word in words
            if len(word) >= 3 and word.lower() not in stop_words
        ]

        logger.debug("extracted_keywords", keywords=keywords)
        return keywords

    def _extract_verbatim_excerpt(
        self,
        text_integral: str,
        chunks: list[tuple[ArgumentareCritica, float]],
        max_chars: int = 4000,
    ) -> str:
        """Extract the most relevant verbatim excerpt from text_integral.

        Searches for key phrases from matched ArgumentareCritica in the
        original decision text and returns a window around the best match.
        Falls back to the last portion of the text (which typically contains
        CNSC's reasoning and ruling).

        Args:
            text_integral: Full decision text.
            chunks: Matched ArgumentareCritica chunks with distances.
            max_chars: Maximum characters to extract.

        Returns:
            Verbatim excerpt from text_integral.
        """
        if len(text_integral) <= max_chars:
            return text_integral

        # Build search phrases from the most relevant chunk's CNSC argumentation
        # (CNSC reasoning is the most valuable for verbatim quoting)
        best_pos = -1
        for arg, _dist in sorted(chunks, key=lambda x: x[1]):
            # Try to find CNSC argumentation text in original
            search_texts = [
                arg.argumentatie_cnsc,
                arg.elemente_retinute_cnsc,
                arg.argumente_contestator,
            ]
            for search_text in search_texts:
                if not search_text:
                    continue
                # Use first ~80 chars as search key (enough to be unique)
                search_key = search_text[:80].strip()
                pos = text_integral.lower().find(search_key.lower())
                if pos >= 0:
                    best_pos = pos
                    break
            if best_pos >= 0:
                break

        if best_pos >= 0:
            # Center the window around the found position
            half_window = max_chars // 2
            start = max(0, best_pos - half_window)
            end = min(len(text_integral), start + max_chars)
            # Adjust start if we hit the end
            if end == len(text_integral):
                start = max(0, end - max_chars)
        else:
            # Fallback: take the last portion (CNSC reasoning is typically at the end)
            start = max(0, len(text_integral) - max_chars)
            end = len(text_integral)

        excerpt = text_integral[start:end]

        # Add ellipsis indicators
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(text_integral) else ""

        return f"{prefix}{excerpt}{suffix}"

    def _build_context(
        self,
        decisions: list[DecizieCNSC],
        matched_chunks: list[tuple[ArgumentareCritica, float]],
    ) -> list[str]:
        """Build context strings from decisions and matched chunks for LLM.

        When vector search was used, builds rich context from the matched
        ArgumentareCritica chunks (structured analysis) plus verbatim excerpts
        from text_integral for direct quoting. Falls back to truncated
        text_integral when no chunks are available.
        """
        contexts = []

        if matched_chunks:
            # Group chunks by decision_id for organized context
            decision_map = {d.id: d for d in decisions}
            chunks_by_decision: dict[str, list[tuple[ArgumentareCritica, float]]] = {}
            for arg, dist in matched_chunks:
                chunks_by_decision.setdefault(arg.decizie_id, []).append((arg, dist))

            # Budget: structured data + verbatim excerpts
            # ~4000 chars per decision for verbatim text
            verbatim_budget = max(2000, 20000 // max(len(chunks_by_decision), 1))

            for decizie_id, chunks in chunks_by_decision.items():
                dec = decision_map.get(decizie_id)
                if not dec:
                    continue

                cpv_info = dec.cod_cpv or 'N/A'
                if dec.cpv_descriere:
                    cpv_info += f" — {dec.cpv_descriere}"
                if dec.cpv_categorie:
                    cpv_info += f" ({dec.cpv_categorie})"

                context_parts = [
                    f"=== Decizia {dec.external_id} ===",
                    f"Număr decizie: {dec.numar_decizie or 'N/A'}",
                    f"Dată: {dec.data_decizie.strftime('%d.%m.%Y') if dec.data_decizie else 'N/A'}",
                    f"Complet: {dec.complet or 'N/A'}",
                    f"Tip contestație: {dec.tip_contestatie}",
                    f"Coduri critici: {', '.join(dec.coduri_critici) if dec.coduri_critici else 'N/A'}",
                    f"CPV: {cpv_info}",
                    f"Soluție: {dec.solutie_contestatie or 'N/A'}",
                    f"Contestator: {dec.contestator or 'N/A'}",
                    f"Autoritate contractantă: {dec.autoritate_contractanta or 'N/A'}",
                    "",
                    "## Analiză structurată per critică:",
                    "",
                ]

                for arg, dist in chunks:
                    similarity = 1.0 - dist
                    context_parts.append(
                        f"--- Critica {arg.cod_critica} (relevanță: {similarity:.2f}) ---"
                    )
                    if arg.argumente_contestator:
                        context_parts.append(f"Argumente contestator: {arg.argumente_contestator}")
                    if arg.jurisprudenta_contestator:
                        context_parts.append(f"Jurisprudență invocată de contestator: {'; '.join(arg.jurisprudenta_contestator)}")
                    if arg.argumente_ac:
                        context_parts.append(f"Argumente AC: {arg.argumente_ac}")
                    if arg.jurisprudenta_ac:
                        context_parts.append(f"Jurisprudență invocată de AC: {'; '.join(arg.jurisprudenta_ac)}")
                    if arg.argumente_intervenienti:
                        for interv in arg.argumente_intervenienti:
                            nr = interv.get("nr", "?")
                            context_parts.append(f"Argumente intervenient #{nr}: {interv.get('argumente', 'N/A')}")
                            jp = interv.get("jurisprudenta", [])
                            if jp:
                                context_parts.append(f"Jurisprudență intervenient #{nr}: {'; '.join(jp)}")
                    if arg.elemente_retinute_cnsc:
                        context_parts.append(f"Elemente reținute CNSC: {arg.elemente_retinute_cnsc}")
                    if arg.argumentatie_cnsc:
                        context_parts.append(f"Argumentație CNSC: {arg.argumentatie_cnsc}")
                    if arg.jurisprudenta_cnsc:
                        context_parts.append(f"Jurisprudență invocată de CNSC: {'; '.join(arg.jurisprudenta_cnsc)}")
                    if arg.castigator_critica and arg.castigator_critica != "unknown":
                        context_parts.append(f"Câștigător: {arg.castigator_critica}")
                    context_parts.append("")

                # Add verbatim excerpt from text_integral for direct quoting
                verbatim = self._extract_verbatim_excerpt(
                    dec.text_integral, chunks, max_chars=verbatim_budget
                )
                context_parts.extend([
                    "## Text original din decizie (pentru citare verbatim):",
                    "",
                    verbatim,
                    "",
                ])

                contexts.append("\n".join(context_parts))
        else:
            # Fallback: use text_integral (up to 15000 chars per decision,
            # reduced proportionally when multiple decisions to stay within
            # LLM context limits)
            max_chars_per_decision = max(4000, 60000 // max(len(decisions), 1))
            for dec in decisions:
                text = dec.text_integral
                truncated = len(text) > max_chars_per_decision
                cpv_info = dec.cod_cpv or 'N/A'
                if dec.cpv_descriere:
                    cpv_info += f" — {dec.cpv_descriere}"
                if dec.cpv_categorie:
                    cpv_info += f" ({dec.cpv_categorie})"
                context_parts = [
                    f"=== Decizia {dec.external_id} ===",
                    f"Număr decizie: {dec.numar_decizie or 'N/A'}",
                    f"Dată: {dec.data_decizie.strftime('%d.%m.%Y') if dec.data_decizie else 'N/A'}",
                    f"Complet: {dec.complet or 'N/A'}",
                    f"Tip contestație: {dec.tip_contestatie}",
                    f"Coduri critici: {', '.join(dec.coduri_critici) if dec.coduri_critici else 'N/A'}",
                    f"CPV: {cpv_info}",
                    f"Soluție: {dec.solutie_contestatie or 'N/A'}",
                    f"Contestator: {dec.contestator or 'N/A'}",
                    f"Autoritate contractantă: {dec.autoritate_contractanta or 'N/A'}",
                    "",
                    "Text integral:" if not truncated else "Text integral (fragment):",
                    text[:max_chars_per_decision] + ("..." if truncated else ""),
                ]
                contexts.append("\n".join(context_parts))

        return contexts

    def _extract_citations(
        self,
        response: str,
        decisions: list[DecizieCNSC],
        matched_chunks: list[tuple[ArgumentareCritica, float]],
    ) -> list[Citation]:
        """Extract and verify citations from LLM response."""
        citations = []

        for dec in decisions:
            decision_ref = dec.external_id

            if decision_ref in response or str(dec.numar_decizie or '') in response:
                # Use matched chunk text if available, otherwise fallback
                citation_text = ""
                if matched_chunks:
                    for arg, _dist in matched_chunks:
                        if arg.decizie_id == dec.id and arg.argumentatie_cnsc:
                            citation_text = arg.argumentatie_cnsc[:300] + "..."
                            break

                if not citation_text:
                    citation_text = dec.text_integral[:200] + "..."

                citations.append(Citation(
                    decision_id=dec.external_id,
                    text=citation_text,
                    verified=True,
                ))

        return citations

    def _calculate_confidence(
        self,
        decisions: list[DecizieCNSC],
        matched_chunks: list[tuple[ArgumentareCritica, float]],
        max_decisions: int,
    ) -> float:
        """Calculate confidence score based on search results.

        Uses cosine similarity scores when available (vector search),
        falls back to a simple count ratio otherwise.
        """
        if not decisions:
            return 0.0

        if matched_chunks:
            # Use average similarity of top chunks (1 - distance)
            top_similarities = [1.0 - dist for _, dist in matched_chunks[:5]]
            avg_similarity = sum(top_similarities) / len(top_similarities)
            # Scale: similarity > 0.8 → high confidence, < 0.5 → low
            return min(1.0, max(0.0, avg_similarity))

        # Fallback: count-based confidence
        return min(1.0, len(decisions) / max_decisions)

    def _generate_suggested_questions(
        self,
        decisions: list[DecizieCNSC],
    ) -> list[str]:
        """Generate contextual follow-up questions based on decisions found."""
        suggestions = []

        all_critici = set()
        solutions = set()

        for dec in decisions:
            if dec.coduri_critici:
                all_critici.update(dec.coduri_critici)
            if dec.solutie_contestatie:
                solutions.add(dec.solutie_contestatie)

        if all_critici:
            critica_list = ', '.join(sorted(all_critici)[:3])
            suggestions.append(f"Ce jurisprudență există pentru criticile {critica_list}?")

        if 'ADMIS' in solutions or 'ADMIS_PARTIAL' in solutions:
            suggestions.append("Care sunt argumentele care au dus la admiterea contestației?")

        if 'RESPINS' in solutions:
            suggestions.append("De ce au fost respinse aceste contestații?")

        suggestions.append("Arată-mi decizii similare")

        return suggestions[:4]

    async def prepare_context(
        self,
        query: str,
        session: AsyncSession,
        conversation_history: list[dict] | None = None,
        max_decisions: int = 5,
        rerank: bool = False,
        filters: dict | None = None,
    ) -> tuple[list[str], str, list[Citation], float, list[str]]:
        """Prepare RAG context without generating the LLM response.

        Args:
            query: User's question.
            session: Database session.
            conversation_history: Previous conversation.
            max_decisions: Max decisions to retrieve.
            rerank: Whether to apply LLM-based reranking.
            filters: Optional scope filters for search.

        Returns:
            Tuple of (contexts, system_prompt, citations, confidence, suggested_questions).
            Returns None for contexts/system_prompt if no results found.
        """
        legislation_fragments = await self._search_legislation_fragments(
            query, session, limit=5
        )
        decisions, matched_chunks = await self.search_decisions(
            query, session, limit=max_decisions, filters=filters
        )

        # Apply reranking if requested
        if rerank and matched_chunks:
            matched_chunks = await self._rerank_chunks(query, matched_chunks, top_k=max_decisions * 3)

        if not decisions and not legislation_fragments:
            return None, None, [], 0.0, ["Ce decizii CNSC sunt disponibile?", "Arată-mi toate deciziile"]

        # Auto-link legislation from matched chunks
        if matched_chunks:
            auto_legislation = await self._extract_legislation_from_chunks(
                matched_chunks, session, legislation_fragments
            )
            if auto_legislation:
                legislation_fragments = list(legislation_fragments) + auto_legislation

        contexts = []
        if legislation_fragments:
            contexts.extend(self._build_legislation_context(legislation_fragments))
        if decisions:
            contexts.extend(self._build_context(decisions, matched_chunks))

        system_prompt = self._build_system_prompt(
            bool(legislation_fragments), bool(decisions)
        )
        citations = self._extract_citations("", decisions, matched_chunks)
        confidence = self._calculate_confidence(decisions, matched_chunks, max_decisions)
        if legislation_fragments and not decisions:
            confidence = max(confidence, 0.9)
        elif legislation_fragments:
            confidence = min(1.0, confidence + 0.1)
        suggested = self._generate_suggested_questions(decisions)

        return contexts, system_prompt, citations, confidence, suggested

    def _build_system_prompt(self, has_legislation: bool, has_decisions: bool) -> str:
        """Build the system prompt based on available context types."""
        system_prompt = """Ești un consultant senior în achiziții publice specializat în legislația și jurisprudența CNSC (Consiliul Național de Soluționare a Contestațiilor) din România.

Sarcina ta este să răspunzi la întrebări folosind EXCLUSIV informațiile din contextul furnizat mai jos."""

        if has_legislation and has_decisions:
            system_prompt += """

Contextul conține atât TEXTE LEGISLATIVE (articole din Legea 98/2016, HG 395/2016, etc.) cât și DECIZII CNSC. Folosește-le pe ambele:
- Citează textul exact al articolelor de lege când sunt disponibile
- Citează deciziile CNSC care aplică sau interpretează acele articole"""
        elif has_legislation:
            system_prompt += """

Contextul conține TEXTE LEGISLATIVE (articole din Legea 98/2016, HG 395/2016, etc.).
Citează textul exact al articolelor, inclusiv alineatele și literele relevante."""
        else:
            system_prompt += """

Contextul conține DECIZII CNSC relevante pentru întrebare."""

        system_prompt += """

Reguli importante:
1. Bazează-te DOAR pe informațiile din contextul furnizat
2. Citează sursele specifice: articole de lege cu citare completă (ex: "**art. 2 alin. (3) lit. b) din HG 395/2016**") și/sau decizii CNSC (ex: "Conform deciziei **BO2025_123**...")
3. Dacă informația nu este în context, spune clar că nu ai suficiente date
4. Oferă răspunsuri clare, structurate și profesionale
5. Folosește terminologie juridică corectă specifică achizițiilor publice
6. Când discuți despre soluții, menționează argumentele CNSC
7. Când în context există referințe la jurisprudență (decizii ale instanțelor naționale, CJUE, directive europene), citează-le exact așa cum apar
8. NU te prezenta și NU folosi formulări de genul "În calitate de..." - răspunde direct la întrebare
9. **CITĂRI VERBATIM**: Când susții un argument sau prezinți o concluzie, include citate exacte din textul original, folosind ghilimele. Exemplu: *Conform **art. 2 alin. (3)** din HG 395/2016, „textul exact al articolului"*
10. Prezintă atât argumentele contestatorului, cât și cele ale autorității contractante și ale CNSC, cu citate verbatim din fiecare parte, pentru a oferi o imagine completă.

Formatare:
- Folosește **bold** pentru termeni cheie, referințe la articole de lege și la decizii
- Folosește „ghilimele românești" pentru citatele verbatim
- Structurează răspunsul cu paragrafe clare, separate prin linii goale
- Folosește liste numerotate (1. 2. 3.) pentru enumerări
- Folosește titluri (## sau ###) pentru secțiuni distincte când răspunsul este lung
- Fiecare argument sau decizie trebuie să fie pe un paragraf separat

IMPORTANT pentru răspunsuri lungi:
- Fii CONCIS și SUBSTANȚIAL — nu repeta aceleași idei în formulări diferite
- Fiecare paragraf trebuie să aducă informație nouă
- Preferă citatele exacte din decizii în loc de parafraze vagi
- Când sunt multe decizii relevante, grupează-le tematic, nu le enumera individual cu texte repetitive
- Evită formulări generice de tipul „conform jurisprudenței constante" — citează decizii concrete

Răspunde în limba română, profesional și precis."""
        return system_prompt

    async def generate_response(
        self,
        query: str,
        session: AsyncSession,
        conversation_history: list[dict] | None = None,
        max_decisions: int = 5,
        rerank: bool = False,
        filters: dict | None = None,
    ) -> tuple[str, list[Citation], float, list[str]]:
        """Generate a response to user query using RAG.

        Args:
            query: User's question.
            session: Database session.
            conversation_history: Optional previous conversation messages.
            max_decisions: Maximum number of decisions to retrieve.
            rerank: Whether to apply LLM-based reranking.
            filters: Optional scope filters for search.

        Returns:
            Tuple of (response_text, citations, confidence, suggested_questions).
        """
        logger.info("generating_rag_response", query=query)

        contexts, system_prompt, citations, confidence, suggested_questions = await self.prepare_context(
            query, session, conversation_history, max_decisions, rerank=rerank, filters=filters
        )

        if contexts is None:
            return (
                "Nu am găsit informații relevante pentru această întrebare. "
                "Încearcă să reformulezi întrebarea sau să folosești termeni mai specifici.",
                [],
                0.0,
                suggested_questions,
            )

        try:
            response_text = await self.llm.complete(
                prompt=query,
                context=contexts,
                system_prompt=system_prompt,
                temperature=0.1,
                max_tokens=12288,
            )

            logger.info(
                "rag_response_generated",
                citations=len(citations),
                confidence=confidence,
            )

            return response_text, citations, confidence, suggested_questions

        except Exception as e:
            logger.error("rag_generation_error", error=str(e))
            raise
