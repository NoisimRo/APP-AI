"""Red flags detection service for procurement documents.

Analyzes procurement documents (tenders, specifications) to identify
potentially illegal or restrictive clauses.
"""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.decision import DecizieCNSC
from app.services.llm.gemini import GeminiProvider

logger = get_logger(__name__)


class RedFlag:
    """A detected red flag in procurement documents."""

    def __init__(
        self,
        category: str,
        severity: str,
        clause: str,
        issue: str,
        legal_reference: str,
        recommendation: str,
        decision_refs: list[str] | None = None
    ):
        self.category = category
        self.severity = severity
        self.clause = clause
        self.issue = issue
        self.legal_reference = legal_reference
        self.recommendation = recommendation
        self.decision_refs = decision_refs or []


class RedFlagsAnalyzer:
    """Service for analyzing procurement documents for red flags."""

    def __init__(self, llm_provider: Optional[GeminiProvider] = None):
        """Initialize red flags analyzer.

        Args:
            llm_provider: Optional LLM provider for analysis
        """
        self.llm = llm_provider or GeminiProvider(model="gemini-3-pro-preview")

    async def search_related_decisions(
        self,
        text: str,
        session: AsyncSession,
        limit: int = 3
    ) -> list[DecizieCNSC]:
        """Search for CNSC decisions related to the text.

        Args:
            text: Document text to analyze
            session: Database session
            limit: Maximum number of decisions to return

        Returns:
            List of related decisions
        """
        # Extract key terms for search
        keywords = []

        # Common red flag terms
        red_flag_terms = [
            'experiență similară', 'cifră afaceri', 'certificare',
            'personal', 'referințe', 'clauză restrictivă'
        ]

        text_lower = text.lower()
        for term in red_flag_terms:
            if term in text_lower:
                keywords.append(term)

        if not keywords:
            return []

        # Search in database (simple ILIKE search)
        # TODO: Replace with vector similarity search
        conditions = []
        for keyword in keywords[:3]:  # Limit to 3 keywords
            pattern = f"%{keyword}%"
            conditions.append(DecizieCNSC.text_integral.ilike(pattern))

        if not conditions:
            return []

        from sqlalchemy import or_
        stmt = (
            select(DecizieCNSC)
            .where(or_(*conditions))
            .limit(limit)
        )

        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        logger.info("related_decisions_found", count=len(decisions), keywords=keywords)
        return decisions

    def _build_context_from_decisions(
        self,
        decisions: list[DecizieCNSC]
    ) -> str:
        """Build context string from decisions for LLM.

        Args:
            decisions: List of CNSC decisions

        Returns:
            Formatted context string
        """
        if not decisions:
            return ""

        contexts = []
        for dec in decisions:
            context = (
                f"Decizia {dec.external_id}: {dec.solutie_contestatie}\n"
                f"Critici: {', '.join(dec.coduri_critici or [])}\n"
                f"Fragmentcheie: {dec.text_integral[:500]}...\n"
            )
            contexts.append(context)

        return "\n---\n".join(contexts)

    async def analyze(
        self,
        document_text: str,
        session: Optional[AsyncSession] = None,
        use_jurisprudence: bool = True
    ) -> list[dict]:
        """Analyze document for red flags.

        Args:
            document_text: Text of procurement document
            session: Optional database session for jurisprudence lookup
            use_jurisprudence: Whether to include CNSC jurisprudence

        Returns:
            List of detected red flags with details
        """
        logger.info(
            "analyzing_red_flags",
            text_length=len(document_text),
            use_jurisprudence=use_jurisprudence
        )

        # Search for related decisions if requested
        context_parts = []
        decision_refs = []

        if use_jurisprudence and session:
            decisions = await self.search_related_decisions(
                document_text, session, limit=3
            )
            if decisions:
                context_parts.append(self._build_context_from_decisions(decisions))
                decision_refs = [dec.external_id for dec in decisions]

        # Build system prompt
        system_prompt = """Ești un expert în achiziții publice din România, specializat în identificarea clauzelor restrictive și ilegale.

Sarcina ta este să analizezi documentația de achiziție și să identifici "red flags" - clauze problematice care ar putea fi ilegale sau discriminatorii.

Categorii de red flags:
1. **Experiență similară excesivă** - Cerințe nejustificate de experiență
2. **Cifră de afaceri disproporționată** - Cerințe financiare excesive
3. **Certificări restrictive** - Cerințe de certificare care limitează concurența
4. **Personal dedicat excesiv** - Cerințe de personal nejustificate
5. **Clauze discriminatorii** - Criterii care favorizează anumiți operatori
6. **Termene nerealiste** - Termene prea scurte pentru pregătirea ofertei
7. **Criterii tehnice restrictive** - Specificații prea detaliate care limitează concurența

Pentru fiecare red flag identificat, furnizează:
- **Categoria**: Din lista de mai sus
- **Severitate**: CRITICĂ, MEDIE, SCĂZUTĂ
- **Clauza problematică**: Textul exact din document
- **Problema**: Ce este ilegal/problematic
- **Referință legală**: Articolul din Legea 98/2016 sau HG 395/2016
- **Recomandare**: Cum ar trebui modificată clauza

Răspunde EXCLUSIV în format JSON cu următoarea structură:
```json
{
  "red_flags": [
    {
      "category": "Experiență similară excesivă",
      "severity": "CRITICĂ",
      "clause": "textul exact al clauzei",
      "issue": "descrierea problemei",
      "legal_reference": "art. 170 alin. (1) din Legea 98/2016",
      "recommendation": "recomandarea de modificare"
    }
  ]
}
```

Dacă nu identifici red flags, returnează: `{"red_flags": []}`
"""

        # Build prompt
        prompt_parts = [
            "Analizează următoarea documentație de achiziție publică și identifică toate clauzele problematice (red flags):\n"
        ]

        if context_parts:
            prompt_parts.append(
                "\n=== JURISPRUDENȚĂ CNSC RELEVANTĂ ===\n"
                + "\n".join(context_parts) +
                "\n=== SFÂRȘIT JURISPRUDENȚĂ ===\n\n"
            )

        prompt_parts.append(f"=== DOCUMENTAȚIE ACHIZIȚIE ===\n{document_text}\n=== SFÂRȘIT DOCUMENTAȚIE ===")

        full_prompt = "\n".join(prompt_parts)

        # Call LLM
        try:
            response = await self.llm.complete(
                prompt=full_prompt,
                system_prompt=system_prompt,
                temperature=0.1,
                max_tokens=4096
            )

            # Parse JSON response
            import json

            # Extract JSON from response (handle markdown code blocks)
            response_text = response.strip()
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            result = json.loads(response_text)
            red_flags = result.get("red_flags", [])

            # Add decision references if available
            for flag in red_flags:
                flag["decision_refs"] = decision_refs

            logger.info(
                "red_flags_analyzed",
                count=len(red_flags),
                has_jurisprudence=len(decision_refs) > 0
            )

            return red_flags

        except json.JSONDecodeError as e:
            logger.error("red_flags_json_parse_error", error=str(e), response=response[:500])
            # Return generic error response
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
