#!/usr/bin/env python3
"""Generate LLM analysis (ArgumentareCritica) for CNSC decisions.

Analyzes raw decision text using Gemini LLM to extract structured
per-criticism argumentation. This is a prerequisite for embedding
generation and RAG search.

Features:
- Skips already-analyzed decisions by default (idempotent)
- Per-decision commit (crash-safe, no progress lost)
- Retry with exponential backoff on API errors
- Rate limiting to respect Gemini API quotas
- Progress reporting

Usage:
    python scripts/generate_analysis.py                  # Analyze all unprocessed
    python scripts/generate_analysis.py --limit 10       # Test with 10 decisions
    python scripts/generate_analysis.py --force           # Re-analyze everything
    python scripts/generate_analysis.py --dry-run         # Show what would be analyzed
    python scripts/generate_analysis.py --decision BO2025_1076  # Re-analyze specific decision(s)
    python scripts/generate_analysis.py --reanalyze-incomplete  # Fix incomplete analyses
    python scripts/generate_analysis.py --reanalyze-incomplete --min-length 60000 --max-length 100000
    python scripts/generate_analysis.py --provider gemini --model gemini-2.5-pro
    python scripts/generate_analysis.py --provider groq --model llama-3.3-70b-versatile
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
from app.models.decision import DecizieCNSC, ArgumentareCritica
from app.services.analysis import DecisionAnalysisService

logger = get_logger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds
RATE_LIMIT_DELAY = 1.0  # seconds between decisions


async def analyze_with_retry(
    analysis_service: DecisionAnalysisService,
    session,
    decision_id: str,
    external_id: str,
    overwrite: bool = False,
) -> tuple[int, str | None]:
    """Analyze a single decision with retry logic.

    Args:
        decision_id: Pre-captured UUID string (safe to use after rollback).
        external_id: Pre-captured external_id string (safe to use after rollback).

    Returns:
        Tuple of (argumentari_created, error_message_or_none)
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Always fetch a fresh decision object for each attempt
            decision = await session.get(DecizieCNSC, decision_id)
            if not decision:
                return (0, f"{external_id}: decision not found")
            count = await analysis_service.analyze_and_store(
                session, decision, overwrite=overwrite
            )
            await session.commit()
            return (count, None)
        except Exception as e:
            error_msg = str(e)
            await session.rollback()
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY ** attempt
                logger.warning(
                    "analysis_retry",
                    external_id=external_id,
                    attempt=attempt,
                    delay=delay,
                    error=error_msg,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "analysis_failed_all_retries",
                    external_id=external_id,
                    error=error_msg,
                )
                return (0, f"{external_id}: {error_msg}")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate LLM analysis (ArgumentareCritica) for CNSC decisions"
    )
    parser.add_argument(
        "--limit", type=int, help="Max number of decisions to analyze"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-analyze all decisions (including already analyzed)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be analyzed without actually doing it",
    )
    parser.add_argument(
        "--reanalyze-incomplete",
        action="store_true",
        help="Find and re-analyze decisions with incomplete analyses "
             "(missing AC arguments or CNSC reasoning, typically caused by input truncation)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=RATE_LIMIT_DELAY,
        help=f"Seconds to wait between decisions (default: {RATE_LIMIT_DELAY})",
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["gemini", "anthropic", "openai", "groq", "openrouter"],
        help="LLM provider to use (default: auto-detect from env vars)",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Specific model to use (e.g., gemini-2.5-pro, claude-sonnet-4-6, llama-3.3-70b-versatile)",
    )
    parser.add_argument(
        "--decision",
        nargs="+",
        metavar="BO_REF",
        help="Specific BO reference(s) to analyze/re-analyze (e.g., BO2025_1076 BO2025_823)",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        help="Only include decisions with text_integral >= this many characters",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        help="Only include decisions with text_integral <= this many characters",
    )

    args = parser.parse_args()

    # Initialize database
    print("Connecting to database...")
    db_initialized = await init_db()
    if not db_initialized:
        print("ERROR: Could not connect to database")
        print("Make sure DATABASE_URL is set and database is accessible")
        sys.exit(1)

    async with db_session.async_session_factory() as session:
        # Count current state
        total_decisions = await session.scalar(
            select(func.count()).select_from(DecizieCNSC)
        )
        total_with_args = await session.scalar(
            select(func.count(func.distinct(ArgumentareCritica.decizie_id)))
        )
        total_args = await session.scalar(
            select(func.count()).select_from(ArgumentareCritica)
        )

        print(f"\nCurrent state:")
        print(f"  Total decisions:        {total_decisions}")
        print(f"  Already analyzed:       {total_with_args}")
        print(f"  Remaining:              {total_decisions - total_with_args}")
        print(f"  Total ArgumentareCritica records: {total_args}")
        print()

        # Build query for decisions to analyze
        overwrite_mode = args.force
        if args.decision:
            # Target specific decisions by BO reference — highest priority
            stmt = (
                select(DecizieCNSC)
                .where(DecizieCNSC.external_id.in_(args.decision))
            )
            overwrite_mode = True  # Always overwrite when targeting specific decisions

            # Warn about unfound references
            result_check = await session.execute(
                select(DecizieCNSC.external_id).where(
                    DecizieCNSC.external_id.in_(args.decision)
                )
            )
            found_refs = {row[0] for row in result_check.fetchall()}
            missing_refs = set(args.decision) - found_refs
            if missing_refs:
                print(f"  WARNING: BO references not found in DB: {', '.join(sorted(missing_refs))}")
            if found_refs:
                print(f"  Targeting {len(found_refs)} specific decision(s): {', '.join(sorted(found_refs))}")

        elif args.reanalyze_incomplete:
            # Find decisions that have incomplete analyses:
            # - NULL or empty fields (AC arguments, CNSC reasoning, CNSC elements)
            # - Fields containing "nu este disponibil" (LLM placeholder when input was truncated)
            from sqlalchemy import or_
            incomplete_ids = (
                select(ArgumentareCritica.decizie_id)
                .where(
                    or_(
                        ArgumentareCritica.argumente_ac.is_(None),
                        ArgumentareCritica.argumente_ac == "",
                        ArgumentareCritica.argumente_ac.ilike("%nu este disponibil%"),
                        ArgumentareCritica.argumentatie_cnsc.is_(None),
                        ArgumentareCritica.argumentatie_cnsc == "",
                        ArgumentareCritica.argumentatie_cnsc.ilike("%nu este disponibil%"),
                        ArgumentareCritica.elemente_retinute_cnsc.is_(None),
                        ArgumentareCritica.elemente_retinute_cnsc == "",
                        ArgumentareCritica.elemente_retinute_cnsc.ilike("%nu este disponibil%"),
                    )
                )
                .distinct()
            )
            result_incomplete = await session.execute(incomplete_ids)
            incomplete_decision_ids = [row[0] for row in result_incomplete.fetchall()]

            if not incomplete_decision_ids:
                print("No decisions with incomplete analyses found.")
                return

            print(f"  Found {len(incomplete_decision_ids)} decisions with incomplete analyses")
            print(f"  (includes NULL/empty fields AND 'nu este disponibil' placeholders)")
            stmt = (
                select(DecizieCNSC)
                .where(DecizieCNSC.id.in_(incomplete_decision_ids))
                .order_by(func.length(DecizieCNSC.text_integral).desc())  # Largest first
            )
            overwrite_mode = True  # Must overwrite to replace incomplete records
        elif args.force:
            stmt = select(DecizieCNSC).order_by(DecizieCNSC.created_at.desc())
        else:
            # Only decisions without ArgumentareCritica
            analyzed_ids = select(ArgumentareCritica.decizie_id).distinct()
            stmt = (
                select(DecizieCNSC)
                .where(DecizieCNSC.id.notin_(analyzed_ids))
                .order_by(DecizieCNSC.created_at.desc())
            )

        # Apply text length filters (works with all modes)
        if args.min_length:
            stmt = stmt.where(func.length(DecizieCNSC.text_integral) >= args.min_length)
            print(f"  Filter: text length >= {args.min_length:,} chars")
        if args.max_length:
            stmt = stmt.where(func.length(DecizieCNSC.text_integral) <= args.max_length)
            print(f"  Filter: text length <= {args.max_length:,} chars")

        if args.limit:
            stmt = stmt.limit(args.limit)

        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        if not decisions:
            print("Nothing to analyze. All decisions already have ArgumentareCritica records.")
            return

        print(f"Decisions to analyze: {len(decisions)}")

        if args.dry_run:
            print("\n[DRY RUN] Would analyze:")
            for i, dec in enumerate(decisions[:20], 1):
                print(f"  {i}. {dec.external_id} ({dec.filename})")
            if len(decisions) > 20:
                print(f"  ... and {len(decisions) - 20} more")
            return

        print(f"Rate limit: {args.rate_limit}s between decisions")
        print(f"Retry: up to {MAX_RETRIES} attempts per decision")
        print()

    # Process decisions (new session per decision for isolation)
    # Build LLM provider from CLI args if specified
    llm_provider = None
    if args.provider or args.model:
        from app.services.llm.factory import get_llm_provider
        kwargs = {}
        if args.model:
            kwargs["model"] = args.model
        llm_provider = get_llm_provider(provider_type=args.provider, **kwargs)
        print(f"  LLM Provider: {llm_provider.provider_name} / {llm_provider.model_name}")

    analysis_service = DecisionAnalysisService(llm_provider=llm_provider)
    start_time = time.time()
    stats = {
        "analyzed": 0,
        "skipped": 0,
        "failed": 0,
        "argumentari_created": 0,
        "errors": [],
    }

    for i, decision in enumerate(decisions, 1):
        elapsed = time.time() - start_time
        rate = stats["analyzed"] / elapsed * 60 if elapsed > 0 and stats["analyzed"] > 0 else 0

        # Capture identifiers as plain strings BEFORE any DB work
        # (safe to use after session rollback, when ORM objects are expired)
        ext_id = decision.external_id
        dec_id = decision.id
        text_len = len(decision.text_integral) if decision.text_integral else 0

        print(
            f"[{i}/{len(decisions)}] Analyzing {ext_id} ({text_len:,} chars)... ",
            end="",
            flush=True,
        )

        async with db_session.async_session_factory() as session:
            count, error = await analyze_with_retry(
                analysis_service, session, decision_id=dec_id,
                external_id=ext_id, overwrite=overwrite_mode
            )

        if error:
            print(f"FAILED: {error}")
            stats["failed"] += 1
            stats["errors"].append(error)
        elif count == 0:
            print("SKIPPED (already analyzed)")
            stats["skipped"] += 1
        else:
            print(f"OK ({count} critici, {rate:.1f}/min)")
            stats["analyzed"] += 1
            stats["argumentari_created"] += count

        # Rate limiting
        if i < len(decisions):
            await asyncio.sleep(args.rate_limit)

    # Summary
    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print("ANALYSIS SUMMARY")
    print("=" * 60)
    print(f"Total processed:          {len(decisions)}")
    print(f"Successfully analyzed:    {stats['analyzed']}")
    print(f"Skipped (already done):   {stats['skipped']}")
    print(f"Failed:                   {stats['failed']}")
    print(f"ArgumentareCritica created: {stats['argumentari_created']}")
    print(f"Time elapsed:             {elapsed:.1f}s ({elapsed/60:.1f}min)")

    if stats["errors"]:
        print(f"\nErrors ({len(stats['errors'])}):")
        for error in stats["errors"][:10]:
            print(f"  - {error}")
        if len(stats["errors"]) > 10:
            print(f"  ... and {len(stats['errors']) - 10} more")

    print("=" * 60)

    if stats["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
