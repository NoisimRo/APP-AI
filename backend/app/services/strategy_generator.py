"""Strategy generator service for procurement contestation.

Combines:
- Historical win rates per criticism code, CPV, panel, procedure type
- RAG search for relevant legislation and jurisprudence
- LLM reasoning to produce actionable contestation strategy

Output: Per-criticism recommendations with legal basis, precedents,
and success probability estimates.
"""

import asyncio
import json
from typing import Optional
from sqlalchemy import select, func, case, and_, not_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.decision import (
    ArgumentareCritica, LegislatieFragment, ActNormativ,
    DecizieCNSC, SpetaANAP,
)
from app.services.embedding import EmbeddingService
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider, get_embedding_provider

logger = get_logger(__name__)

# Procedural rejection reasons — excluded from win rate calculations
PROCEDURAL_REJECTIONS = ["tardivă", "inadmisibilă", "lipsită de interes", "rămasă fără obiect"]

# Concurrency limits
MAX_CONCURRENT_GROUNDING = 6
LLM_CALL_TIMEOUT = 120

# Criticism code descriptions (canonical)
CRITIQUE_LABELS = {
    "D1": "Cerințe restrictive — experiență similară, calificare, specificații tehnice",
    "D2": "Criterii de atribuire / factori de evaluare netransparenți sau subiectivi",
    "D3": 'Denumiri de produse/mărci fără sintagma "sau echivalent"',
    "D4": "Lipsa răspuns clar la solicitările de clarificări",
    "D5": "Forma de constituire a garanției de participare",
    "D6": "Clauze contractuale inechitabile sau excesive",
    "D7": "Nedivizarea achiziției pe loturi",
    "D8": "Alte critici documentație",
    "DAL": "Alte critici documentație",
    "R1": "Contestații proces-verbal ședință deschidere oferte",
    "R2": "Respingerea ofertei ca neconformă sau inacceptabilă",
    "R3": "Prețul neobișnuit de scăzut al altor ofertanți",
    "R4": "Documente calificare alți ofertanți / mod de evaluare",
    "R5": "Lipsa precizării motivelor de respingere",
    "R6": "Lipsa solicitare clarificări / apreciere incorectă răspunsuri",
    "R7": "Anularea fără temei legal a procedurii",
    "R8": "Alte critici rezultat",
    "RAL": "Alte critici rezultat",
}


class StrategyGenerator:
    """Service for generating contestation strategies based on historical data and RAG."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm = llm_provider or get_llm_provider()
        self.embedding_service = EmbeddingService(llm_provider=get_embedding_provider())

    async def generate_strategy(
        self,
        session: AsyncSession,
        description: str,
        coduri_critici: list[str],
        cod_cpv: Optional[str] = None,
        tip_procedura: Optional[str] = None,
        complet: Optional[str] = None,
        tip_contestatie: Optional[str] = None,
        valoare_estimata: Optional[float] = None,
    ) -> dict:
        """Generate a full contestation strategy.

        Returns dict with:
        - overall_assessment: overall probability and recommendation
        - per_criticism: list of per-criticism recommendations
        - legal_basis: aggregated legal references
        - precedents: relevant CNSC decisions
        - tactical_recommendations: practical advice
        """
        logger.info(
            "strategy_generate_start",
            codes=coduri_critici,
            cpv=cod_cpv,
            complet=complet,
            tip_procedura=tip_procedura,
        )

        # --- Phase 1: Gather historical statistics (all DB, sequential) ---
        stats = await self._gather_statistics(
            session, coduri_critici, cod_cpv, tip_procedura, complet,
        )

        # --- Phase 2: RAG search per criticism code (sequential DB, parallel LLM) ---
        # Phase 2a: sequential DB fetches
        per_code_context = {}
        for code in coduri_critici:
            label = CRITIQUE_LABELS.get(code, code)
            search_query = f"{label} achiziții publice {description[:2000]}"  # Embedding query
            query_vector = await self.embedding_service.embed_query(search_query)
            legislation = await self._search_legislation(session, search_query, query_vector)
            jurisprudence = await self._search_jurisprudence(session, search_query, query_vector)
            per_code_context[code] = {
                "legislation": legislation,
                "jurisprudence": jurisprudence,
                "stats": stats.get(f"critica_{code}", {}),
                "label": label,
            }

        # Phase 2b: parallel LLM reasoning per criticism
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_GROUNDING)
        tasks = [
            self._reason_per_criticism(
                semaphore, code, per_code_context[code], description,
                cod_cpv, tip_procedura, complet,
            )
            for code in coduri_critici
        ]
        per_criticism_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out errors
        recommendations = []
        for i, result in enumerate(per_criticism_results):
            if isinstance(result, Exception):
                logger.warning("strategy_criticism_failed", code=coduri_critici[i], error=str(result))
                recommendations.append({
                    "code": coduri_critici[i],
                    "label": CRITIQUE_LABELS.get(coduri_critici[i], coduri_critici[i]),
                    "recommendation": "Nu am putut genera recomandarea — reîncearcă.",
                    "success_probability": None,
                    "legal_references": [],
                    "precedents": [],
                })
            else:
                recommendations.append(result)

        # --- Phase 3: Overall strategy synthesis ---
        overall = await self._synthesize_overall(
            description, recommendations, stats, cod_cpv, tip_procedura, complet,
            tip_contestatie, valoare_estimata,
        )

        # Collect all unique legal references and precedents
        all_legal = []
        all_precedents = []
        seen_legal = set()
        seen_prec = set()
        for rec in recommendations:
            for ref in rec.get("legal_references", []):
                key = ref.get("citare", ref.get("text", ""))
                if key and key not in seen_legal:
                    seen_legal.add(key)
                    all_legal.append(ref)
            for prec in rec.get("precedents", []):
                bo = prec.get("bo_reference", "")
                if bo and bo not in seen_prec:
                    seen_prec.add(bo)
                    all_precedents.append(prec)

        result = {
            "overall_assessment": overall,
            "per_criticism": recommendations,
            "legal_basis": all_legal[:20],
            "precedents": all_precedents[:15],
            "statistics": stats,
            "input": {
                "description": description,
                "coduri_critici": coduri_critici,
                "cod_cpv": cod_cpv,
                "tip_procedura": tip_procedura,
                "complet": complet,
                "tip_contestatie": tip_contestatie,
                "valoare_estimata": valoare_estimata,
            },
        }

        logger.info(
            "strategy_generate_complete",
            codes=coduri_critici,
            recommendations=len(recommendations),
            legal_refs=len(all_legal),
            precedents=len(all_precedents),
        )
        return result

    # =========================================================================
    # Statistics gathering
    # =========================================================================

    async def _gather_statistics(
        self,
        session: AsyncSession,
        coduri_critici: list[str],
        cod_cpv: Optional[str],
        tip_procedura: Optional[str],
        complet: Optional[str],
    ) -> dict:
        """Gather historical win rates for all input dimensions."""
        stats = {}
        pe_fond = not_(and_(
            DecizieCNSC.solutie_contestatie == "RESPINS",
            DecizieCNSC.motiv_respingere.in_(PROCEDURAL_REJECTIONS),
        ))

        # Per-criticism code (from ArgumentareCritica, pe fond)
        for code in coduri_critici:
            q = await session.execute(
                select(
                    ArgumentareCritica.castigator_critica,
                    func.count().label("cnt"),
                )
                .join(DecizieCNSC, ArgumentareCritica.decizie_id == DecizieCNSC.id)
                .where(and_(ArgumentareCritica.cod_critica == code, pe_fond))
                .group_by(ArgumentareCritica.castigator_critica)
            )
            code_stats = {r.castigator_critica: r.cnt for r in q}
            total = sum(code_stats.values())
            contest_wins = code_stats.get("contestator", 0)
            partial = code_stats.get("partial", 0)
            stats[f"critica_{code}"] = {
                "total": total,
                "contestator_wins": contest_wins,
                "autoritate_wins": code_stats.get("autoritate", 0),
                "partial": partial,
                "win_rate": round((contest_wins + partial) / total * 100, 1) if total > 0 else 0,
            }

        # Helper for decision-level stats
        async def _dec_stat(extra_filter, key: str):
            q = await session.execute(
                select(
                    DecizieCNSC.solutie_contestatie,
                    func.count().label("cnt"),
                )
                .where(and_(extra_filter, DecizieCNSC.solutie_contestatie.isnot(None), pe_fond))
                .group_by(DecizieCNSC.solutie_contestatie)
            )
            s = {r.solutie_contestatie: r.cnt for r in q}
            t = sum(s.values())
            w = s.get("ADMIS", 0) + s.get("ADMIS_PARTIAL", 0)
            stats[key] = {"total": t, "win_rate": round(w / t * 100, 1) if t > 0 else 0}

        if cod_cpv:
            await _dec_stat(DecizieCNSC.cod_cpv.startswith(cod_cpv[:3]), "cpv_domain")
        if complet:
            await _dec_stat(DecizieCNSC.complet == complet.upper(), "panel")
        if tip_procedura:
            await _dec_stat(DecizieCNSC.tip_procedura == tip_procedura, "procedure")

        # Global pe fond stats
        q = await session.execute(
            select(func.count()).select_from(DecizieCNSC)
            .where(and_(DecizieCNSC.solutie_contestatie.isnot(None), pe_fond))
        )
        total_global = q.scalar() or 1
        q2 = await session.execute(
            select(func.count()).select_from(DecizieCNSC)
            .where(and_(DecizieCNSC.solutie_contestatie.in_(["ADMIS", "ADMIS_PARTIAL"]), pe_fond))
        )
        wins_global = q2.scalar() or 0
        stats["global"] = {
            "total": total_global,
            "win_rate": round(wins_global / total_global * 100, 1),
        }

        return stats

    # =========================================================================
    # RAG search
    # =========================================================================

    async def _search_legislation(
        self, session: AsyncSession, query_text: str, query_vector=None, limit: int = 5,
    ) -> list[dict]:
        """Search relevant legislation fragments."""
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

        return [
            {
                "citare": row.LegislatieFragment.citare,
                "text": row.LegislatieFragment.text_fragment,
                "act": row.ActNormativ.denumire,
                "distance": round(row.distance, 3),
            }
            for row in rows if row.distance < 0.75
        ]

    async def _search_jurisprudence(
        self, session: AsyncSession, query_text: str, query_vector=None, limit: int = 10,
    ) -> list[dict]:
        """Search relevant CNSC decisions."""
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

        relevant = [(row.ArgumentareCritica, row.distance) for row in rows if row.distance < 0.65]
        if not relevant:
            return []

        # Load parent decisions
        from sqlalchemy.orm import defer
        dec_ids = list({arg.decizie_id for arg, _ in relevant})
        dec_result = await session.execute(
            select(DecizieCNSC).options(defer(DecizieCNSC.text_integral)).where(DecizieCNSC.id.in_(dec_ids))
        )
        dec_map = {str(d.id): d for d in dec_result.scalars().all()}

        precedents = []
        for arg, dist in relevant[:8]:
            dec = dec_map.get(str(arg.decizie_id))
            if dec:
                precedents.append({
                    "bo_reference": f"BO{dec.an_bo}_{dec.numar_bo}",
                    "complet": dec.complet,
                    "solutie": dec.solutie_contestatie,
                    "cod_critica": arg.cod_critica,
                    "castigator": arg.castigator_critica,
                    "argumentatie_cnsc": arg.argumentatie_cnsc or "",
                    "distance": round(dist, 3),
                })
        return precedents

    # =========================================================================
    # Per-criticism LLM reasoning
    # =========================================================================

    async def _reason_per_criticism(
        self,
        semaphore: asyncio.Semaphore,
        code: str,
        context: dict,
        description: str,
        cod_cpv: Optional[str],
        tip_procedura: Optional[str],
        complet: Optional[str],
    ) -> dict:
        """Generate recommendation for a single criticism code."""
        async with semaphore:
            stats = context["stats"]
            label = context["label"]

            # Format legislation context
            leg_text = ""
            legal_refs = []
            for leg in context["legislation"]:
                leg_text += f"- {leg['citare']} ({leg['act']}): {leg['text']}\n"
                legal_refs.append(leg)

            # Format jurisprudence context
            jur_text = ""
            prec_list = []
            for prec in context["jurisprudence"]:
                outcome = "câștigat de contestator" if prec["castigator"] == "contestator" else f"câștigat de {prec['castigator']}"
                jur_text += f"- {prec['bo_reference']} ({prec['solutie']}, {outcome}): {prec['argumentatie_cnsc']}\n"
                prec_list.append(prec)

            # Stats context
            stats_text = f"Rata de câștig contestator pe critica {code}: {stats.get('win_rate', 'N/A')}% ({stats.get('total', 0)} cazuri pe fond)"

            prompt = f"""Ești un avocat expert în contestații achiziții publice din România.

CRITICA: {code} — {label}

CONTEXTUL CAZULUI:
{description}
{f"CPV: {cod_cpv}" if cod_cpv else ""}
{f"Tip procedură: {tip_procedura}" if tip_procedura else ""}
{f"Complet CNSC: {complet}" if complet else ""}

STATISTICI ISTORICE:
{stats_text}

LEGISLAȚIE RELEVANTĂ:
{leg_text or "Nu s-au găsit fragmente legislative relevante."}

JURISPRUDENȚĂ CNSC RELEVANTĂ:
{jur_text or "Nu s-au găsit decizii relevante."}

Pe baza contextului de mai sus, generează o RECOMANDARE STRATEGICĂ pentru critica {code}:

1. **Evaluare** (1-2 propoziții): Merită contestată această critică? De ce?
2. **Argumente cheie** (2-4 puncte): Ce argumente concrete să folosească contestatorul?
3. **Temei legal**: Ce articole de lege susțin poziția?
4. **Jurisprudență utilă**: Ce decizii CNSC sprijină argumentația?
5. **Riscuri**: Ce contraargumente ar putea invoca autoritatea contractantă?
6. **Probabilitate succes**: Estimare procentuală bazată pe statistici și context.

Răspunde în format JSON strict:
{{
  "evaluare": "...",
  "argumente": ["arg1", "arg2", ...],
  "temei_legal": ["art. X din Legea Y", ...],
  "jurisprudenta_utila": ["BO2025_xxx — context", ...],
  "riscuri": ["risc1", ...],
  "probabilitate_succes": 65
}}"""

            try:
                response = await asyncio.wait_for(
                    self.llm.complete(prompt, temperature=0.2, max_tokens=2000),
                    timeout=LLM_CALL_TIMEOUT,
                )

                # Parse JSON from response
                parsed = self._extract_json(response)

                return {
                    "code": code,
                    "label": label,
                    "success_probability": parsed.get("probabilitate_succes", stats.get("win_rate")),
                    "recommendation": parsed.get("evaluare", response[:500]),
                    "arguments": parsed.get("argumente", []),
                    "legal_basis": parsed.get("temei_legal", []),
                    "useful_precedents": parsed.get("jurisprudenta_utila", []),
                    "risks": parsed.get("riscuri", []),
                    "legal_references": legal_refs,
                    "precedents": prec_list,
                    "stats": stats,
                }
            except asyncio.TimeoutError:
                raise TimeoutError(f"Timeout la generarea strategiei pentru {code}")

    # =========================================================================
    # Overall synthesis
    # =========================================================================

    async def _synthesize_overall(
        self,
        description: str,
        recommendations: list[dict],
        stats: dict,
        cod_cpv: Optional[str],
        tip_procedura: Optional[str],
        complet: Optional[str],
        tip_contestatie: Optional[str],
        valoare_estimata: Optional[float],
    ) -> dict:
        """Synthesize overall strategy from per-criticism recommendations."""

        # Calculate weighted average probability
        probs = [r["success_probability"] for r in recommendations if r.get("success_probability") is not None]
        avg_prob = round(sum(probs) / len(probs), 1) if probs else 50.0

        # Build synthesis prompt
        recs_summary = ""
        for rec in recommendations:
            prob = rec.get("success_probability", "N/A")
            recs_summary += f"- {rec['code']} ({rec['label']}): {prob}% — {rec.get('recommendation', 'N/A')}\n"

        global_stats = stats.get("global", {})
        panel_stats = stats.get("panel", {})
        cpv_stats = stats.get("cpv_domain", {})

        prompt = f"""Ești un avocat expert în contestații achiziții publice din România.

CAZUL:
{description}
{f"CPV: {cod_cpv}" if cod_cpv else ""}
{f"Procedură: {tip_procedura}" if tip_procedura else ""}
{f"Complet: {complet}" if complet else ""}
{f"Tip contestație: {tip_contestatie}" if tip_contestatie else ""}
{f"Valoare estimată: {valoare_estimata:,.0f} RON" if valoare_estimata else ""}

EVALUĂRI PER CRITICĂ:
{recs_summary}

STATISTICI GLOBALE:
- Rata generală admitere CNSC (pe fond): {global_stats.get('win_rate', 'N/A')}%
{f"- Completul {complet}: {panel_stats.get('win_rate', 'N/A')}% din {panel_stats.get('total', 0)} decizii" if panel_stats else ""}
{f"- Domeniu CPV: {cpv_stats.get('win_rate', 'N/A')}% din {cpv_stats.get('total', 0)} decizii" if cpv_stats else ""}

Generează o SINTEZĂ STRATEGICĂ:

1. **Recomandare generală**: Merită depusă contestația? (DA/NU/CONDIȚIONAT)
2. **Probabilitate globală de succes**: Procentaj ponderat
3. **Critici prioritare**: Care critici au cele mai mari șanse? Ordinea de impact.
4. **Abordare tactică**: Cum să structureze contestația pentru impact maxim?
5. **Atenționări**: Termene, riscuri procedurale, aspecte de luat în calcul.

Răspunde concis, practic, structurat. Maximum 500 cuvinte."""

        try:
            response = await asyncio.wait_for(
                self.llm.complete(prompt, temperature=0.2, max_tokens=2000),
                timeout=LLM_CALL_TIMEOUT,
            )
            return {
                "text": response,
                "overall_probability": avg_prob,
                "recommendation": "ADMIS" if avg_prob >= 50 else "RESPINS",
                "confidence": round(abs(avg_prob - 50) / 50, 2),
            }
        except Exception as e:
            logger.warning("strategy_synthesis_failed", error=str(e))
            return {
                "text": "Nu am putut genera sinteza strategică.",
                "overall_probability": avg_prob,
                "recommendation": "ADMIS" if avg_prob >= 50 else "RESPINS",
                "confidence": 0.0,
            }

    # =========================================================================
    # Utilities
    # =========================================================================

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Extract JSON object from LLM response text."""
        # Try to find JSON block
        import re
        # Match ```json ... ``` or { ... }
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try raw JSON
        brace_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return {}
