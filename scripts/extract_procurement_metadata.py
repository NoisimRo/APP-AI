#!/usr/bin/env python3
"""Retroactive extraction of criteriu_atribuire and numar_oferte for existing decisions.

Uses regex patterns (zero LLM calls) to extract:
- criteriu_atribuire: one of 4 canonical award criteria
- numar_oferte: number of offers submitted

Features:
- Skips decisions that already have both fields (idempotent)
- Per-decision commit (crash-safe)
- Dry-run mode for review before applying
- Force mode to re-extract all

Usage:
    python scripts/extract_procurement_metadata.py                  # Extract for all missing
    python scripts/extract_procurement_metadata.py --limit 10       # Test with 10
    python scripts/extract_procurement_metadata.py --dry-run        # Preview without applying
    python scripts/extract_procurement_metadata.py --force          # Re-extract all (overwrite)
"""

import asyncio
import argparse
import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select, func, or_
from app.core.logging import get_logger
from app.db.session import init_db
from app.db import session as db_session
from app.models.decision import DecizieCNSC
from app.services.parser import CNSCDecisionParser

logger = get_logger(__name__)


async def main():
    parser = argparse.ArgumentParser(description="Extract procurement metadata retroactively")
    parser.add_argument("--limit", type=int, help="Max decisions to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without applying")
    parser.add_argument("--force", action="store_true", help="Re-extract all (overwrite)")
    args = parser.parse_args()

    await init_db()

    cnsc_parser = CNSCDecisionParser()

    async with db_session.get_session() as session:
        # Count total
        total_q = select(func.count()).select_from(DecizieCNSC)
        total = (await session.execute(total_q)).scalar()

        # Build query for decisions needing extraction
        query = select(DecizieCNSC).order_by(DecizieCNSC.numar_bo)

        if not args.force:
            # Only process decisions missing at least one field
            query = query.where(
                or_(
                    DecizieCNSC.criteriu_atribuire.is_(None),
                    DecizieCNSC.numar_oferte.is_(None),
                )
            )

        if args.limit:
            query = query.limit(args.limit)

        result = await session.execute(query)
        decisions = result.scalars().all()

        logger.info(f"Total decisions: {total}, to process: {len(decisions)}")
        if not decisions:
            logger.info("Nothing to process.")
            return

        extracted_criteriu = 0
        extracted_oferte = 0
        start = time.time()

        for i, decision in enumerate(decisions, 1):
            text = decision.text_integral or ""
            if not text:
                continue

            # Extract award criterion
            criteriu = cnsc_parser._extract_criteriu_atribuire(text)
            numar = cnsc_parser._extract_numar_oferte(text)

            changes = []
            if criteriu and (args.force or not decision.criteriu_atribuire):
                if args.dry_run:
                    changes.append(f"criteriu={criteriu}")
                else:
                    decision.criteriu_atribuire = criteriu
                extracted_criteriu += 1

            if numar and (args.force or not decision.numar_oferte):
                if args.dry_run:
                    changes.append(f"numar_oferte={numar}")
                else:
                    decision.numar_oferte = numar
                extracted_oferte += 1

            if changes:
                bo_ref = f"BO{decision.an_bo}_{decision.numar_bo}"
                logger.info(f"  [{i}/{len(decisions)}] {bo_ref}: {', '.join(changes)}")

            if not args.dry_run and (criteriu or numar):
                await session.commit()

            if i % 100 == 0:
                elapsed = time.time() - start
                logger.info(f"  Progress: {i}/{len(decisions)} ({elapsed:.1f}s)")

        elapsed = time.time() - start
        logger.info(
            f"Done in {elapsed:.1f}s. "
            f"Extracted criteriu_atribuire: {extracted_criteriu}, "
            f"numar_oferte: {extracted_oferte}"
        )
        if args.dry_run:
            logger.info("DRY RUN — no changes applied.")


if __name__ == "__main__":
    asyncio.run(main())
