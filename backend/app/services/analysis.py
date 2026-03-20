"""Decision analysis service for ExpertAP.

Uses Gemini LLM to extract structured ArgumentareCritica records
from raw CNSC decision text. This fills the gap between:
  raw text_integral → ArgumentareCritica rows → embeddings → RAG

Each decision is analyzed to extract per-criticism argumentation:
- Contestant arguments
- Authority (AC) arguments
- CNSC analysis and reasoning
- Winner per criticism
"""

import json
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.decision import DecizieCNSC, ArgumentareCritica
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider

logger = get_logger(__name__)

ANALYSIS_SYSTEM_PROMPT = """Ești un analist juridic expert în achiziții publice românești, specializat în deciziile CNSC.

Sarcina ta: Analizează textul integral al unei decizii CNSC și extrage:
1. Un REZUMAT scurt al deciziei (2-3 propoziții)
2. Codul CPV sugerat (dacă nu este deja specificat)
3. Argumentația structurată per critică (per motiv de contestare)

Returnează un obiect JSON cu structura de mai jos.

REZUMAT (rezumat):
- 2-3 propoziții concise care descriu: cine a contestat, ce obiect are contractul, care este soluția CNSC
- Exemplu: "Contestatorul SC Alfa SRL a contestat rezultatul procedurii de atribuire a contractului de furnizare echipamente medicale, susținând că oferta sa a fost respinsă nejustificat ca neconformă. CNSC a admis contestația, constatând că autoritatea contractantă nu a solicitat clarificări conform art. 209 din Legea 98/2016."

CPV SUGERAT (cpv_sugerat):
- Dacă decizia NU are cod CPV specificat, identifică obiectul contractului și sugerează codul CPV cel mai probabil (format: "XXXXXXXX-X", ex: "33100000-1")
- Dacă decizia ARE deja cod CPV, returnează null
- Bazează-te pe obiectul contractului menționat în text

REGULI CRITICE PENTRU CRITICI:
- cod_critica TREBUIE să fie un cod scurt de max 10 caractere (ex: "R2", "D1", "D4", "R1"). NU include descrieri sau paranteze. Dacă o critică acoperă mai multe coduri, folosește codul principal (ex: "R2" nu "R2, R3, R4 (Tardivitate)"). Dacă nu există cod explicit, folosește "C1", "C2" etc.
- Fiecare critică separată = un obiect separat în array. NU combina criticile într-un singur obiect.
- Extrage TOATE argumentele relevante, nu doar primele rânduri
- Fiecare câmp text trebuie să fie un rezumat substanțial (minim 200 de cuvinte dacă informația există)
- castigator_critica trebuie să fie unul din: "contestator", "autoritate", "partial", "unknown"

ARGUMENTELE CONTESTATORULUI (argumente_contestator):
- Include TOATE motivele de fapt invocate de contestator
- Include TOATE motivele de drept (articole de lege, directive europene)
- Păstrează structura logică a argumentației

JURISPRUDENȚA CONTESTATORULUI (jurisprudenta_contestator):
- Extrage FIECARE referință la jurisprudență invocată de contestator
- Include: decizii ale Curților de Apel, decizii CJUE, alte decizii CNSC, directive europene
- Format exact cum apare în text (ex: "cauza C-927/19 CJUE", "Decizia nr. 506/2023 a Curții de Apel Alba Iulia")
- Dacă nu există jurisprudență invocată, returnează array gol []

ARGUMENTELE AUTORITĂȚII CONTRACTANTE (argumente_ac):
- Include toate contra-argumentele AC
- Include referințele la legislație pe care AC le invocă

JURISPRUDENȚA AC (jurisprudenta_ac):
- Extrage referințele la jurisprudență invocate de AC
- Același format ca la contestator

ARGUMENTE INTERVENIENȚI (argumente_intervenienti):
- Dacă există intervenienți, pentru fiecare extrage argumentele și jurisprudența separată
- Format: [{"nr": 1, "argumente": "...", "jurisprudenta": ["referință 1", "referință 2"]}]
- Dacă nu există intervenienți, returnează null

ANALIZA CNSC:
- elemente_retinute_cnsc: Toate constatările, dovezile și elementele de fapt reținute de Consiliu
- argumentatie_cnsc: Raționamentul COMPLET al CNSC, inclusiv articolele de lege aplicate
- jurisprudenta_cnsc: Referințe la jurisprudență invocate de CNSC în motivarea sa (decizii instanțe, CJUE, etc.)

Format JSON strict (fără alte texte înainte sau după JSON):
{
  "rezumat": "Rezumat concis al deciziei în 2-3 propoziții...",
  "cpv_sugerat": "XXXXXXXX-X sau null dacă CPV-ul este deja cunoscut",
  "critici": [
    {
      "cod_critica": "R2",
      "ordine_in_decizie": 1,
      "argumente_contestator": "Rezumat detaliat al argumentelor contestatorului...",
      "jurisprudenta_contestator": ["cauza C-927/19 CJUE", "Decizia Curții de Apel Alba Iulia nr. 506/2023"],
      "argumente_ac": "Rezumat detaliat al argumentelor autorității contractante...",
      "jurisprudenta_ac": ["Decizia CNSC nr. 123/2024"],
      "argumente_intervenienti": [{"nr": 1, "argumente": "...", "jurisprudenta": ["..."]}],
      "elemente_retinute_cnsc": "Elementele de fapt și de drept reținute de CNSC...",
      "argumentatie_cnsc": "Raționamentul și motivarea CNSC, cu referiri la articolele de lege...",
      "jurisprudenta_cnsc": ["cauza C-285/18 CJUE", "Directiva 89/665/CEE"],
      "castigator_critica": "contestator"
    }
  ]
}"""

ANALYSIS_PROMPT_TEMPLATE = """Analizează următoarea decizie CNSC și extrage argumentația structurată.

Decizia: {external_id}
Coduri critici din filename: {coduri_critici}
Tip contestație: {tip_contestatie}
Soluție: {solutie}
Cod CPV cunoscut: {cod_cpv}

TEXT INTEGRAL:
{text_integral}

Returnează DOAR JSON-ul structurat (rezumat + cpv_sugerat + critici), fără alte explicații."""


class DecisionAnalysisService:
    """Service for extracting structured argumentation from decision text."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm = llm_provider or get_llm_provider()

    async def analyze_decision(
        self,
        decision: DecizieCNSC,
    ) -> tuple[list[dict], list[str], dict]:
        """Analyze a single decision and extract argumentation.

        Args:
            decision: The decision to analyze.

        Returns:
            Tuple of (list of argumentation dicts, list of warnings, decision_metadata).
            decision_metadata may contain 'rezumat' and 'cpv_sugerat'.
        """
        text_len = len(decision.text_integral) if decision.text_integral else 0
        was_truncated = text_len > 3500000

        logger.info(
            "analyzing_decision",
            external_id=decision.external_id,
            text_length=text_len,
            truncated=was_truncated,
        )

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            external_id=decision.external_id,
            coduri_critici=", ".join(decision.coduri_critici) if decision.coduri_critici else "N/A",
            tip_contestatie=decision.tip_contestatie,
            solutie=decision.solutie_contestatie or "N/A",
            cod_cpv=decision.cod_cpv or "NECUNOSCUT — te rog sugerează unul",
            text_integral=decision.text_integral[:3500000],  # Gemini 2.5 Pro: 1M tokens, 3.5M chars ≈ 875K tokens (leaves room for prompt + output)
        )

        try:
            response = await self.llm.complete(
                prompt=prompt,
                system_prompt=ANALYSIS_SYSTEM_PROMPT,
                temperature=0.05,
                max_tokens=65536,
            )

            # Parse JSON from response
            argumentari, warnings, decision_metadata = self._parse_response(response)

            logger.info(
                "decision_analyzed",
                external_id=decision.external_id,
                critici_found=len(argumentari),
                has_rezumat=bool(decision_metadata.get("rezumat")),
                has_cpv_sugerat=bool(decision_metadata.get("cpv_sugerat")),
                warnings=warnings if warnings else None,
            )
            return argumentari, warnings, decision_metadata

        except Exception as e:
            logger.error(
                "decision_analysis_failed",
                external_id=decision.external_id,
                error=str(e),
            )
            raise

    def _parse_response(self, response: str) -> tuple[list[dict], list[str], dict]:
        """Parse LLM response into list of argumentation dicts + decision-level metadata.

        Supports two JSON formats:
        - New format: {"rezumat": "...", "cpv_sugerat": "...", "critici": [...]}
        - Legacy format: [{critica1}, {critica2}] (backward-compatible)

        If JSON is truncated (e.g. due to max_tokens), recovers complete
        objects and returns them with warnings about what was lost.

        Returns:
            Tuple of (parsed critici, warning messages, decision_metadata).
            decision_metadata may contain 'rezumat' and 'cpv_sugerat'.
            Raises only if zero objects can be recovered.
        """
        warnings = []
        decision_metadata = {}

        # Strip markdown code fences if present
        text = response.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            # JSON truncat — recuperăm obiectele complete
            recovered = self._recover_json_objects(text)
            if recovered:
                recovered_codes = [r.get("cod_critica", "?") for r in recovered]
                warning_msg = (
                    f"JSON trunchiat la {len(text)} caractere. "
                    f"Recuperate {len(recovered)} critici ({', '.join(recovered_codes)}). "
                    f"Critici trunchiate/pierdute după ultima completă. "
                    f"Eroare: {e}"
                )
                warnings.append(warning_msg)
                logger.warning(
                    "json_truncated_partial_save",
                    recovered_count=len(recovered),
                    recovered_critici=recovered_codes,
                    original_error=str(e),
                    response_length=len(text),
                )
                parsed = recovered
            else:
                logger.error(
                    "json_truncated_no_recovery",
                    original_error=str(e),
                    response_length=len(text),
                    response_tail=text[-200:] if len(text) > 200 else text,
                )
                raise

        # Handle new format: {"rezumat": "...", "cpv_sugerat": "...", "critici": [...]}
        if isinstance(parsed, dict) and "critici" in parsed:
            if parsed.get("rezumat"):
                decision_metadata["rezumat"] = str(parsed["rezumat"])
            if parsed.get("cpv_sugerat"):
                decision_metadata["cpv_sugerat"] = str(parsed["cpv_sugerat"])
            parsed = parsed["critici"]
        # Legacy format: single object without "critici" key
        elif isinstance(parsed, dict):
            parsed = [parsed]

        if not isinstance(parsed, list):
            raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")

        # Validate required fields
        valid_items = []
        for item in parsed:
            if "cod_critica" not in item:
                warnings.append(f"Obiect JSON ignorat — lipsește cod_critica: {str(item)[:100]}")
                continue
            # Normalize castigator
            castigator = item.get("castigator_critica", "unknown")
            if castigator not in ("contestator", "autoritate", "partial", "unknown"):
                item["castigator_critica"] = "unknown"
            valid_items.append(item)

        if not valid_items:
            raise ValueError("No valid argumentation objects found in response")

        return valid_items, warnings, decision_metadata

    @staticmethod
    def _recover_json_objects(text: str) -> list[dict]:
        """Extract complete JSON objects from a truncated JSON array.

        Walks through the text tracking brace depth to find complete {...}
        pairs. Recovered objects are saved; truncated ones are logged as warnings.
        """
        objects = []
        i = 0
        while i < len(text):
            obj_start = text.find("{", i)
            if obj_start == -1:
                break
            depth = 0
            in_string = False
            escape_next = False
            obj_end = -1
            for j in range(obj_start, len(text)):
                ch = text[j]
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    if in_string:
                        escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        obj_end = j
                        break
            if obj_end == -1:
                break
            try:
                obj = json.loads(text[obj_start:obj_end + 1])
                if isinstance(obj, dict) and "cod_critica" in obj:
                    objects.append(obj)
            except json.JSONDecodeError:
                pass
            i = obj_end + 1
        return objects

    async def analyze_and_store(
        self,
        session: AsyncSession,
        decision: DecizieCNSC,
        overwrite: bool = False,
    ) -> int:
        """Analyze decision and store ArgumentareCritica records.

        Args:
            session: Database session.
            decision: The decision to analyze.
            overwrite: If True, delete existing records and re-analyze.

        Returns:
            Number of ArgumentareCritica records created.
        """
        # Check if already analyzed
        existing_count = await session.scalar(
            select(func.count())
            .select_from(ArgumentareCritica)
            .where(ArgumentareCritica.decizie_id == decision.id)
        )

        if existing_count and existing_count > 0 and not overwrite:
            logger.info(
                "decision_already_analyzed",
                external_id=decision.external_id,
                existing_count=existing_count,
            )
            return 0

        if overwrite and existing_count:
            # Delete existing
            from sqlalchemy import delete
            await session.execute(
                delete(ArgumentareCritica).where(
                    ArgumentareCritica.decizie_id == decision.id
                )
            )

        # Analyze with LLM
        argumentari, analysis_warnings, decision_metadata = await self.analyze_decision(decision)

        # Populate decision-level fields from LLM response
        if decision_metadata.get("rezumat"):
            decision.rezumat = decision_metadata["rezumat"]
            logger.info(
                "rezumat_saved",
                external_id=decision.external_id,
                rezumat_length=len(decision_metadata["rezumat"]),
            )

        if decision_metadata.get("cpv_sugerat") and not decision.cod_cpv:
            import re
            cpv_match = re.match(r"^\d{8}-\d$", decision_metadata["cpv_sugerat"])
            if cpv_match:
                decision.cod_cpv = decision_metadata["cpv_sugerat"]
                decision.cpv_source = "dedus"
                logger.info(
                    "cpv_deduced_from_analysis",
                    external_id=decision.external_id,
                    cpv_sugerat=decision_metadata["cpv_sugerat"],
                )

        # Store warnings on the decision record if any
        if analysis_warnings:
            existing_warnings = decision.parse_warnings or []
            # Prefix analysis warnings to distinguish from import warnings
            new_warnings = [f"[ANALYSIS] {w}" for w in analysis_warnings]
            decision.parse_warnings = existing_warnings + new_warnings
            logger.warning(
                "decision_analysis_warnings_saved",
                external_id=decision.external_id,
                warnings=analysis_warnings,
            )

        # Create records
        created = 0
        for item in argumentari:
            # Extract jurisprudence arrays (normalize to list of strings)
            jp_contestator = item.get("jurisprudenta_contestator") or []
            jp_ac = item.get("jurisprudenta_ac") or []
            jp_cnsc = item.get("jurisprudenta_cnsc") or []

            # Ensure they are lists of strings
            if not isinstance(jp_contestator, list):
                jp_contestator = []
            if not isinstance(jp_ac, list):
                jp_ac = []
            if not isinstance(jp_cnsc, list):
                jp_cnsc = []

            # Extract intervenients (normalize to JSON-compatible dict/list)
            intervenienti = item.get("argumente_intervenienti")
            if intervenienti and not isinstance(intervenienti, list):
                intervenienti = None

            # Truncate VARCHAR fields to match DB column limits
            cod_critica = str(item.get("cod_critica", "UNKNOWN"))[:10]
            castigator = str(item.get("castigator_critica", "unknown"))[:20]

            arg = ArgumentareCritica(
                decizie_id=decision.id,
                cod_critica=cod_critica,
                ordine_in_decizie=item.get("ordine_in_decizie"),
                argumente_contestator=item.get("argumente_contestator"),
                jurisprudenta_contestator=jp_contestator if jp_contestator else None,
                argumente_ac=item.get("argumente_ac"),
                jurisprudenta_ac=jp_ac if jp_ac else None,
                argumente_intervenienti=intervenienti,
                elemente_retinute_cnsc=item.get("elemente_retinute_cnsc"),
                argumentatie_cnsc=item.get("argumentatie_cnsc"),
                jurisprudenta_cnsc=jp_cnsc if jp_cnsc else None,
                castigator_critica=castigator,
            )
            session.add(arg)
            created += 1

        await session.flush()

        logger.info(
            "argumentari_stored",
            external_id=decision.external_id,
            count=created,
        )
        return created

    async def analyze_all_unprocessed(
        self,
        session: AsyncSession,
        limit: Optional[int] = None,
    ) -> dict:
        """Analyze all decisions that don't have ArgumentareCritica records.

        Args:
            session: Database session.
            limit: Max number of decisions to process.

        Returns:
            Stats dict with counts.
        """
        # Find decisions without argumentari
        subquery = (
            select(ArgumentareCritica.decizie_id)
            .distinct()
        )
        stmt = (
            select(DecizieCNSC)
            .where(DecizieCNSC.id.notin_(subquery))
            .order_by(DecizieCNSC.created_at.desc())
        )
        if limit:
            stmt = stmt.limit(limit)

        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        stats = {
            "total": len(decisions),
            "analyzed": 0,
            "failed": 0,
            "argumentari_created": 0,
            "errors": [],
        }

        logger.info("analyzing_unprocessed_decisions", count=len(decisions))

        for dec in decisions:
            try:
                count = await self.analyze_and_store(session, dec)
                stats["analyzed"] += 1
                stats["argumentari_created"] += count
                # Commit after each decision to avoid losing progress
                await session.commit()
            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append(f"{dec.external_id}: {str(e)}")
                logger.error(
                    "decision_analysis_failed",
                    external_id=dec.external_id,
                    error=str(e),
                )
                await session.rollback()

        logger.info("analysis_completed", **stats)
        return stats

    async def analyze_all(
        self,
        session: AsyncSession,
        limit: Optional[int] = None,
        overwrite: bool = False,
    ) -> dict:
        """Analyze all decisions, optionally overwriting existing records.

        Args:
            session: Database session.
            limit: Max number of decisions to process.
            overwrite: If True, re-analyze even already-analyzed decisions.

        Returns:
            Stats dict with counts.
        """
        stmt = (
            select(DecizieCNSC)
            .order_by(DecizieCNSC.created_at.desc())
        )
        if limit:
            stmt = stmt.limit(limit)

        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        stats = {
            "total": len(decisions),
            "analyzed": 0,
            "failed": 0,
            "argumentari_created": 0,
            "errors": [],
        }

        logger.info("analyzing_all_decisions", count=len(decisions), overwrite=overwrite)

        for dec in decisions:
            try:
                count = await self.analyze_and_store(session, dec, overwrite=overwrite)
                stats["analyzed"] += 1
                stats["argumentari_created"] += count
                await session.commit()
            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append(f"{dec.external_id}: {str(e)}")
                logger.error(
                    "decision_analysis_failed",
                    external_id=dec.external_id,
                    error=str(e),
                )
                await session.rollback()

        logger.info("analysis_completed", **stats)
        return stats
