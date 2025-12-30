"""RAG (Retrieval-Augmented Generation) service for ExpertAP.

This service handles:
1. Retrieving relevant CNSC decisions from the database
2. Building context for LLM generation
3. Generating responses with verified citations
"""

from typing import Optional
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.decision import DecizieCNSC, ArgumentareCritica
from app.services.llm.gemini import GeminiProvider
from app.api.v1.chat import Citation

logger = get_logger(__name__)


class RAGService:
    """Service for retrieval-augmented generation over CNSC decisions."""

    def __init__(self, llm_provider: Optional[GeminiProvider] = None):
        """Initialize RAG service.

        Args:
            llm_provider: Optional LLM provider. If not provided, creates new GeminiProvider.
        """
        self.llm = llm_provider or GeminiProvider(model="gemini-1.5-flash")

    async def search_decisions(
        self,
        query: str,
        session: AsyncSession,
        limit: int = 5
    ) -> list[DecizieCNSC]:
        """Search for relevant decisions based on query.

        Uses full-text search with PostgreSQL trigrams for now.
        Future: Will use vector embeddings for semantic search.

        Args:
            query: User's search query
            session: Database session
            limit: Maximum number of results

        Returns:
            List of relevant decisions
        """
        logger.info("searching_decisions", query=query, limit=limit)

        # Extract potential keywords from query
        keywords = self._extract_keywords(query)

        # Build search query using ILIKE for case-insensitive search
        # This is a simple text search - will be replaced with vector search later
        conditions = []
        for keyword in keywords:
            keyword_pattern = f"%{keyword}%"
            conditions.append(DecizieCNSC.text_integral.ilike(keyword_pattern))
            conditions.append(DecizieCNSC.contestator.ilike(keyword_pattern))
            conditions.append(DecizieCNSC.autoritate_contractanta.ilike(keyword_pattern))

        if not conditions:
            # If no keywords, return recent decisions
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

        logger.info("decisions_found", count=len(decisions))
        return decisions

    def _extract_keywords(self, query: str) -> list[str]:
        """Extract meaningful keywords from query.

        Removes common Romanian stop words and short words.
        """
        # Common Romanian stop words to filter
        stop_words = {
            'ce', 'sunt', 'este', 'cum', 'care', 'din', 'la', 'în', 'și', 'sau',
            'pentru', 'cu', 'despre', 'pe', 'de', 'a', 'ai', 'am', 'ma', 'mi',
            'le', 'îmi', 'îți', 'și-a', 'dat', 'dau', 'da', 'spune', 'spune-mi',
            'decizii', 'decizie', 'cnsc', 'avem', 'baza', 'date'
        }

        # Extract words, convert to lowercase, filter stop words and short words
        words = query.lower().split()
        keywords = [
            word.strip('.,?!;:')
            for word in words
            if len(word) >= 3 and word.lower() not in stop_words
        ]

        logger.debug("extracted_keywords", keywords=keywords)
        return keywords

    def _build_context(self, decisions: list[DecizieCNSC]) -> list[str]:
        """Build context strings from decisions for LLM.

        Formats each decision as a structured document for the LLM to reference.
        """
        contexts = []

        for dec in decisions:
            # Build structured summary
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
                "Text integral (fragment):",
                # Include first 1500 chars of text to keep context manageable
                dec.text_integral[:1500] + ("..." if len(dec.text_integral) > 1500 else ""),
            ]

            contexts.append("\n".join(context_parts))

        return contexts

    def _extract_citations(
        self,
        response: str,
        decisions: list[DecizieCNSC]
    ) -> list[Citation]:
        """Extract and verify citations from LLM response.

        Looks for references to decisions in the response and creates
        verified citations.
        """
        citations = []

        for dec in decisions:
            # Check if this decision is referenced in the response
            decision_ref = dec.external_id  # e.g., "BO2023_123"

            if decision_ref in response or str(dec.numar_decizie or '') in response:
                # Extract a relevant snippet from the decision
                # For now, use the first 200 chars as citation text
                citation_text = dec.text_integral[:200] + "..."

                citations.append(Citation(
                    decision_id=dec.external_id,
                    text=citation_text,
                    verified=True
                ))

        return citations

    def _generate_suggested_questions(
        self,
        decisions: list[DecizieCNSC]
    ) -> list[str]:
        """Generate contextual follow-up questions based on decisions found."""
        suggestions = []

        # Extract unique criticism codes and solutions
        all_critici = set()
        solutions = set()

        for dec in decisions:
            if dec.coduri_critici:
                all_critici.update(dec.coduri_critici)
            if dec.solutie_contestatie:
                solutions.add(dec.solutie_contestatie)

        # Generate questions based on findings
        if all_critici:
            critica_list = ', '.join(sorted(all_critici)[:3])
            suggestions.append(f"Ce jurisprudență există pentru criticile {critica_list}?")

        if 'ADMIS' in solutions or 'ADMIS_PARTIAL' in solutions:
            suggestions.append("Care sunt argumentele care au dus la admiterea contestației?")

        if 'RESPINS' in solutions:
            suggestions.append("De ce au fost respinse aceste contestații?")

        # Always add generic helpful questions
        suggestions.append("Arată-mi decizii similare")

        return suggestions[:4]  # Limit to 4 suggestions

    async def generate_response(
        self,
        query: str,
        session: AsyncSession,
        conversation_history: list[dict] | None = None,
        max_decisions: int = 5
    ) -> tuple[str, list[Citation], float, list[str]]:
        """Generate a response to user query using RAG.

        Args:
            query: User's question
            session: Database session
            conversation_history: Optional previous conversation messages
            max_decisions: Maximum number of decisions to retrieve

        Returns:
            Tuple of (response_text, citations, confidence, suggested_questions)
        """
        logger.info("generating_rag_response", query=query)

        # 1. Retrieve relevant decisions
        decisions = await self.search_decisions(query, session, limit=max_decisions)

        if not decisions:
            logger.warning("no_decisions_found", query=query)
            return (
                "Nu am găsit decizii CNSC relevante pentru această întrebare. "
                "Încearcă să reformulezi întrebarea sau să folosești termeni mai specifici.",
                [],
                0.0,
                ["Ce decizii CNSC sunt disponibile?", "Arată-mi toate deciziile"]
            )

        # 2. Build context from decisions
        contexts = self._build_context(decisions)

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
                temperature=0.1,  # Low temperature for factual responses
                max_tokens=2048
            )

            # 5. Extract citations
            citations = self._extract_citations(response_text, decisions)

            # 6. Calculate confidence based on number of decisions found
            confidence = min(1.0, len(decisions) / max_decisions)

            # 7. Generate suggested follow-up questions
            suggested_questions = self._generate_suggested_questions(decisions)

            logger.info(
                "rag_response_generated",
                decisions_used=len(decisions),
                citations=len(citations),
                confidence=confidence
            )

            return response_text, citations, confidence, suggested_questions

        except Exception as e:
            logger.error("rag_generation_error", error=str(e))
            raise
