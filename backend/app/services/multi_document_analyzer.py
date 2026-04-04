"""Multi-document analysis service for procurement dossiers.

Analyzes multiple uploaded documents together to identify:
1. Per-document red flags (via existing RedFlagsAnalyzer)
2. Cross-document inconsistencies (e.g., different criteria in fisa de date vs caiet de sarcini)
3. Missing required documents
4. Unified risk assessment

Architecture:
- Step 1: Extract text from each document
- Step 2: Run red flags analysis per document (parallel)
- Step 3: Cross-document consistency check (single LLM call with all doc summaries)
"""

import asyncio
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.document_processor import DocumentProcessor
from app.services.redflags_analyzer import RedFlagsAnalyzer
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider

logger = get_logger(__name__)

LLM_CALL_TIMEOUT = 180
MAX_DOCUMENTS = 5
MAX_DOC_CHARS = 30000


class MultiDocumentAnalyzer:
    """Service for analyzing multiple procurement documents together."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm = llm_provider or get_llm_provider()
        self.processor = DocumentProcessor()

    async def analyze(
        self,
        session: AsyncSession,
        documents: list[dict],
        use_jurisprudence: bool = True,
    ) -> dict:
        """Analyze multiple documents for red flags and cross-document issues.

        Args:
            session: Database session.
            documents: List of {filename: str, content: bytes, mime_type: str}.
            use_jurisprudence: Whether to ground findings in legislation/jurisprudence.

        Returns:
            dict with per_document results, cross_document issues, and unified_assessment.
        """
        if len(documents) > MAX_DOCUMENTS:
            documents = documents[:MAX_DOCUMENTS]

        logger.info("multi_doc_start", doc_count=len(documents))

        # --- Step 1: Extract text from each document ---
        extracted = []
        for doc in documents:
            text = self._extract_text(doc)
            if text and len(text.strip()) > 50:
                extracted.append({
                    "filename": doc.get("filename", "document"),
                    "text": text[:MAX_DOC_CHARS],
                    "word_count": len(text.split()),
                })
            else:
                extracted.append({
                    "filename": doc.get("filename", "document"),
                    "text": "",
                    "word_count": 0,
                    "error": "Nu s-a putut extrage text din document.",
                })

        # --- Step 2: Per-document red flags (parallel) ---
        analyzer = RedFlagsAnalyzer(llm_provider=self.llm)
        per_doc_tasks = []
        for doc in extracted:
            if doc["text"] and not doc.get("error"):
                per_doc_tasks.append(
                    self._analyze_single_doc(analyzer, session, doc, use_jurisprudence)
                )
            else:
                per_doc_tasks.append(asyncio.coroutine(lambda d=doc: {
                    "filename": d["filename"],
                    "flags": [],
                    "error": d.get("error", "Document gol"),
                })())

        per_doc_results = await asyncio.gather(*per_doc_tasks, return_exceptions=True)

        doc_analyses = []
        for i, result in enumerate(per_doc_results):
            if isinstance(result, Exception):
                logger.warning("multi_doc_analysis_failed", filename=extracted[i]["filename"], error=str(result))
                doc_analyses.append({
                    "filename": extracted[i]["filename"],
                    "word_count": extracted[i].get("word_count", 0),
                    "flags": [],
                    "flag_count": 0,
                    "error": str(result),
                })
            else:
                doc_analyses.append(result)

        # --- Step 3: Cross-document consistency check ---
        cross_issues = await self._check_cross_document(extracted, doc_analyses)

        # --- Step 4: Unified assessment ---
        total_flags = sum(d.get("flag_count", 0) for d in doc_analyses)
        critical_flags = sum(
            sum(1 for f in d.get("flags", []) if f.get("severity") == "CRITICAL")
            for d in doc_analyses
        )

        unified = await self._generate_unified_assessment(
            doc_analyses, cross_issues, total_flags, critical_flags,
        )

        result = {
            "per_document": doc_analyses,
            "cross_document_issues": cross_issues,
            "unified_assessment": unified,
            "total_documents": len(documents),
            "total_flags": total_flags,
            "critical_flags": critical_flags,
            "cross_issues_count": len(cross_issues),
        }

        logger.info(
            "multi_doc_complete",
            docs=len(documents), flags=total_flags, cross_issues=len(cross_issues),
        )
        return result

    def _extract_text(self, doc: dict) -> str:
        """Extract text from a document."""
        content = doc.get("content", b"")
        filename = doc.get("filename", "").lower()
        try:
            if filename.endswith(".pdf"):
                return self.processor.extract_text_from_pdf(content)
            elif filename.endswith((".docx", ".doc")):
                return self.processor.extract_text_from_docx(content)
            else:
                return self.processor.extract_text_from_txt(content)
        except Exception as e:
            logger.warning("multi_doc_extract_failed", filename=filename, error=str(e))
            return ""

    async def _analyze_single_doc(
        self, analyzer: RedFlagsAnalyzer, session: AsyncSession,
        doc: dict, use_jurisprudence: bool,
    ) -> dict:
        """Analyze a single document for red flags."""
        try:
            result = await asyncio.wait_for(
                analyzer.analyze(
                    text=doc["text"],
                    session=session,
                    use_jurisprudence=use_jurisprudence,
                ),
                timeout=LLM_CALL_TIMEOUT,
            )
            flags = result.get("flags", []) if isinstance(result, dict) else []
            return {
                "filename": doc["filename"],
                "word_count": doc.get("word_count", 0),
                "flags": flags,
                "flag_count": len(flags),
            }
        except Exception as e:
            return {
                "filename": doc["filename"],
                "word_count": doc.get("word_count", 0),
                "flags": [],
                "flag_count": 0,
                "error": str(e),
            }

    async def _check_cross_document(
        self, extracted: list[dict], analyses: list[dict],
    ) -> list[dict]:
        """Check for cross-document inconsistencies."""
        # Build summaries of each document
        doc_summaries = []
        for i, doc in enumerate(extracted):
            if not doc["text"]:
                continue
            flags_text = ""
            if i < len(analyses) and analyses[i].get("flags"):
                flags_text = "\n".join(
                    f"  - [{f.get('severity', '?')}] {f.get('issue', '')[:150]}"
                    for f in analyses[i]["flags"][:5]
                )
            summary = f"**{doc['filename']}** ({doc.get('word_count', 0)} cuvinte):\n"
            summary += f"Conținut: {doc['text'][:800]}\n"
            if flags_text:
                summary += f"Red flags identificate:\n{flags_text}\n"
            doc_summaries.append(summary)

        if len(doc_summaries) < 2:
            return []

        prompt = f"""Ești un expert în achiziții publice din România. Ai analizat un dosar de achiziție format din {len(doc_summaries)} documente.

DOCUMENTE:

{"---".join(doc_summaries)}

Identifică INCONSISTENȚE ÎNTRE DOCUMENTE:
1. Diferențe în cerințe de calificare (fișa de date vs caiet de sarcini)
2. Valori diferite (estimări, garanții, termene)
3. Criterii de evaluare contradictorii
4. Documente obligatorii lipsă din dosar
5. Referințe legislative inconsistente

Răspunde în JSON:
[
  {{
    "tip": "inconsistență" | "document_lipsă" | "contradicție",
    "severitate": "CRITICAL" | "MEDIUM" | "LOW",
    "descriere": "Descriere clară a problemei",
    "documente_implicate": ["doc1.pdf", "doc2.docx"],
    "recomandare": "Ce trebuie corectat"
  }}
]

Dacă nu există inconsistențe, returnează [] (array gol)."""

        try:
            response = await asyncio.wait_for(
                self.llm.complete(prompt, temperature=0.1, max_tokens=2000),
                timeout=LLM_CALL_TIMEOUT,
            )
            import json, re
            match = re.search(r'\[.*\]', response, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            return []
        except Exception as e:
            logger.warning("cross_document_check_failed", error=str(e))
            return []

    async def _generate_unified_assessment(
        self, analyses: list[dict], cross_issues: list[dict],
        total_flags: int, critical_flags: int,
    ) -> dict:
        """Generate unified risk assessment."""
        if total_flags == 0 and not cross_issues:
            return {
                "risk_level": "LOW",
                "text": "Dosarul nu prezintă probleme semnificative. Nu au fost identificate red flags sau inconsistențe între documente.",
            }

        risk_level = "CRITICAL" if critical_flags >= 3 or len(cross_issues) >= 2 else \
                     "HIGH" if critical_flags >= 1 or total_flags >= 5 else \
                     "MEDIUM" if total_flags >= 2 else "LOW"

        prompt = f"""Rezumă riscurile unui dosar de achiziție publică:
- {total_flags} red flags identificate ({critical_flags} critice)
- {len(cross_issues)} inconsistențe între documente
- Nivel risc: {risk_level}

Scrie 2-3 propoziții de evaluare generală. Concis, în română."""

        try:
            text = await asyncio.wait_for(
                self.llm.complete(prompt, temperature=0.2, max_tokens=300),
                timeout=60,
            )
            return {"risk_level": risk_level, "text": text}
        except Exception:
            return {
                "risk_level": risk_level,
                "text": f"Evaluare: {total_flags} probleme ({critical_flags} critice), {len(cross_issues)} inconsistențe. Nivel risc: {risk_level}.",
            }
