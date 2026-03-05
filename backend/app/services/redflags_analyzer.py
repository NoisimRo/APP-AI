"""Red flags detection service for procurement documents.

Analyzes procurement documents (tenders, specifications) to identify
potentially illegal or restrictive clauses. Uses RAG vector search
to ground jurisprudence references in actual CNSC decisions.
"""

import json
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.decision import ArgumentareCritica, DecizieCNSC
from app.services.embedding import EmbeddingService
from app.services.llm.gemini import GeminiProvider

logger = get_logger(__name__)


class RedFlagsAnalyzer:
    """Service for analyzing procurement documents for red flags."""

    def __init__(self, llm_provider: Optional[GeminiProvider] = None):
        self.llm = llm_provider or GeminiProvider(model="gemini-2.5-flash")
        self.embedding_service = EmbeddingService(llm_provider=self.llm)

    async def _vector_search_jurisprudence(
        self,
        query_text: str,
        session: AsyncSession,
        limit: int = 5,
    ) -> tuple[list[DecizieCNSC], list[tuple[ArgumentareCritica, float]]]:
        """Search CNSC decisions by vector similarity.

        Args:
            query_text: Text to search for (clause or issue description).
            session: Database session.
            limit: Maximum number of chunks to return.

        Returns:
            Tuple of (decisions, matched_chunks with distances).
        """
        # Check if embeddings exist
        has_embeddings = await session.scalar(
            select(func.count())
            .select_from(ArgumentareCritica)
            .where(ArgumentareCritica.embedding.isnot(None))
        )

        if not has_embeddings or has_embeddings == 0:
            logger.warning("no_embeddings_available_for_redflags")
            return [], []

        # Generate query embedding
        query_vector = await self.embedding_service.embed_query(query_text)

        # Vector similarity search
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

        matched_chunks = [(row.ArgumentareCritica, row.distance) for row in rows]

        # Filter by relevance threshold (cosine distance < 0.5 means similarity > 0.5)
        relevant_chunks = [(arg, dist) for arg, dist in matched_chunks if dist < 0.5]

        if not relevant_chunks:
            logger.info("no_relevant_chunks_found", top_distance=rows[0].distance if rows else None)
            return [], []

        # Load parent decisions
        dec_ids = list({arg.decizie_id for arg, _ in relevant_chunks})
        stmt = select(DecizieCNSC).where(DecizieCNSC.id.in_(dec_ids))
        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        logger.info(
            "vector_search_jurisprudence",
            query_preview=query_text[:80],
            chunks_found=len(relevant_chunks),
            decisions_found=len(decisions),
            top_similarity=1.0 - relevant_chunks[0][1] if relevant_chunks else 0,
        )

        return decisions, relevant_chunks

    def _build_jurisprudence_context(
        self,
        decisions: list[DecizieCNSC],
        matched_chunks: list[tuple[ArgumentareCritica, float]],
    ) -> str:
        """Build rich context from matched CNSC decisions and chunks."""
        if not decisions or not matched_chunks:
            return ""

        decision_map = {d.id: d for d in decisions}
        chunks_by_decision: dict[str, list[tuple[ArgumentareCritica, float]]] = {}
        for arg, dist in matched_chunks:
            chunks_by_decision.setdefault(arg.decizie_id, []).append((arg, dist))

        parts = []
        for decizie_id, chunks in chunks_by_decision.items():
            dec = decision_map.get(decizie_id)
            if not dec:
                continue

            section = [
                f"=== Decizia {dec.external_id} ===",
                f"Soluție: {dec.solutie_contestatie or 'N/A'}",
                f"Contestator: {dec.contestator or 'N/A'}",
                f"Autoritate contractantă: {dec.autoritate_contractanta or 'N/A'}",
            ]

            for arg, dist in chunks:
                similarity = 1.0 - dist
                section.append(f"\n--- Critica {arg.cod_critica} (relevanță: {similarity:.2f}) ---")
                if arg.argumente_contestator:
                    section.append(f"Argumente contestator: {arg.argumente_contestator[:500]}")
                if arg.elemente_retinute_cnsc:
                    section.append(f"Elemente reținute CNSC: {arg.elemente_retinute_cnsc[:500]}")
                if arg.argumentatie_cnsc:
                    section.append(f"Argumentație CNSC: {arg.argumentatie_cnsc[:500]}")
                if arg.castigator_critica and arg.castigator_critica != "unknown":
                    section.append(f"Câștigător: {arg.castigator_critica}")

            parts.append("\n".join(section))

        return "\n\n---\n\n".join(parts)

    async def analyze(
        self,
        document_text: str,
        session: Optional[AsyncSession] = None,
        use_jurisprudence: bool = True
    ) -> list[dict]:
        """Analyze document for red flags with RAG-grounded jurisprudence.

        Args:
            document_text: Text of procurement document.
            session: Database session for jurisprudence lookup.
            use_jurisprudence: Whether to search and include CNSC jurisprudence.

        Returns:
            List of detected red flags with details and verified decision refs.
        """
        logger.info(
            "analyzing_red_flags",
            text_length=len(document_text),
            use_jurisprudence=use_jurisprudence,
        )

        # Step 1: Search for relevant CNSC jurisprudence via vector search
        jurisprudence_context = ""
        available_decisions: dict[str, DecizieCNSC] = {}

        if use_jurisprudence and session:
            # Use a summary of the document as the search query
            # (truncate to reasonable length for embedding)
            search_query = document_text[:3000]
            decisions, matched_chunks = await self._vector_search_jurisprudence(
                search_query, session, limit=10
            )
            if decisions:
                jurisprudence_context = self._build_jurisprudence_context(
                    decisions, matched_chunks
                )
                available_decisions = {d.external_id: d for d in decisions}

        # Step 2: Build system prompt
        decision_ids_list = list(available_decisions.keys())
        decisions_instruction = ""
        if decision_ids_list:
            decisions_instruction = f"""

IMPORTANT - Jurisprudență CNSC disponibilă:
Ai la dispoziție următoarele decizii CNSC reale: {', '.join(decision_ids_list)}
Contextul complet al acestor decizii este furnizat mai jos.

Reguli stricte pentru jurisprudență:
- Poți cita DOAR deciziile din lista de mai sus
- NU inventa și NU hallucina alte numere de decizii
- Pentru fiecare red flag, dacă una din deciziile disponibile este relevantă, include-o în "decision_refs"
- Dacă NICIO decizie disponibilă nu este relevantă pentru un anumit red flag, lasă "decision_refs" ca listă goală []
- Verifică că decizia chiar se referă la o problemă similară înainte de a o cita"""
        else:
            decisions_instruction = """

Nu ai la dispoziție jurisprudență CNSC. Lasă câmpul "decision_refs" ca listă goală [] pentru fiecare red flag."""

        system_prompt = f"""Ești un expert în achiziții publice din România, specializat în identificarea clauzelor restrictive și ilegale.

Sarcina ta este să analizezi documentația de achiziție și să identifici "red flags" - clauze problematice care ar putea fi ilegale sau discriminatorii.

Categorii de red flags:
1. **Experiență similară excesivă** - Cerințe nejustificate de experiență
2. **Cifră de afaceri disproporționată** - Cerințe financiare excesive
3. **Certificări restrictive** - Cerințe de certificare care limitează concurența
4. **Personal dedicat excesiv** - Cerințe de personal nejustificate
5. **Clauze discriminatorii** - Criterii care favorizează anumiți operatori
6. **Termene nerealiste** - Termene prea scurte pentru pregătirea ofertei
7. **Criterii tehnice restrictive** - Specificații prea detaliate care limitează concurența
{decisions_instruction}

Pentru fiecare red flag, furnizează:
- **category**: Categoria din lista de mai sus
- **severity**: CRITICĂ, MEDIE, sau SCĂZUTĂ
- **clause**: Textul exact din document
- **issue**: Descrierea detaliată a problemei
- **legal_reference**: Articolul din Legea 98/2016 sau HG 395/2016
- **recommendation**: Cum ar trebui modificată clauza
- **decision_refs**: Lista de ID-uri de decizii CNSC relevante (DOAR din cele disponibile!) sau [] dacă nicio decizie nu e relevantă

Răspunde EXCLUSIV în format JSON:
```json
{{
  "red_flags": [
    {{
      "category": "...",
      "severity": "CRITICĂ",
      "clause": "textul exact",
      "issue": "descrierea problemei",
      "legal_reference": "art. ... din Legea 98/2016",
      "recommendation": "recomandarea",
      "decision_refs": ["BO2025_1000"]
    }}
  ]
}}
```

Dacă nu identifici red flags, returnează: `{{"red_flags": []}}`"""

        # Step 3: Build the full prompt with jurisprudence context
        prompt_parts = [
            "Analizează următoarea documentație de achiziție publică și identifică toate clauzele problematice (red flags):\n"
        ]

        if jurisprudence_context:
            prompt_parts.append(
                "\n=== JURISPRUDENȚĂ CNSC RELEVANTĂ (din baza de date) ===\n"
                + jurisprudence_context
                + "\n=== SFÂRȘIT JURISPRUDENȚĂ ===\n\n"
            )

        prompt_parts.append(
            f"=== DOCUMENTAȚIE ACHIZIȚIE ===\n{document_text}\n=== SFÂRȘIT DOCUMENTAȚIE ==="
        )

        full_prompt = "\n".join(prompt_parts)

        # Step 4: Call LLM
        try:
            response = await self.llm.complete(
                prompt=full_prompt,
                system_prompt=system_prompt,
                temperature=0.1,
                max_tokens=4096,
            )

            # Parse JSON response
            response_text = response.strip()
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            result = json.loads(response_text)
            red_flags = result.get("red_flags", [])

            # Step 5: Verify decision refs - remove any that aren't in our actual DB
            valid_ids = set(available_decisions.keys())
            for flag in red_flags:
                refs = flag.get("decision_refs", [])
                if isinstance(refs, list):
                    flag["decision_refs"] = [r for r in refs if r in valid_ids]
                else:
                    flag["decision_refs"] = []

            logger.info(
                "red_flags_analyzed",
                count=len(red_flags),
                has_jurisprudence=bool(available_decisions),
                verified_refs=sum(len(f.get("decision_refs", [])) for f in red_flags),
            )

            return red_flags

        except json.JSONDecodeError as e:
            logger.error("red_flags_json_parse_error", error=str(e), response=response[:500])
            return [{
                "category": "Eroare",
                "severity": "INFO",
                "clause": "",
                "issue": f"Nu s-a putut parsa răspunsul AI: {str(e)}",
                "legal_reference": "",
                "recommendation": "Verifică manual documentația.",
                "decision_refs": []
            }]

        except Exception as e:
            logger.error("red_flags_analysis_error", error=str(e))
            raise
