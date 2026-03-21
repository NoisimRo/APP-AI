#!/usr/bin/env python3
"""Retroactive extraction of procurement metadata for existing decisions.

Uses regex patterns (zero LLM calls) to extract:
- criteriu_atribuire: one of 4 canonical award criteria
- numar_oferte: number of offers submitted
- valoare_estimata + moneda: estimated contract value
- numar_anunt_participare: SEAP participation notice number
- data_raport_procedura: procedure report date

Features:
- Skips decisions that already have all fields (idempotent)
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

# All extractable fields
FIELDS = [
    "criteriu_atribuire",
    "numar_oferte",
    "valoare_estimata",
    "numar_anunt_participare",
    "data_raport_procedura",
    "domeniu_legislativ",
    "tip_procedura",
]


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
                    DecizieCNSC.valoare_estimata.is_(None),
                    DecizieCNSC.numar_anunt_participare.is_(None),
                    DecizieCNSC.data_raport_procedura.is_(None),
                    DecizieCNSC.domeniu_legislativ.is_(None),
                    DecizieCNSC.tip_procedura.is_(None),
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

        counters = {f: 0 for f in FIELDS}
        start = time.time()

        for i, decision in enumerate(decisions, 1):
            text = decision.text_integral or ""
            if not text:
                continue

            changes = []
            any_change = False

            # criteriu_atribuire
            criteriu = cnsc_parser._extract_criteriu_atribuire(text)
            if criteriu and (args.force or not decision.criteriu_atribuire):
                changes.append(f"criteriu={criteriu}")
                if not args.dry_run:
                    decision.criteriu_atribuire = criteriu
                counters["criteriu_atribuire"] += 1
                any_change = True

            # numar_oferte
            numar = cnsc_parser._extract_numar_oferte(text)
            if numar and (args.force or not decision.numar_oferte):
                changes.append(f"oferte={numar}")
                if not args.dry_run:
                    decision.numar_oferte = numar
                counters["numar_oferte"] += 1
                any_change = True

            # valoare_estimata
            val, mon = cnsc_parser._extract_valoare_estimata(text)
            if val and (args.force or not decision.valoare_estimata):
                changes.append(f"valoare={val:,.2f} {mon}")
                if not args.dry_run:
                    decision.valoare_estimata = val
                    if mon:
                        decision.moneda = mon
                counters["valoare_estimata"] += 1
                any_change = True

            # numar_anunt_participare
            anunt = cnsc_parser._extract_numar_anunt(text)
            if anunt and (args.force or not decision.numar_anunt_participare):
                changes.append(f"anunt={anunt}")
                if not args.dry_run:
                    decision.numar_anunt_participare = anunt
                counters["numar_anunt_participare"] += 1
                any_change = True

            # data_raport_procedura
            data_rap = cnsc_parser._extract_data_raport(text)
            if data_rap and (args.force or not decision.data_raport_procedura):
                changes.append(f"raport={data_rap.strftime('%d.%m.%Y')}")
                if not args.dry_run:
                    decision.data_raport_procedura = data_rap
                counters["data_raport_procedura"] += 1
                any_change = True

            # domeniu_legislativ
            domeniu = cnsc_parser._extract_domeniu_legislativ(text)
            if domeniu and (args.force or not decision.domeniu_legislativ):
                changes.append(f"domeniu={domeniu}")
                if not args.dry_run:
                    decision.domeniu_legislativ = domeniu
                counters["domeniu_legislativ"] += 1
                any_change = True

            # tip_procedura
            procedura = cnsc_parser._extract_tip_procedura(text)
            if procedura and (args.force or not decision.tip_procedura):
                changes.append(f"procedura={procedura}")
                if not args.dry_run:
                    decision.tip_procedura = procedura
                counters["tip_procedura"] += 1
                any_change = True

            if changes:
                bo_ref = f"BO{decision.an_bo}_{decision.numar_bo}"
                logger.info(f"  [{i}/{len(decisions)}] {bo_ref}: {', '.join(changes)}")

            if not args.dry_run and any_change:
                await session.commit()

            if i % 100 == 0:
                elapsed = time.time() - start
                logger.info(f"  Progress: {i}/{len(decisions)} ({elapsed:.1f}s)")

        elapsed = time.time() - start
        logger.info(f"Done in {elapsed:.1f}s. Extracted:")
        for field, count in counters.items():
            logger.info(f"  {field}: {count}")
        if args.dry_run:
            logger.info("DRY RUN — no changes applied.")


if __name__ == "__main__":
    asyncio.run(main())
