#!/usr/bin/env python3
"""Retroactive extraction of obiect_contract for existing decisions.

Uses regex patterns (zero LLM calls) to extract the contract object
description from text_integral for decisions that were imported before
this field existed.

Features:
- Skips decisions that already have obiect_contract (idempotent)
- Per-decision commit (crash-safe)
- Dry-run mode for review before applying
- Force mode to re-extract all
- Appends CPV code when found near the contract object

Usage:
    python scripts/extract_obiect_contract.py                  # Extract for all missing
    python scripts/extract_obiect_contract.py --limit 10       # Test with 10
    python scripts/extract_obiect_contract.py --dry-run        # Preview without applying
    python scripts/extract_obiect_contract.py --force          # Re-extract all (overwrite)
"""

import asyncio
import argparse
import re
import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select, func
from app.core.logging import get_logger
from app.db.session import init_db
from app.db import session as db_session
from app.models.decision import DecizieCNSC

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for contract object extraction
# ---------------------------------------------------------------------------

# Marker phrases that precede the contract object
_MARKER = (
    r"(?:având\s+ca\s+obiect|obiectul\s+(?:contractului|achiziției|acordului[\s-]+cadru)"
    r"|obiect\s+(?:al\s+)?(?:contractului|achiziției))"
)

# Action words (nominative + genitive-dative forms)
_ACTION_WORDS = (
    r"(?:furnizare[ai]?|furnizării"
    r"|prestare[ai]?|prestării"
    r"|execuți[ae]|execuției"
    r"|achiziți[ae]i?|achiziției)"
)

# CPV suffix: "cod CPV 31681500-8 Aparate de reîncărcare (Rev. 2)"
# Description between CPV number and (Rev. N) is optional
_CPV_SUFFIX_RE = re.compile(
    r'(cod\s+CPV\s+\d{8}-\d'
    r'(?:[\s\-]+[^(,]{2,80}?)?'
    r'\s*\(Rev\.\s*\d+\))',
    re.IGNORECASE,
)

# --- Strategy 1: Quoted text near markers ---
# Separate patterns per quote type to handle nested quotes correctly.
# „..." can contain «...» inside without breaking.

# 1a: „..." Romanian quotes (U+201E ... U+201D)
_QUOTED_RO = re.compile(
    _MARKER + r'[:\s]*\u201e([^\u201d]{5,800}?)\u201d',
    re.IGNORECASE,
)
# 1b: "..." standard ASCII quotes
_QUOTED_STD = re.compile(
    _MARKER + r'[:\s]*"([^"]{5,800}?)"',
    re.IGNORECASE,
)
# 1c: «...» guillemets (rare as outer quotes)
_QUOTED_GUIL = re.compile(
    _MARKER + r'[:\s]*\u00ab([^\u00bb]{5,800}?)\u00bb',
    re.IGNORECASE,
)

_QUOTED_PATTERNS = [_QUOTED_RO, _QUOTED_STD, _QUOTED_GUIL]

# --- Strategy 2: Unquoted text after structural markers ---
# Terminators use lookahead so CPV appending works from match.end(1)
_UNQUOTED = re.compile(
    r"(?:"
    r"obiectul\s+(?:contractului|achiziției|acordului[\s-]+cadru)"
    r"|obiect\s+(?:al\s+)?(?:contractului|achiziției)"
    r"|privind\s+" + _ACTION_WORDS + r"(?:\s+(?:de|a))?"
    r"|în\s+vederea\s+(?:încheierii|atribuirii)\s+(?:(?:unui\s+)?(?:acord|acordului)[\s-]+"
    r"cadru|contractului)(?:\s+(?:de|pentru)\s+)?"
    r"(?:furnizare|servicii|lucrări|prestare|execuția)?"
    r")"
    r"[:\s]+(.{10,300}?)"
    r"(?=,?\s*cod\s+CPV"
    r"|,\s*(?:finanțat|în\s+valoare|estimat|organizată|publicat|înregistrat|lotul|lot\s|loturile)"
    r"|\.\s|,\s+s-a\s+solicitat)",
    re.IGNORECASE,
)

# --- Strategy 3: Action keyword after simple markers ---
# Captures: "având ca obiect furnizarea de servere..."
#           "privind prestării de servicii de pază..."
#           "pentru execuția de lucrări..."
_KEYWORD = re.compile(
    r"(?:având\s+ca\s+obiect|obiectul\s+contractului|privind|pentru)"
    r"[:\s]+"
    r"(" + _ACTION_WORDS + r"(?:\s+(?:de|a)\s+)?"
    r".{5,250}?)"
    r"(?=,?\s*cod\s+CPV"
    r"|,\s*(?:finanțat|în\s+valoare|estimat|organizată|publicat|înregistrat)"
    r"|\.\s|,\s+s-a\s+solicitat)",
    re.IGNORECASE,
)

# --- Strategy 4: Broadest fallback ---
_FALLBACK = re.compile(
    r"având\s+ca\s+obiect[:\s]+"
    r"(.{10,300}?)"
    r"(?=,?\s*cod\s+CPV|,\s*(?:s-a\s+solicitat|anunț|publicat)|[.]\s)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Prefixes to strip from extracted text (markers that leak into capture groups)
_PREFIX_RE = re.compile(
    r"^(?:având\s+ca\s+obiect|obiectul\s+(?:contractului|achiziției))"
    r"[:\s]*",
    re.IGNORECASE,
)


def _clean_prefix(text: str) -> str:
    """Strip leading marker phrases that sometimes leak into the capture group."""
    return _PREFIX_RE.sub("", text).strip() or text


def _try_append_cpv(intro: str, after_obj_pos: int, obj: str) -> str:
    """If a CPV code appears within 300 chars after the extracted text, append it."""
    remainder = intro[after_obj_pos:after_obj_pos + 300]
    m = _CPV_SUFFIX_RE.search(remainder)
    if m:
        return f"{obj}, {m.group(1)}"
    return obj


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_obiect_contract(text: str) -> str | None:
    """Extract contract object from decision text using regex patterns.

    Searches the first 5000 characters for common patterns.
    Returns the extracted text (with CPV if found nearby) or None.
    """
    # Normalize whitespace for better regex matching
    intro = re.sub(r"\s+", " ", text[:5000])

    # Strategy 1: Quoted text near markers (most reliable)
    # Each quote type handled separately to avoid nested-quote truncation
    for pat in _QUOTED_PATTERNS:
        match = pat.search(intro)
        if match:
            obj = match.group(1).strip()
            if len(obj) >= 5:
                obj = _try_append_cpv(intro, match.end(1), obj)
                return _clean_prefix(obj)

    # Strategy 2: Unquoted text after structural markers
    match = _UNQUOTED.search(intro)
    if match:
        obj = match.group(1).strip()
        if len(obj) >= 10:
            obj = _try_append_cpv(intro, match.end(1), obj)
            return _clean_prefix(obj)

    # Strategy 3: Action keyword after simple markers
    match = _KEYWORD.search(intro)
    if match:
        obj = match.group(1).strip()
        if len(obj) >= 10:
            obj = _try_append_cpv(intro, match.end(1), obj)
            return _clean_prefix(obj)

    # Strategy 4: Broadest fallback
    match = _FALLBACK.search(intro)
    if match:
        obj = match.group(1).strip()
        obj = obj.lstrip('\u201e\u201c"«')
        if len(obj) >= 10:
            obj = _try_append_cpv(intro, match.end(1), obj)
            return _clean_prefix(obj)

    return None


async def main():
    parser = argparse.ArgumentParser(
        description="Extract obiect_contract retroactively from text_integral"
    )
    parser.add_argument("--limit", type=int, help="Max decisions to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without applying")
    parser.add_argument("--force", action="store_true", help="Re-extract all (overwrite existing)")
    args = parser.parse_args()

    await init_db()

    async with db_session.async_session_factory() as session:
        # Count totals for context
        total_result = await session.execute(select(func.count(DecizieCNSC.id)))
        total_decisions = total_result.scalar()

        has_obiect_result = await session.execute(
            select(func.count(DecizieCNSC.id)).where(
                DecizieCNSC.obiect_contract.isnot(None),
                DecizieCNSC.obiect_contract != "",
            )
        )
        has_obiect = has_obiect_result.scalar()

        print(f"Total decisions: {total_decisions}")
        print(f"Already have obiect_contract: {has_obiect}")
        print(f"Missing obiect_contract: {total_decisions - has_obiect}")

        # Build query
        stmt = select(DecizieCNSC).order_by(DecizieCNSC.created_at.desc())
        if not args.force:
            stmt = stmt.where(
                (DecizieCNSC.obiect_contract.is_(None))
                | (DecizieCNSC.obiect_contract == "")
            )
        if args.limit:
            stmt = stmt.limit(args.limit)

        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        print(f"\n{'='*60}")
        print(f"Processing: {len(decisions)} decisions")
        print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
        print(f"Force: {args.force}")
        print(f"{'='*60}\n")

        stats = {"total": len(decisions), "extracted": 0, "no_match": 0, "no_text": 0}
        start_time = time.time()

        for i, decision in enumerate(decisions):
            if not decision.text_integral or len(decision.text_integral) < 50:
                stats["no_text"] += 1
                continue

            obj = extract_obiect_contract(decision.text_integral)

            if obj:
                old = decision.obiect_contract
                prefix = "OVERWRITE" if old else "NEW"
                print(f"  [{i+1:4d}] {decision.external_id}: [{prefix}] {obj[:120]}")
                if old and args.force:
                    print(f"         was: {old[:120]}")

                if not args.dry_run:
                    decision.obiect_contract = obj
                    await session.commit()

                stats["extracted"] += 1
            else:
                stats["no_match"] += 1
                if args.dry_run:
                    print(f"  [{i+1:4d}] {decision.external_id}: NO MATCH")

        elapsed = time.time() - start_time

        print(f"\n{'='*60}")
        print(f"EXTRACTION COMPLETE")
        print(f"  Extracted:  {stats['extracted']}/{stats['total']}")
        print(f"  No match:   {stats['no_match']} (regex didn't find pattern)")
        print(f"  No text:    {stats['no_text']} (text_integral missing/too short)")
        pct = (stats['extracted'] / stats['total'] * 100) if stats['total'] > 0 else 0
        print(f"  Success:    {pct:.1f}%")
        print(f"  Time:       {elapsed:.1f}s")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
