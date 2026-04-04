"""Entity extraction service for procurement documents.

Uses LLM with structured JSON schema to extract metadata from uploaded
procurement documents: CPV code, procedure type, estimated value,
award criteria, deadlines, parties, and more.

This enables auto-populating forms in Drafter, Red Flags, Strategy, etc.
"""

import asyncio
import json
import re
from typing import Optional

from app.core.logging import get_logger
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider

logger = get_logger(__name__)

LLM_CALL_TIMEOUT = 90


class EntityExtractor:
    """Service for extracting structured metadata from procurement documents."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm = llm_provider or get_llm_provider()

    async def extract_entities(self, document_text: str) -> dict:
        """Extract structured metadata from a procurement document.

        Args:
            document_text: Full text of the procurement document.

        Returns:
            dict with extracted entities (all fields optional).
        """
        doc_excerpt = document_text  # Full text — LLM handles context limits
        logger.info("entity_extract_start", text_length=len(document_text))

        prompt = f"""Ești un expert în achiziții publice din România. Extrage metadatele structurate din următorul document de achiziție publică.

DOCUMENT:
{doc_excerpt}

Extrage DOAR informațiile care apar explicit în document. Nu inventa date.

Răspunde STRICT în JSON:
{{
  "cod_cpv": "cod CPV principal (ex: 45310000-3)" | null,
  "cpv_descriere": "descrierea codului CPV" | null,
  "tip_procedura": "licitație deschisă / licitație restrânsă / negociere / procedură simplificată / etc." | null,
  "criteriu_atribuire": "prețul cel mai scăzut / cel mai bun raport calitate-preț / costul cel mai scăzut" | null,
  "valoare_estimata": number (valoare în RON, fără TVA) | null,
  "moneda": "RON" | "EUR" | null,
  "termen_depunere": "data limită depunere oferte (format YYYY-MM-DD)" | null,
  "termen_contestare": "termenul de contestare (zile sau data)" | null,
  "autoritate_contractanta": "numele autorității contractante" | null,
  "obiect_contract": "descrierea scurtă a obiectului contractului" | null,
  "tip_contract": "furnizare / servicii / lucrări" | null,
  "numar_loturi": number | null,
  "garantie_participare": number (valoare garanție) | null,
  "durata_contract": "durata contractului (ex: 12 luni, 24 luni)" | null,
  "sursa_finantare": "buget propriu / fonduri europene / etc." | null,
  "articole_legale_menționate": ["art. X din Legea Y", ...] | []
}}

IMPORTANT: Returnează DOAR JSON valid, fără text suplimentar."""

        try:
            response = await asyncio.wait_for(
                self.llm.complete(prompt, temperature=0.0, max_tokens=1500),
                timeout=LLM_CALL_TIMEOUT,
            )
            entities = self._extract_json(response)
            # Clean up null values
            entities = {k: v for k, v in entities.items() if v is not None and v != [] and v != ""}

            logger.info("entity_extract_complete", fields_found=len(entities))
            return entities

        except asyncio.TimeoutError:
            logger.warning("entity_extract_timeout")
            return {"error": "Timeout la extragerea entităților"}
        except Exception as e:
            logger.error("entity_extract_error", error=str(e))
            return {"error": str(e)}

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Extract JSON object from LLM response."""
        match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {}
