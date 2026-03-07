#!/usr/bin/env python3
"""Enrich decizii_cnsc with CPV descriptions from nomenclator_cpv.

Populates cpv_descriere, cpv_categorie, cpv_clasa in decizii_cnsc
by joining with the nomenclator_cpv table.

Usage:
    DATABASE_URL="..." python scripts/enrich_cpv.py
    DATABASE_URL="..." python scripts/enrich_cpv.py --dry-run
    DATABASE_URL="..." python scripts/enrich_cpv.py --force  # Re-enrich all, even if already populated
"""

import asyncio
import argparse
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select, update
from app.core.logging import get_logger
from app.db.session import init_db
from app.db import session as db_session
from app.models.decision import DecizieCNSC, NomenclatorCPV

logger = get_logger(__name__)


async def enrich_cpv(force: bool = False, dry_run: bool = False) -> dict:
    """Enrich decisions with CPV descriptions from nomenclator.

    Args:
        force: Re-enrich all decisions, even if cpv_descriere is already set.
        dry_run: Print what would be done without writing to DB.

    Returns:
        Dict with enrichment statistics.
    """
    stats = {
        "total_with_cpv": 0,
        "already_enriched": 0,
        "enriched": 0,
        "no_match": 0,
        "no_match_codes": [],
    }

    async with db_session.async_session_factory() as session:
        # Load CPV nomenclator into memory (fast lookup)
        result = await session.execute(select(NomenclatorCPV))
        cpv_map = {cpv.cod_cpv: cpv for cpv in result.scalars().all()}
        print(f"Loaded {len(cpv_map)} CPV codes from nomenclator")

        if not cpv_map:
            print("ERROR: nomenclator_cpv is empty. Run import_cpv.py first.")
            return stats

        # Get decisions with CPV codes
        query = select(DecizieCNSC).where(DecizieCNSC.cod_cpv.isnot(None))
        if not force:
            query = query.where(DecizieCNSC.cpv_descriere.is_(None))

        result = await session.execute(query)
        decisions = list(result.scalars().all())

        stats["total_with_cpv"] = len(decisions)
        print(f"Found {len(decisions)} decisions to enrich")

        for dec in decisions:
            cpv = cpv_map.get(dec.cod_cpv)
            if cpv:
                if not dry_run:
                    dec.cpv_descriere = cpv.descriere
                    dec.cpv_categorie = cpv.categorie_achizitii
                    dec.cpv_clasa = cpv.clasa_produse
                stats["enriched"] += 1
            else:
                stats["no_match"] += 1
                if dec.cod_cpv not in stats["no_match_codes"]:
                    stats["no_match_codes"].append(dec.cod_cpv)

        if not dry_run:
            await session.commit()
            print(f"Committed {stats['enriched']} updates")

    return stats


async def main():
    parser = argparse.ArgumentParser(
        description="Enrich decisions with CPV descriptions from nomenclator"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-enrich all decisions (even if cpv_descriere is already set)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview only, no DB writes",
    )
    args = parser.parse_args()

    await init_db()
    stats = await enrich_cpv(force=args.force, dry_run=args.dry_run)

    print("\n" + "=" * 60)
    print("CPV ENRICHMENT SUMMARY")
    print("=" * 60)
    print(f"Decisions with CPV:    {stats['total_with_cpv']}")
    print(f"Enriched:              {stats['enriched']}")
    print(f"No match in nomenclator: {stats['no_match']}")
    if stats["no_match_codes"]:
        print(f"Unmatched CPV codes:   {', '.join(stats['no_match_codes'][:20])}")
    print("=" * 60)

    if args.dry_run:
        print("\n(DRY RUN - no changes written)")


if __name__ == "__main__":
    asyncio.run(main())
