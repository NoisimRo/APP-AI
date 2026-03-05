"""RAG (Retrieval-Augmented Generation) service for ExpertAP.

This service handles:
1. Retrieving relevant CNSC decisions via semantic vector search
2. Building rich context from ArgumentareCritica chunks
3. Generating responses with verified citations

The retrieval strategy uses ArgumentareCritica as the primary search unit
(each row is a natural semantic chunk covering one criticism with full
argumentation flow), then loads the parent DecizieCNSC for metadata.
Falls back to keyword ILIKE search when no embeddings are available.
"""

from typing import Optional
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.logging import get_logger
from app.models.decision import DecizieCNSC, ArgumentareCritica
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

    async def search_decisions(
        self,
        query: str,
        session: AsyncSession,
        limit: int = 5,
    ) -> tuple[list[DecizieCNSC], list[tuple[ArgumentareCritica, float]]]:
        """Search for relevant decisions based on query.

        Uses vector search on ArgumentareCritica when embeddings are available,
        falling back to keyword ILIKE search otherwise.

        Args:
            query: User's search query.
            session: Database session.
            limit: Maximum number of decisions to return.

        Returns:
            Tuple of (decisions, matched_chunks). matched_chunks is a list of
            (ArgumentareCritica, distance) tuples; empty if using fallback.
        """
        logger.info("searching_decisions", query=query, limit=limit)

        # Check if embeddings exist
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
                )
                return decisions, matched_chunks

        # Fallback: keyword ILIKE search
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

    def _build_context(
        self,
        decisions: list[DecizieCNSC],
        matched_chunks: list[tuple[ArgumentareCritica, float]],
    ) -> list[str]:
        """Build context strings from decisions and matched chunks for LLM.

        When vector search was used, builds rich context from the matched
        ArgumentareCritica chunks (full argumentation text). Falls back to
        truncated text_integral when no chunks are available.
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

                context_parts = [
                    f"=== Decizia {dec.external_id} ===",
                    f"Număr decizie: {dec.numar_decizie or 'N/A'}",
                    f"Dată: {dec.data_decizie.strftime('%d.%m.%Y') if dec.data_decizie else 'N/A'}",
                    f"Complet: {dec.complet or 'N/A'}",
                    f"Tip contestație: {dec.tip_contestatie}",
                    f"Coduri critici: {', '.join(dec.coduri_critici) if dec.coduri_critici else 'N/A'}",
                    f"Soluție: {dec.solutie_contestatie or 'N/A'}",
                    f"Contestator: {dec.contestator or 'N/A'}",
                    f"Autoritate contractantă: {dec.autoritate_contractanta or 'N/A'}",
                    "",
                ]

                for arg, dist in chunks:
                    similarity = 1.0 - dist
                    context_parts.append(
                        f"--- Critica {arg.cod_critica} (relevanță: {similarity:.2f}) ---"
                    )
                    if arg.argumente_contestator:
                        context_parts.append(f"Argumente contestator: {arg.argumente_contestator}")
                    if arg.argumente_ac:
                        context_parts.append(f"Argumente AC: {arg.argumente_ac}")
                    if arg.elemente_retinute_cnsc:
                        context_parts.append(f"Elemente reținute CNSC: {arg.elemente_retinute_cnsc}")
                    if arg.argumentatie_cnsc:
                        context_parts.append(f"Argumentație CNSC: {arg.argumentatie_cnsc}")
                    if arg.castigator_critica and arg.castigator_critica != "unknown":
                        context_parts.append(f"Câștigător: {arg.castigator_critica}")
                    context_parts.append("")

                contexts.append("\n".join(context_parts))
        else:
            # Fallback: use text_integral (up to 15000 chars per decision,
            # reduced proportionally when multiple decisions to stay within
            # LLM context limits)
            max_chars_per_decision = max(4000, 60000 // max(len(decisions), 1))
            for dec in decisions:
                text = dec.text_integral
                truncated = len(text) > max_chars_per_decision
                context_parts = [
                    f"=== Decizia {dec.external_id} ===",
                    f"Număr decizie: {dec.numar_decizie or 'N/A'}",
                    f"Dată: {dec.data_decizie.strftime('%d.%m.%Y') if dec.data_decizie else 'N/A'}",
                    f"Complet: {dec.complet or 'N/A'}",
                    f"Tip contestație: {dec.tip_contestatie}",
                    f"Coduri critici: {', '.join(dec.coduri_critici) if dec.coduri_critici else 'N/A'}",
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
        logger.info("generating_rag_response", query=query)

        # 1. Retrieve relevant decisions (vector search with keyword fallback)
        decisions, matched_chunks = await self.search_decisions(
            query, session, limit=max_decisions
        )

        if not decisions:
            logger.warning("no_decisions_found", query=query)
            return (
                "Nu am găsit decizii CNSC relevante pentru această întrebare. "
                "Încearcă să reformulezi întrebarea sau să folosești termeni mai specifici.",
                [],
                0.0,
                ["Ce decizii CNSC sunt disponibile?", "Arată-mi toate deciziile"],
            )

        # 2. Build context from decisions and matched chunks
        contexts = self._build_context(decisions, matched_chunks)

        # 3. Build system prompt
        system_prompt = """Ești ExpertAP, un consultant senior în achiziții publice specializat în jurisprudența CNSC (Consiliul Național de Soluționare a Contestațiilor).

Sarcina ta este să răspunzi la întrebări despre deciziile CNSC folosind EXCLUSIV informațiile din documentele furnizate în contextul de mai jos.

Reguli importante:
1. Bazează-te DOAR pe informațiile din contextul furnizat
2. Citează deciziile specifice când răspunzi (ex: "Conform deciziei BO2023_123...")
3. Dacă informația nu este în context, spune clar că nu ai suficiente date
4. Oferă răspunsuri clare, structurate și profesionale
5. Folosește terminologie juridică corectă specifică achizițiilor publice
6. Când discuți despre soluții, menționează argumentele CNSC

Răspunde în limba română, profesional și precis."""

        # 4. Generate response with LLM
        try:
            response_text = await self.llm.complete(
                prompt=query,
                context=contexts,
                system_prompt=system_prompt,
                temperature=0.1,
                max_tokens=2048,
            )

            # 5. Extract citations
            citations = self._extract_citations(response_text, decisions, matched_chunks)

            # 6. Calculate confidence
            confidence = self._calculate_confidence(
                decisions, matched_chunks, max_decisions
            )

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
