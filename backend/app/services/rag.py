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

import re
from typing import Optional
from sqlalchemy import select, or_, func, and_
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
        scope_decision_ids: list[str] | None = None,
        query_vector: list[float] | None = None,
    ) -> list[tuple[ArgumentareCritica, float]]:
        """Search ArgumentareCritica by vector cosine similarity.

        Args:
            query: User's search query.
            session: Database session.
            limit: Maximum number of results.
            scope_decision_ids: If provided, restrict search to these decision IDs only.
            query_vector: Pre-computed embedding vector. If None, will embed the query.

        Returns:
            List of (ArgumentareCritica, distance) tuples ordered by similarity.
        """
        # Use pre-computed vector or embed the query
        if query_vector is None:
            query_vector = await self.embedding_service.embed_query(query)

        # Cosine distance search on ArgumentareCritica embeddings
        stmt = (
            select(
                ArgumentareCritica,
                ArgumentareCritica.embedding.cosine_distance(query_vector).label("distance"),
            )
            .where(ArgumentareCritica.embedding.isnot(None))
            .order_by("distance")
            .limit(limit)
        )

        # Scope pre-filter: restrict to specific decision IDs
        if scope_decision_ids is not None:
            stmt = stmt.where(ArgumentareCritica.decizie_id.in_(scope_decision_ids))

        result = await session.execute(stmt)
        rows = result.all()

        logger.info(
            "vector_search_completed",
            query=query[:80],
            results=len(rows),
            top_distance=rows[0].distance if rows else None,
            scoped=scope_decision_ids is not None,
        )

        return [(row.ArgumentareCritica, row.distance) for row in rows]

    async def _trigram_search(
        self,
        query: str,
        session: AsyncSession,
        limit: int = 15,
        scope_decision_ids: list[str] | None = None,
    ) -> list[tuple[ArgumentareCritica, float]]:
        """Search ArgumentareCritica via pg_trgm word similarity on CPV description fields.

        Complements vector search by catching exact term matches that
        semantic search might miss (legal terminology, entity names, CPV terms).

        Uses cpv_descriere + cpv_clasa instead of text_integral for performance:
        text_integral is 78MB+ across all decisions and word_similarity on it
        causes 10-30s scans. CPV fields are small and relevant for domain matching.

        Only called when scope_decision_ids is set (scoped search).
        For global (unscoped) search, vector search alone is sufficient.

        Args:
            query: User's search query.
            session: Database session.
            limit: Maximum number of chunk results.
            scope_decision_ids: If provided, restrict search to these decision IDs only.

        Returns:
            List of (ArgumentareCritica, distance) tuples.
        """
        try:
            # Use cpv_descriere for trigram matching (small field, CPV-relevant)
            # Coalesce to handle NULLs, combine cpv_descriere + cpv_clasa
            combined_cpv = func.coalesce(DecizieCNSC.cpv_descriere, "") + " " + func.coalesce(DecizieCNSC.cpv_clasa, "")
            wsim = func.word_similarity(query, combined_cpv)

            stmt = (
                select(DecizieCNSC.id, wsim.label("wsim"))
                .where(wsim > 0.3)
                .order_by(wsim.desc())
                .limit(limit)
            )

            if scope_decision_ids is not None:
                stmt = stmt.where(DecizieCNSC.id.in_(scope_decision_ids))

            result = await session.execute(stmt)
            dec_rows = result.all()

            if not dec_rows:
                logger.info("trigram_search_no_results", query=query[:80])
                return []

            # Load ArgumentareCritica for matched decisions
            dec_ids = [row[0] for row in dec_rows]
            dec_scores = {str(row[0]): float(row[1]) for row in dec_rows}

            chunk_stmt = (
                select(ArgumentareCritica)
                .where(ArgumentareCritica.decizie_id.in_(dec_ids))
                .order_by(ArgumentareCritica.ordine_in_decizie)
            )
            chunk_result = await session.execute(chunk_stmt)
            chunks = list(chunk_result.scalars().all())

            # Convert word_similarity (0-1, higher=better) to distance (0-1, lower=better)
            results = [
                (chunk, 1.0 - dec_scores.get(str(chunk.decizie_id), 0.0))
                for chunk in chunks
            ]

            logger.info(
                "trigram_search_completed",
                query=query[:80],
                decisions=len(dec_rows),
                chunks=len(results),
                top_wsim=float(dec_rows[0][1]) if dec_rows else None,
            )

            return results
        except Exception as e:
            logger.warning("trigram_search_failed", error=str(e))
            return []

    def _rrf_merge(
        self,
        vector_results: list[tuple[ArgumentareCritica, float]],
        trigram_results: list[tuple[ArgumentareCritica, float]],
        k_vector: int = 60,
        k_trigram: int = 70,
    ) -> list[tuple[ArgumentareCritica, float]]:
        """Merge vector and trigram search results using Reciprocal Rank Fusion.

        RRF normalizes scores from different search methods without
        requiring calibration. k_vector < k_trigram gives slight preference
        to vector (semantic) results.

        Args:
            vector_results: Results from vector cosine search.
            trigram_results: Results from trigram word similarity search.
            k_vector: RRF constant for vector results (lower = more weight).
            k_trigram: RRF constant for trigram results.

        Returns:
            Merged list of (ArgumentareCritica, distance) tuples ordered by RRF score.
        """
        rrf_scores: dict[str, float] = {}
        chunk_map: dict[str, ArgumentareCritica] = {}
        best_dist: dict[str, float] = {}

        for rank, (chunk, dist) in enumerate(vector_results):
            cid = str(chunk.id)
            rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (k_vector + rank + 1)
            chunk_map[cid] = chunk
            best_dist[cid] = dist

        for rank, (chunk, dist) in enumerate(trigram_results):
            cid = str(chunk.id)
            rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (k_trigram + rank + 1)
            if cid not in chunk_map:
                chunk_map[cid] = chunk
            best_dist[cid] = min(best_dist.get(cid, 1.0), dist)

        sorted_ids = sorted(rrf_scores.keys(), key=lambda c: rrf_scores[c], reverse=True)

        vector_ids = {str(c.id) for c, _ in vector_results}
        trigram_ids = {str(c.id) for c, _ in trigram_results}

        logger.info(
            "rrf_merge",
            vector_count=len(vector_results),
            trigram_count=len(trigram_results),
            merged_count=len(sorted_ids),
            overlap=len(vector_ids & trigram_ids),
        )

        return [(chunk_map[cid], best_dist[cid]) for cid in sorted_ids]

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

    async def _batch_load_act_names(
        self,
        act_ids: list,
        session: AsyncSession,
    ) -> dict[str, str]:
        """Batch load ActNormativ names for a list of act IDs.

        Avoids N+1 queries by loading all needed acts in a single query.

        Returns:
            Dict mapping str(act_id) → denumire.
        """
        if not act_ids:
            return {}
        unique_ids = list(set(str(aid) for aid in act_ids if aid))
        if not unique_ids:
            return {}
        result = await session.execute(
            select(ActNormativ).where(ActNormativ.id.in_(unique_ids))
        )
        return {str(act.id): act.denumire for act in result.scalars().all()}

    async def _search_legislation_fragments(
        self,
        query: str,
        session: AsyncSession,
        limit: int = 5,
        query_vector: list[float] | None = None,
    ) -> list[tuple[LegislatieFragment, str]]:
        """Search legislatie_fragmente for relevant legal text.

        Strategy:
        1. Parse exact article references → exact DB lookup
        2. Vector search for semantic matching (fallback)

        Args:
            query: User's query.
            session: Database session.
            limit: Maximum fragments to return.
            query_vector: Pre-computed embedding vector. If None, will embed the query.

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
                fragments.append(frag)

        if fragments:
            # Batch load act names (avoid N+1 queries)
            act_map = await self._batch_load_act_names(
                [f.act_id for f in fragments if f.act_id], session
            )
            named_fragments = [
                (f, act_map.get(str(f.act_id), "N/A")) for f in fragments
            ]
            logger.info(
                "legislation_exact_match",
                count=len(named_fragments),
                refs=[f.citare for f, _ in named_fragments],
            )
            return named_fragments[:limit]

        # 2. Vector search fallback on legislatie_fragmente
        try:
            # Use pre-computed vector or embed the query
            if query_vector is None:
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

            # Collect fragments with reasonable similarity, then batch-load act names
            vector_frags = [row[0] for row in rows if row.distance < 0.6]
            if vector_frags:
                act_map = await self._batch_load_act_names(
                    [f.act_id for f in vector_frags if f.act_id], session
                )
                for frag in vector_frags:
                    act_name = act_map.get(str(frag.act_id), "N/A")
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

    async def _find_legislation_for_chunks(
        self,
        matched_chunks: list[tuple[ArgumentareCritica, float]],
        session: AsyncSession,
        already_found_ids: set[str] | None = None,
        limit: int = 8,
    ) -> list[tuple[LegislatieFragment, str]]:
        """Find legislation fragments referenced by or semantically related to matched chunks.

        Enriches RAG context by providing the actual legal text when a decision
        references "art. 2 alin. (2) lit. e) din Legea 98" without explaining
        what it says, or when it paraphrases a provision without citing
        the specific article (e.g., anonymized references like "art. ... alin. (xx)").

        Two strategies:
        1. Explicit: Parse legal references from chunk text → exact DB lookup
        2. Semantic: Vector search using CNSC argumentation text → find paraphrased provisions

        Args:
            matched_chunks: ArgumentareCritica chunks found by vector search.
            session: Database session.
            already_found_ids: Fragment IDs already in context (to avoid duplicates).
            limit: Maximum fragments to return.

        Returns:
            List of (LegislatieFragment, act_name) tuples.
        """
        if not matched_chunks:
            return []

        already_found_ids = already_found_ids or set()
        fragments: list[tuple[LegislatieFragment, str]] = []
        found_ids: set[str] = set(already_found_ids)

        # Collect text from chunks for reference extraction
        all_chunk_texts: list[str] = []
        semantic_parts: list[str] = []

        for arg, dist in matched_chunks[:10]:
            for text in [
                arg.argumentatie_cnsc,
                arg.argumente_contestator,
                arg.argumente_ac,
                arg.elemente_retinute_cnsc,
            ]:
                if text:
                    all_chunk_texts.append(text)
            # For semantic search, use CNSC argumentation from high-relevance chunks
            if arg.argumentatie_cnsc and dist < 0.5:
                semantic_parts.append(arg.argumentatie_cnsc[:500])

        combined_text = "\n".join(all_chunk_texts)

        # --- Strategy 1: Explicit references WITH act specification ---
        # Only process refs that include the law name (e.g., "art. 57 din Legea 98/2016")
        # Standalone "art. 57" is too ambiguous from chunk text
        parsed_refs = self._parse_article_query(combined_text)
        qualified_refs = [
            r for r in parsed_refs
            if r.get("tip_act") and r.get("numar_act")
        ]

        # Deduplicate parsed references
        seen_refs: set[tuple] = set()
        unique_refs: list[dict] = []
        for ref in qualified_refs:
            key = (
                ref["numar_articol"],
                ref.get("alineat"),
                ref.get("litera"),
                ref.get("numar_act"),
                ref.get("an_act"),
            )
            if key not in seen_refs:
                seen_refs.add(key)
                unique_refs.append(ref)

        # Pre-load act IDs in batch (avoid N+1)
        act_cache: dict[tuple, dict[str, str]] = {}  # cache_key -> {act_id: act_name}
        for ref in unique_refs:
            cache_key = (ref["tip_act"].upper(), ref["numar_act"], ref.get("an_act"))
            if cache_key not in act_cache:
                act_stmt = select(ActNormativ).where(
                    and_(
                        func.upper(ActNormativ.tip_act) == cache_key[0],
                        ActNormativ.numar == cache_key[1],
                    )
                )
                if cache_key[2]:
                    act_stmt = act_stmt.where(ActNormativ.an == cache_key[2])
                result = await session.execute(act_stmt)
                act_cache[cache_key] = {str(act.id): act.denumire for act in result.scalars().all()}

        # Look up fragments for each qualified reference
        for ref in unique_refs[:20]:
            if len(fragments) >= limit:
                break

            cache_key = (ref["tip_act"].upper(), ref["numar_act"], ref.get("an_act"))
            act_map = act_cache.get(cache_key, {})
            if not act_map:
                continue

            conditions = [
                LegislatieFragment.numar_articol == ref["numar_articol"],
                LegislatieFragment.act_id.in_(list(act_map.keys())),
            ]
            if ref.get("alineat") is not None:
                conditions.append(LegislatieFragment.alineat == ref["alineat"])
            if ref.get("litera") is not None:
                conditions.append(LegislatieFragment.litera == ref["litera"])

            stmt = select(LegislatieFragment).where(and_(*conditions)).limit(3)
            result = await session.execute(stmt)
            for frag in result.scalars().all():
                if frag.id not in found_ids:
                    found_ids.add(frag.id)
                    act_name = act_map.get(str(frag.act_id), "N/A")
                    fragments.append((frag, act_name))

        # Strategy 2 (semantic embedding) was removed for performance:
        # It required an additional Gemini API call (30-60s) with marginal benefit.
        # Explicit reference parsing (Strategy 1) catches the important cases.

        if fragments:
            logger.info(
                "legislation_linking_complete",
                explicit=len(fragments),
                total=len(fragments),
            )

        return fragments

    async def _find_cpv_codes_for_query(
        self,
        query: str,
        session: AsyncSession,
        query_vector: list[float] | None = None,
    ) -> list[str]:
        """Find CPV codes matching the query via vector search + keyword fallback.

        Strategy 1 (primary): Semantic vector search on nomenclator_cpv.embedding
        using the pre-computed query_vector. Zero additional API calls.

        Strategy 2 (fallback): ILIKE keyword search across descriere,
        categorie_achizitii, clasa_produse.

        Args:
            query: User's search query.
            session: Database session.
            query_vector: Pre-computed embedding vector for semantic search.

        Returns:
            List of matching CPV codes (may be empty).
        """
        CPV_DISTANCE_THRESHOLD = 0.5  # cosine distance < 0.5 = similarity > 0.5

        # Strategy 1: Vector search on CPV embeddings (semantic)
        if query_vector is not None:
            stmt = (
                select(
                    NomenclatorCPV.cod_cpv,
                    NomenclatorCPV.nivel,
                    NomenclatorCPV.embedding.cosine_distance(query_vector).label("distance"),
                )
                .where(NomenclatorCPV.embedding.isnot(None))
                .order_by("distance")
                .limit(20)
            )
            result = await session.execute(stmt)
            rows = result.all()

            # Filter by distance threshold
            matching = [r for r in rows if r.distance < CPV_DISTANCE_THRESHOLD]

            if matching:
                cpv_codes = self._deduplicate_cpv_codes(
                    [(r.cod_cpv, r.nivel) for r in matching]
                )
                if cpv_codes:
                    logger.info(
                        "cpv_vector_search",
                        query=query[:80],
                        cpv_count=len(cpv_codes),
                        top_distance=round(matching[0].distance, 3),
                    )
                    return cpv_codes

        # Strategy 2: Keyword ILIKE fallback
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        conditions = []
        for keyword in keywords:
            conditions.append(or_(
                NomenclatorCPV.descriere.ilike(f"%{keyword}%"),
                NomenclatorCPV.categorie_achizitii.ilike(f"%{keyword}%"),
                NomenclatorCPV.clasa_produse.ilike(f"%{keyword}%"),
            ))

        if not conditions:
            return []

        stmt = (
            select(NomenclatorCPV.cod_cpv, NomenclatorCPV.nivel)
            .where(or_(*conditions))
            .order_by(NomenclatorCPV.nivel.asc().nulls_last())
            .limit(50)
        )
        result = await session.execute(stmt)
        rows = result.all()

        cpv_codes = self._deduplicate_cpv_codes(rows)

        if cpv_codes:
            logger.info(
                "cpv_keyword_search",
                query=query[:80],
                keywords=keywords,
                cpv_count=len(cpv_codes),
            )

        return cpv_codes

    @staticmethod
    def _deduplicate_cpv_codes(rows: list[tuple]) -> list[str]:
        """Deduplicate CPV codes: if we have a parent code, skip its children.

        Args:
            rows: List of (cod_cpv, nivel) tuples.

        Returns:
            Deduplicated list of CPV codes.
        """
        cpv_codes = []
        seen_prefixes: set[str] = set()
        for row in rows:
            cod = row[0]
            prefix = cod.split("-")[0] if cod else cod
            is_child = any(prefix.startswith(sp) for sp in seen_prefixes)
            if not is_child:
                cpv_codes.append(cod)
                seen_prefixes.add(prefix.rstrip("0") if prefix else "")
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

        # Find decisions with matching CPV codes (prefix match for hierarchy)
        cpv_conditions = [DecizieCNSC.cod_cpv.ilike(f"{cpv}%") for cpv in cpv_codes]
        stmt = (
            select(DecizieCNSC)
            .where(or_(*cpv_conditions))
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

    async def _rerank_chunks(
        self,
        query: str,
        chunks: list[tuple[ArgumentareCritica, float]],
        top_k: int = 10,
    ) -> list[tuple[ArgumentareCritica, float]]:
        """LLM-based reranking of retrieved chunks.

        Asks the LLM to reorder chunks by relevance to the query.
        Falls back to original order on any error.

        Args:
            query: Original user query.
            chunks: Retrieved chunks with distances.
            top_k: Number of top chunks to return.

        Returns:
            Reranked list of (ArgumentareCritica, distance) tuples.
        """
        if len(chunks) <= top_k:
            return chunks

        # Build compact prompt with top 15 chunks
        candidates = chunks[:15]
        numbered_parts = []
        for i, (arg, dist) in enumerate(candidates):
            text = (arg.argumentatie_cnsc or arg.argumente_contestator or "")[:200]
            numbered_parts.append(f"[{i + 1}] {arg.cod_critica}: {text}")

        rerank_prompt = (
            "Ordonează următoarele fragmente juridice după relevanță pentru întrebarea:\n"
            f'"{query}"\n\n'
            + "\n".join(numbered_parts)
            + "\n\nRăspunde DOAR cu numerele în ordinea relevanței, separate prin virgulă.\n"
            "Exemplu: 3,1,5,2,4"
        )

        try:
            response = await self.llm.complete(
                prompt=rerank_prompt,
                temperature=0.0,
                max_tokens=50,
            )
            # Parse the comma-separated numbers
            order = [
                int(x.strip()) - 1
                for x in response.strip().split(",")
                if x.strip().isdigit()
            ]
            reranked = [candidates[i] for i in order if 0 <= i < len(candidates)]

            # Append any chunks not mentioned in the LLM response
            seen = {i for i in order if 0 <= i < len(candidates)}
            for i, chunk in enumerate(candidates):
                if i not in seen:
                    reranked.append(chunk)

            logger.info(
                "rerank_completed",
                query=query[:80],
                candidates=len(candidates),
                reranked=len(reranked),
            )
            return reranked[:top_k]
        except Exception as e:
            logger.warning("rerank_failed", error=str(e))
            return chunks[:top_k]

    async def _expand_query(self, query: str) -> list[str]:
        """Generate query reformulations using LLM for better retrieval.

        Produces 2-3 variants with equivalent legal terminology so that
        vector search can match documents using different phrasing
        (e.g., "99% materie primă" → "preț aparent neobișnuit de scăzut").

        Skips expansion for simple queries or direct BO references.

        Args:
            query: Original user query.

        Returns:
            List of query strings: [original] + up to 3 expansions.
        """
        # Skip expansion for short queries or BO references
        if len(query.split()) < 4 or self._extract_bo_references(query):
            return [query]

        expansion_prompt = (
            "Reformulează următoarea întrebare în 3 variante scurte pentru căutare "
            "în jurisprudența CNSC. Folosește termeni juridici din achizițiile publice "
            "din România. Răspunde DOAR cu cele 3 variante, una per linie.\n\n"
            f"Întrebare: {query}"
        )

        try:
            response = await self.llm.complete(
                prompt=expansion_prompt,
                temperature=0.3,
                max_tokens=200,
            )
            variants = [
                line.strip().lstrip("0123456789.-) ")
                for line in response.strip().split("\n")
                if line.strip() and len(line.strip()) > 5
            ]
            result = [query] + variants[:1]
            logger.info(
                "query_expansion",
                original=query[:80],
                variants=len(result) - 1,
                expanded=[v[:60] for v in result[1:]],
            )
            return result
        except Exception as e:
            logger.warning("query_expansion_failed", error=str(e))
            return [query]

    async def search_decisions(
        self,
        query: str,
        session: AsyncSession,
        limit: int = 5,
        max_chunks: int = 20,
        scope_decision_ids: list[str] | None = None,
        enable_expansion: bool = False,
        enable_rerank: bool = False,
        query_vector: list[float] | None = None,
    ) -> tuple[list[DecizieCNSC], list[tuple[ArgumentareCritica, float]]]:
        """Search for relevant decisions based on query.

        Returns top max_chunks ArgumentareCritica syntheses (LLM-extracted)
        plus their parent decision metadata (without text_integral).
        More chunks = broader coverage across more decisions (typically 10-15+).

        Search strategy (in order of priority):
        1. Direct BO references (e.g. BO2025_1011)
        2. Legal article/reference search (e.g. art. 57 din Legea 98/2016)
        3. Vector search on ArgumentareCritica embeddings (primary strategy)
        4. Keyword ILIKE fallback

        Args:
            query: User's search query.
            session: Database session.
            limit: Maximum decisions for fallback searches (BO, legal ref, keyword).
            max_chunks: Maximum ArgumentareCritica chunks to return from vector search.
            scope_decision_ids: If provided, restrict search to these decision IDs only.
            enable_expansion: If True, use LLM to generate query reformulations.
            enable_rerank: If True, use LLM to rerank retrieved chunks.
            query_vector: Pre-computed embedding vector.

        Returns:
            Tuple of (decisions, matched_chunks). matched_chunks is a list of
            (ArgumentareCritica, distance) tuples; empty if using fallback.
        """
        import time
        t0 = time.monotonic()

        logger.info("searching_decisions", query=query, max_chunks=max_chunks,
                     scoped=scope_decision_ids is not None,
                     expansion=enable_expansion, rerank=enable_rerank)

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

        # 3. Vector search — primary strategy
        # Optional query expansion (OFF by default, user can enable via UI toggle)
        queries = [query]
        if enable_expansion:
            queries = await self._expand_query(query)

        # Vector search for each query variant
        t_vec_start = time.monotonic()
        matched_chunks: list[tuple[ArgumentareCritica, float]] = []
        for i, q in enumerate(queries):
            v_results = await self.search_by_vector(
                q, session, limit=max_chunks,
                scope_decision_ids=scope_decision_ids,
                query_vector=query_vector if i == 0 else None,
            )
            matched_chunks.extend(v_results)
        t_vec = time.monotonic()
        logger.info("timing_vector_search",
                     duration_s=round(t_vec - t_vec_start, 2),
                     chunks=len(matched_chunks))

        # Optional trigram search (only when scope + expansion are both active)
        if scope_decision_ids is not None and enable_expansion:
            all_trigram: list[tuple[ArgumentareCritica, float]] = []
            for q in queries:
                t_results = await self._trigram_search(
                    q, session, limit=max_chunks,
                    scope_decision_ids=scope_decision_ids,
                )
                all_trigram.extend(t_results)
            if matched_chunks and all_trigram:
                matched_chunks = self._rrf_merge(matched_chunks, all_trigram)
            elif all_trigram:
                matched_chunks = all_trigram

        if matched_chunks:
            # Optional LLM reranking (OFF by default, user can enable via UI toggle)
            if enable_rerank:
                matched_chunks = await self._rerank_chunks(
                    query, matched_chunks, top_k=max_chunks
                )

            # Deduplicate chunks (same chunk from different query variants)
            seen_chunk_ids = set()
            deduped_chunks = []
            for arg, dist in matched_chunks:
                if arg.id not in seen_chunk_ids:
                    seen_chunk_ids.add(arg.id)
                    deduped_chunks.append((arg, dist))
            matched_chunks = deduped_chunks[:max_chunks]

            # Collect ALL unique decision IDs from matched chunks (no cap)
            unique_decision_ids = list({arg.decizie_id for arg, _ in matched_chunks})

            # Load parent decisions WITHOUT text_integral (metadata only)
            t_load_start = time.monotonic()
            from sqlalchemy.orm import defer
            stmt = (
                select(DecizieCNSC)
                .options(defer(DecizieCNSC.text_integral))
                .where(DecizieCNSC.id.in_(unique_decision_ids))
            )
            result = await session.execute(stmt)
            decisions = list(result.scalars().all())
            t_load = time.monotonic()
            logger.info("timing_decision_load",
                         duration_s=round(t_load - t_load_start, 2),
                         count=len(decisions))

            # Sort decisions by first appearance in matched chunks
            first_appearance = {}
            for i, (arg, _) in enumerate(matched_chunks):
                if arg.decizie_id not in first_appearance:
                    first_appearance[arg.decizie_id] = i
            decisions.sort(key=lambda d: first_appearance.get(d.id, 999))

            logger.info(
                "vector_search_decisions_found",
                count=len(decisions),
                chunks_matched=len(matched_chunks),
                duration_s=round(t_load - t0, 2),
            )
            return decisions, matched_chunks

        # 3b. CPV domain search — use semantic CPV matching as fallback
        # when vector search found nothing (e.g., domain-specific queries)
        cpv_codes = await self._find_cpv_codes_for_query(
            query, session, query_vector=query_vector
        )
        if cpv_codes:
            cpv_decisions, cpv_chunks = await self._search_by_cpv_domain(
                cpv_codes, session, limit=limit
            )
            if cpv_decisions:
                logger.info(
                    "cpv_domain_fallback_used",
                    cpv_codes=cpv_codes[:5],
                    decisions=len(cpv_decisions),
                )
                return cpv_decisions, cpv_chunks

        # 4. Fallback: keyword ILIKE search
        logger.info("falling_back_to_keyword_search", query=query)
        decisions = await self._keyword_search(
            query, session, limit, scope_decision_ids=scope_decision_ids
        )
        return decisions, []

    async def _keyword_search(
        self,
        query: str,
        session: AsyncSession,
        limit: int,
        scope_decision_ids: list[str] | None = None,
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

        from sqlalchemy.orm import defer
        if not conditions:
            stmt = (
                select(DecizieCNSC)
                .options(defer(DecizieCNSC.text_integral))
                .order_by(DecizieCNSC.data_decizie.desc().nulls_last())
                .limit(limit)
            )
        else:
            stmt = (
                select(DecizieCNSC)
                .options(defer(DecizieCNSC.text_integral))
                .where(or_(*conditions))
                .order_by(DecizieCNSC.data_decizie.desc().nulls_last())
                .limit(limit)
            )

        # Scope pre-filter
        if scope_decision_ids is not None:
            stmt = stmt.where(DecizieCNSC.id.in_(scope_decision_ids))

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

                # Sintezele LLM din ArgumentareCritica conțin toată informația
                # relevantă — text_integral nu mai este necesar (conține garbage)

                contexts.append("\n".join(context_parts))
        else:
            # Fallback: keyword search found decisions but no ArgumentareCritica chunks
            # Show only metadata (text_integral not loaded for performance)
            for dec in decisions:
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
                ]
                contexts.append("\n".join(context_parts))

        return contexts

    def _build_citations(
        self,
        decisions: list[DecizieCNSC],
        matched_chunks: list[tuple[ArgumentareCritica, float]],
    ) -> list[Citation]:
        """Build citations from ALL matched decisions, ordered by relevance.

        Order follows vector search distance (first = most relevant).
        Includes every decision that has matched chunks, not just those
        mentioned in the LLM response.
        """
        citations = []
        decision_map = {d.id: d for d in decisions}
        seen_dec_ids = set()

        # matched_chunks is sorted by distance (relevance)
        # First occurrence of each decision determines its rank
        for arg, _dist in matched_chunks:
            if arg.decizie_id in seen_dec_ids:
                continue
            seen_dec_ids.add(arg.decizie_id)
            dec = decision_map.get(arg.decizie_id)
            if not dec:
                continue

            citation_text = (
                arg.argumentatie_cnsc[:300] + "..."
                if arg.argumentatie_cnsc
                else f"Decizia {dec.external_id} — {dec.solutie_contestatie or 'N/A'}"
            )
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
        scope_decision_ids: list[str] | None = None,
        enable_expansion: bool = False,
        enable_rerank: bool = False,
    ) -> tuple[list[str], str, list[Citation], float, list[str]]:
        """Prepare RAG context without generating the LLM response.

        Args:
            scope_decision_ids: If provided, restrict search to these decision IDs only.
            enable_expansion: If True, use LLM query expansion for better retrieval.
                Default False for performance.
            enable_rerank: If True, use LLM reranking of retrieved chunks.

        Returns:
            Tuple of (contexts, system_prompt, citations, confidence, suggested_questions).
            Returns None for contexts/system_prompt if no results found.
        """
        import time
        t0 = time.monotonic()

        # EMBED QUERY ONCE — reuse the vector in all sub-functions
        query_vector = await self.embedding_service.embed_query(query)
        t_embed = time.monotonic()
        logger.info("timing_embed_query", duration_s=round(t_embed - t0, 2))

        # Note: legislation + decisions share the same DB session,
        # so they must run sequentially (asyncpg doesn't support
        # concurrent queries on the same connection)
        legislation_fragments = await self._search_legislation_fragments(
            query, session, limit=5, query_vector=query_vector
        )
        t_legis = time.monotonic()
        logger.info("timing_legislation_search",
                     duration_s=round(t_legis - t_embed, 2),
                     results=len(legislation_fragments))

        decisions, matched_chunks = await self.search_decisions(
            query, session, limit=max_decisions,
            scope_decision_ids=scope_decision_ids,
            enable_expansion=enable_expansion,
            enable_rerank=enable_rerank,
            query_vector=query_vector,
        )
        t_decisions = time.monotonic()
        logger.info("timing_decision_search",
                     duration_s=round(t_decisions - t_legis, 2),
                     decisions=len(decisions),
                     chunks=len(matched_chunks))

        logger.info("timing_prepare_context_total",
                     duration_s=round(t_decisions - t0, 2),
                     embedding_s=round(t_embed - t0, 2),
                     legislation_s=round(t_legis - t_embed, 2),
                     decisions_s=round(t_decisions - t_legis, 2))

        if not decisions and not legislation_fragments:
            return None, None, [], 0.0, ["Ce decizii CNSC sunt disponibile?", "Arată-mi toate deciziile"]

        contexts = []
        if legislation_fragments:
            contexts.extend(self._build_legislation_context(legislation_fragments))
        if decisions:
            contexts.extend(self._build_context(decisions, matched_chunks))

        system_prompt = self._build_system_prompt(
            bool(legislation_fragments), bool(decisions)
        )
        citations = self._build_citations(decisions, matched_chunks)
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
        scope_decision_ids: list[str] | None = None,
        enable_rerank: bool = False,
        enable_expansion: bool = False,
    ) -> tuple[str, list[Citation], float, list[str]]:
        """Generate a response to user query using RAG.

        Args:
            query: User's question.
            session: Database session.
            conversation_history: Optional previous conversation messages.
            max_decisions: Maximum number of decisions to retrieve.
            scope_decision_ids: If provided, restrict search to these decision IDs only.
            enable_rerank: If True, use LLM reranking of retrieved chunks.
            enable_expansion: If True, use LLM query expansion for better retrieval.

        Returns:
            Tuple of (response_text, citations, confidence, suggested_questions).
        """
        logger.info("generating_rag_response", query=query,
                     scoped=scope_decision_ids is not None)

        contexts, system_prompt, citations, confidence, suggested_questions = await self.prepare_context(
            query, session, conversation_history, max_decisions,
            scope_decision_ids=scope_decision_ids,
            enable_rerank=enable_rerank,
            enable_expansion=enable_expansion,
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
