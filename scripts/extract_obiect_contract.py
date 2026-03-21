#!/usr/bin/env python3
"""Retroactive extraction of obiect_contract for existing decisions.

Uses the same regex patterns from parser.py (zero LLM calls) to extract
the contract object description from text_integral for decisions that
were imported before this field existed.

Features:
- Skips decisions that already have obiect_contract (idempotent)
- Per-decision commit (crash-safe)
- Dry-run mode for review before applying
- Force mode to re-extract all

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
# Regex patterns (mirrored from backend/app/services/parser.py)
# ---------------------------------------------------------------------------

# Pattern 1: Quoted contract object near standard markers
_CONTRACT_OBJECT_QUOTED = re.compile(
    r"(?:având\s+ca\s+obiect|obiectul\s+(?:contractului|achiziției|acordului[\s-]+cadru)"
    r"|obiect\s+(?:al\s+)?(?:contractului|achiziției))"
    r"[:\s]*"
    "[\u201e\"«]"  # opening quote: „ " «
    r"(.{5,500}?)"
    "[\u201d\u201c\"»]",  # closing quote: " " " »
    re.IGNORECASE,
)

# Pattern 2: Unquoted text after specific markers
_CONTRACT_OBJECT_UNQUOTED = re.compile(
    r"(?:"
    r"obiectul\s+(?:contractului|achiziției|acordului[\s-]+cadru)"
    r"|obiect\s+(?:al\s+)?(?:contractului|achiziției)"
    r"|privind\s+(?:achiziția\s+de|furnizarea\s+de|prestarea\s+de|execuția)"
    r"|în\s+vederea\s+(?:încheierii|atribuirii)\s+(?:(?:unui\s+)?(?:acord|acordului)[\s-]+"
    r"cadru|contractului)(?:\s+(?:de|pentru)\s+)?"
    r"(?:furnizare|servicii|lucrări|prestare|execuția)?"
    r")"
    r"[:\s]+(.{10,300}?)"
    r"(?:,\s*(?:cod|CPV|finanțat|în\s+valoare|estimat|organizată|publicat|înregistrat|lotul|lot\s|loturile)"
    r"|\.\s|,\s+s-a\s+solicitat)",
    re.IGNORECASE,
)

# Pattern 3: Broadest fallback
_CONTRACT_OBJECT_FALLBACK = re.compile(
    r"având\s+ca\s+obiect[:\s]+"
    r"(.{10,200}?)"
    r"(?:,\s*(?:cod|CPV|s-a\s+solicitat|anunț|publicat)|[.]\s)",
    re.IGNORECASE,
)

# Prefixes to strip from extracted text (markers that leak into capture groups)
_PREFIX_RE = re.compile(
    r"^(?:având\s+ca\s+obiect|obiectul\s+(?:contractului|achiziției))"
    r"[:\s]*",
    re.IGNORECASE,
)


def _clean_prefix(text: str) -> str:
    """Strip leading marker phrases that sometimes leak into the capture group."""
    return _PREFIX_RE.sub("", text).strip() or text


def extract_obiect_contract(text: str) -> str | None:
    """Extract contract object from decision text using regex patterns.

    Searches the first 5000 characters for common patterns.
    Returns the extracted text or None if no pattern matched.
    """
    # Normalize whitespace for better regex matching
    intro = re.sub(r"\s+", " ", text[:5000])

    # Strategy 1: Quoted text near markers (most reliable)
    match = _CONTRACT_OBJECT_QUOTED.search(intro)
    if match:
        obj = match.group(1).strip()
        if len(obj) >= 5:
            return _clean_prefix(obj)

    # Strategy 2: Unquoted text after specific markers
    match = _CONTRACT_OBJECT_UNQUOTED.search(intro)
    if match:
        obj = match.group(1).strip()
        if len(obj) >= 10:
            return _clean_prefix(obj)

    # Strategy 3: Broadest fallback
    match = _CONTRACT_OBJECT_FALLBACK.search(intro)
    if match:
        obj = match.group(1).strip()
        obj = obj.lstrip('\u201e\u201c"«')
        if len(obj) >= 10:
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
                print(f"  [{i+1:4d}] {decision.external_id}: [{prefix}] {obj[:80]}")
                if old and args.force:
                    print(f"         was: {old[:80]}")

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
