#!/usr/bin/env python3
"""Generate lightweight summaries for CNSC decisions that lack a rezumat.

This is a RETROACTIVE script for decisions already analyzed. It uses a
SHORT prompt (~5000 chars of text) to generate a 2-3 sentence summary,
consuming ~1600 tokens per decision vs ~500K for full re-analysis.

Features:
- Skips decisions that already have a rezumat (idempotent)
- Per-decision commit (crash-safe)
- Retry with exponential backoff on API errors
- Progress reporting

Usage:
    python scripts/generate_summaries.py                  # Summarize all without rezumat
    python scripts/generate_summaries.py --limit 10       # Test with 10 decisions
    python scripts/generate_summaries.py --force           # Re-summarize all
    python scripts/generate_summaries.py --dry-run         # Show what would be summarized
    python scripts/generate_summaries.py --provider gemini --model gemini-2.0-flash
"""

import asyncio
import argparse
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
from app.services.llm.base import ResourceExhaustedError

logger = get_logger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0
RATE_LIMIT_DELAY = 0.5  # Lighter than full analysis
CIRCUIT_BREAKER_THRESHOLD = 3

SUMMARY_PROMPT = """Rezumă în 2-3 propoziții concise următoarea decizie CNSC.
Precizează: cine a contestat, obiectul contractului, și soluția CNSC.

Decizia: {external_id}
Soluție: {solutie}
Contestator: {contestator}
Autoritate contractantă: {autoritate}

TEXT (primele 5000 caractere):
{text_intro}

Răspunde DOAR cu rezumatul, fără alte explicații sau prefixuri."""


async def generate_summary(llm, decision: DecizieCNSC) -> str | None:
    """Generate a short summary for a single decision."""
    prompt = SUMMARY_PROMPT.format(
        external_id=decision.external_id,
        solutie=decision.solutie_contestatie or "N/A",
        contestator=decision.contestator or "N/A",
        autoritate=decision.autoritate_contractanta or "N/A",
        text_intro=decision.text_integral[:5000] if decision.text_integral else "",
    )

    response = await llm.complete(
        prompt=prompt,
        system_prompt="Ești un analist juridic expert în achiziții publice românești.",
        temperature=0.1,
        max_tokens=500,
    )

    return response.strip() if response else None


async def summarize_with_retry(llm, session, decision_id: str, external_id: str) -> tuple[bool, str | None]:
    """Summarize a decision with retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            decision = await session.get(DecizieCNSC, decision_id)
            if not decision:
                return (False, f"{external_id}: not found")

            summary = await generate_summary(llm, decision)
            if summary:
                decision.rezumat = summary
                await session.commit()
                return (True, None)
            else:
                return (False, f"{external_id}: empty response")
        except ResourceExhaustedError:
            await session.rollback()
            raise
        except Exception as e:
            await session.rollback()
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY ** attempt
                logger.warning("summary_retry", external_id=external_id, attempt=attempt, delay=delay)
                await asyncio.sleep(delay)
            else:
                return (False, f"{external_id}: {e}")
    return (False, f"{external_id}: max retries exceeded")


async def main():
    parser = argparse.ArgumentParser(description="Generate lightweight summaries for CNSC decisions")
    parser.add_argument("--limit", type=int, help="Max decisions to process")
    parser.add_argument("--force", action="store_true", help="Re-summarize all decisions")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be summarized")
    parser.add_argument("--provider", type=str, help="LLM provider override")
    parser.add_argument("--model", type=str, help="Model override")
    parser.add_argument("--rate-limit", type=float, default=RATE_LIMIT_DELAY, help="Delay between decisions")
    args = parser.parse_args()

    await init_db()

    # Import LLM factory after init
    from app.services.llm.factory import get_llm_provider
    llm = get_llm_provider(provider_type=args.provider, model=args.model)

    async with db_session.async_session_factory() as session:
        # Find decisions needing summaries
        stmt = select(DecizieCNSC.id, DecizieCNSC.an_bo, DecizieCNSC.numar_bo, DecizieCNSC.rezumat)

        if not args.force:
            stmt = stmt.where(DecizieCNSC.rezumat.is_(None))

        stmt = stmt.order_by(DecizieCNSC.created_at.desc())
        if args.limit:
            stmt = stmt.limit(args.limit)

        result = await session.execute(stmt)
        decisions = list(result.all())

        print(f"\n{'='*60}")
        print(f"Decisions to summarize: {len(decisions)}")
        print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
        print(f"Force: {args.force}")
        print(f"{'='*60}\n")

        if args.dry_run:
            for i, (did, an, nr, rez) in enumerate(decisions[:20]):
                status = "HAS_REZUMAT" if rez else "NEEDS_REZUMAT"
                print(f"  [{i+1}] BO{an}_{nr} — {status}")
            if len(decisions) > 20:
                print(f"  ... and {len(decisions) - 20} more")
            return

        stats = {"total": len(decisions), "success": 0, "failed": 0, "errors": []}
        consecutive_failures = 0
        start_time = time.time()

        for i, (did, an, nr, rez) in enumerate(decisions):
            external_id = f"BO{an}_{nr}"

            try:
                success, error = await summarize_with_retry(llm, session, str(did), external_id)

                if success:
                    stats["success"] += 1
                    consecutive_failures = 0
                    logger.info("summary_generated", external_id=external_id, progress=f"{i+1}/{len(decisions)}")
                else:
                    stats["failed"] += 1
                    stats["errors"].append(error)
                    consecutive_failures += 1
                    logger.error("summary_failed", external_id=external_id, error=error)

            except ResourceExhaustedError:
                print(f"\n⚠ API quota exhausted at decision {i+1}/{len(decisions)}")
                stats["failed"] += len(decisions) - i
                break

            if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                print(f"\n⚠ Circuit breaker: {CIRCUIT_BREAKER_THRESHOLD} consecutive failures")
                stats["failed"] += len(decisions) - i - 1
                break

            if i < len(decisions) - 1:
                await asyncio.sleep(args.rate_limit)

        elapsed = time.time() - start_time
        rate = stats["success"] / (elapsed / 60) if elapsed > 0 else 0

        print(f"\n{'='*60}")
        print(f"SUMMARY GENERATION COMPLETE")
        print(f"  Success: {stats['success']}/{stats['total']}")
        print(f"  Failed:  {stats['failed']}")
        print(f"  Time:    {elapsed:.1f}s ({rate:.1f} decisions/min)")
        if stats["errors"]:
            print(f"  Errors:")
            for err in stats["errors"][:10]:
                print(f"    - {err}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
