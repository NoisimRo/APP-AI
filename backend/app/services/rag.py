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
from app.services.llm.gemini import GeminiProvider

logger = get_logger(__name__)


class Citation(BaseModel):
    """A citation from a CNSC decision."""

    decision_id: str
    text: str
    verified: bool = True


class RAGService:
    """Service for retrieval-augmented generation over CNSC decisions."""

    def __init__(self, llm_provider: Optional[GeminiProvider] = None):
        """Initialize RAG service.

        Args:
            llm_provider: Optional LLM provider. If not provided, creates new GeminiProvider.
        """
        self.llm = llm_provider or GeminiProvider(model="gemini-3-flash-preview")
        self.embedding_service = EmbeddingService(llm_provider=self.llm)

    async def search_by_vector(
        self,
        query: str,
        session: AsyncSession,
        limit: int = 10,
    ) -> list[tuple[ArgumentareCritica, float]]:
        """Search ArgumentareCritica by vector cosine similarity.

        Args:
            query: User's search query.
            session: Database session.
            limit: Maximum number of results.

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
            .order_by("distance")
            .limit(limit)
        )

        result = await session.execute(stmt)
        rows = result.all()

        logger.info(
            "vector_search_completed",
            query=query[:80],
            results=len(rows),
            top_distance=rows[0].distance if rows else None,
        )

        return [(row.ArgumentareCritica, row.distance) for row in rows]

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
    ) -> tuple[list[DecizieCNSC], list[tuple[ArgumentareCritica, float]]]:
        """Search for relevant decisions based on query.

        Search strategy (in order of priority):
        1. Direct BO references (e.g. BO2025_1011)
        2. Legal article/reference search (e.g. art. 57 din Legea 98/2016)
        3. CPV domain search (e.g. "catering" → find CPV codes → filter decisions)
        4. Vector search on ArgumentareCritica embeddings (boosted by CPV if available)
        5. Keyword ILIKE fallback

        Args:
            query: User's search query.
            session: Database session.
            limit: Maximum number of decisions to return.

        Returns:
            Tuple of (decisions, matched_chunks). matched_chunks is a list of
            (ArgumentareCritica, distance) tuples; empty if using fallback.
        """
        logger.info("searching_decisions", query=query, limit=limit)

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
            # Vector search path
            matched_chunks = await self.search_by_vector(
                query, session, limit=limit * 3
            )

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

    async def generate_response(
        self,
        query: str,
        session: AsyncSession,
        conversation_history: list[dict] | None = None,
        max_decisions: int = 5,
    ) -> tuple[str, list[Citation], float, list[str]]:
        """Generate a response to user query using RAG.

        Args:
            query: User's question.
            session: Database session.
            conversation_history: Optional previous conversation messages.
            max_decisions: Maximum number of decisions to retrieve.

        Returns:
            Tuple of (response_text, citations, confidence, suggested_questions).
        """
        logger.info(“generating_rag_response”, query=query)

        # 1. Search legislation fragments (parallel with decision search)
        legislation_fragments = await self._search_legislation_fragments(
            query, session, limit=5
        )

        # 2. Retrieve relevant decisions (vector search with keyword fallback)
        decisions, matched_chunks = await self.search_decisions(
            query, session, limit=max_decisions
        )

        if not decisions and not legislation_fragments:
            logger.warning(“no_results_found”, query=query)
            return (
                “Nu am găsit informații relevante pentru această întrebare. “
                “Încearcă să reformulezi întrebarea sau să folosești termeni mai specifici.”,
                [],
                0.0,
                [“Ce decizii CNSC sunt disponibile?”, “Arată-mi toate deciziile”],
            )

        # 2. Build context — legislation first, then decisions
        contexts = []
        if legislation_fragments:
            contexts.extend(self._build_legislation_context(legislation_fragments))
        if decisions:
            contexts.extend(self._build_context(decisions, matched_chunks))

        # 3. Build system prompt
        has_legislation = bool(legislation_fragments)
        has_decisions = bool(decisions)

        system_prompt = “””Ești un consultant senior în achiziții publice specializat în legislația și jurisprudența CNSC (Consiliul Național de Soluționare a Contestațiilor) din România.

Sarcina ta este să răspunzi la întrebări folosind EXCLUSIV informațiile din contextul furnizat mai jos.”””

        if has_legislation and has_decisions:
            system_prompt += “””

Contextul conține atât TEXTE LEGISLATIVE (articole din Legea 98/2016, HG 395/2016, etc.) cât și DECIZII CNSC. Folosește-le pe ambele:
- Citează textul exact al articolelor de lege când sunt disponibile
- Citează deciziile CNSC care aplică sau interpretează acele articole”””
        elif has_legislation:
            system_prompt += “””

Contextul conține TEXTE LEGISLATIVE (articole din Legea 98/2016, HG 395/2016, etc.).
Citează textul exact al articolelor, inclusiv alineatele și literele relevante.”””
        else:
            system_prompt += “””

Contextul conține DECIZII CNSC relevante pentru întrebare.”””

        system_prompt += “””

Reguli importante:
1. Bazează-te DOAR pe informațiile din contextul furnizat
2. Citează sursele specifice: articole de lege cu citare completă (ex: “**art. 2 alin. (3) lit. b) din HG 395/2016**”) și/sau decizii CNSC (ex: “Conform deciziei **BO2025_123**...”)
3. Dacă informația nu este în context, spune clar că nu ai suficiente date
4. Oferă răspunsuri clare, structurate și profesionale
5. Folosește terminologie juridică corectă specifică achizițiilor publice
6. Când discuți despre soluții, menționează argumentele CNSC
7. Când în context există referințe la jurisprudență (decizii ale instanțelor naționale, CJUE, directive europene), citează-le exact așa cum apar
8. NU te prezenta și NU folosi formulări de genul “În calitate de...” - răspunde direct la întrebare
9. **CITĂRI VERBATIM**: Când susții un argument sau prezinți o concluzie, include citate exacte din textul original, folosind ghilimele. Exemplu: *Conform **art. 2 alin. (3)** din HG 395/2016, „textul exact al articolului”*
10. Prezintă atât argumentele contestatorului, cât și cele ale autorității contractante și ale CNSC, cu citate verbatim din fiecare parte, pentru a oferi o imagine completă.

Formatare:
- Folosește **bold** pentru termeni cheie, referințe la articole de lege și la decizii
- Folosește „ghilimele românești” pentru citatele verbatim
- Structurează răspunsul cu paragrafe clare, separate prin linii goale
- Folosește liste numerotate (1. 2. 3.) pentru enumerări
- Folosește titluri (## sau ###) pentru secțiuni distincte când răspunsul este lung
- Fiecare argument sau decizie trebuie să fie pe un paragraf separat

Răspunde în limba română, profesional și precis.”””

        # 4. Generate response with LLM
        try:
            response_text = await self.llm.complete(
                prompt=query,
                context=contexts,
                system_prompt=system_prompt,
                temperature=0.1,
                max_tokens=8192,
            )

            # 5. Extract citations
            citations = self._extract_citations(
                response_text, decisions, matched_chunks
            )

            # 6. Calculate confidence (boost when legislation was found)
            confidence = self._calculate_confidence(
                decisions, matched_chunks, max_decisions
            )
            if legislation_fragments and not decisions:
                # Pure legislation query — high confidence if exact match
                confidence = max(confidence, 0.9)
            elif legislation_fragments:
                # Mixed — boost slightly
                confidence = min(1.0, confidence + 0.1)

            # 7. Generate suggested follow-up questions
            suggested_questions = self._generate_suggested_questions(decisions)

            logger.info(
                "rag_response_generated",
                decisions_used=len(decisions),
                citations=len(citations),
                confidence=confidence,
                used_vector_search=bool(matched_chunks),
            )

            return response_text, citations, confidence, suggested_questions

        except Exception as e:
            logger.error("rag_generation_error", error=str(e))
            raise
