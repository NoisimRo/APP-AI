#!/usr/bin/env python3
"""Import court decisions from rejust.ro JSON files into the database.

Reads JSON files produced by the rejust scraper (docs/rejust_scraper.py),
parses the solutie field into structured solutie_tip/solutie_detalii,
and inserts into the hotarari_judecatoresti table.

Idempotent: skips records where cod_rj already exists in DB.

Usage:
    # Import from a single file
    DATABASE_URL="..." python scripts/import_hotarari_rejust.py --file rejust_output/hotarari_batch_000.json

    # Import from a directory (all hotarari_batch_*.json files)
    DATABASE_URL="..." python scripts/import_hotarari_rejust.py --dir rejust_output/

    # Dry run (parse only, no DB writes)
    DATABASE_URL="..." python scripts/import_hotarari_rejust.py --file data.json --dry-run

    # Limit number of records
    DATABASE_URL="..." python scripts/import_hotarari_rejust.py --file data.json --limit 10

    # Force reimport (delete all + reinsert)
    DATABASE_URL="..." python scripts/import_hotarari_rejust.py --dir rejust_output/ --force
"""

import asyncio
import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select, delete
from app.core.logging import get_logger
from app.db.session import init_db
from app.db import session as db_session
from app.models.decision import HotarareJudecatoreasca

logger = get_logger(__name__)

# Batch size for DB commits
COMMIT_BATCH_SIZE = 50

# ─── SOLUȚIE PARSER ─────────────────────────────────────────────────────────

# Patterns for parsing the raw "denumire_solutie" field
# Examples:
#   "Civil - Fond - Respinge cererea"       → ("Respingere", None)
#   "Civil - Fond - Admite cererea"         → ("Admitere", None)
#   "Civil - Fond - Alte soluţii"           → ("Alte soluții", None)
#   "Civil - Respingere recurs - Nefondat"  → ("Respingere", "Nefondat")
#   "Civil - Respingere recurs - Inadmisibil" → ("Respingere", "Inadmisibil")
#   "Civil - Admitere apel"                 → ("Admitere", None)
#   "Civil - Recurs (alte soluţii)"         → ("Alte soluții", None)

SOLUTIE_RULES = [
    # Recurs patterns (check before generic fond patterns)
    (re.compile(r'Respingere\s+recurs\s*[-–]\s*(.+)', re.I), "Respingere"),
    (re.compile(r'Admitere\s+recurs', re.I), "Admitere"),
    (re.compile(r'Recurs\s*\(?\s*alte\s+solu', re.I), "Alte soluții"),
    # Apel patterns
    (re.compile(r'Respingere\s+apel', re.I), "Respingere"),
    (re.compile(r'Admitere\s+apel', re.I), "Admitere"),
    # Fond patterns
    (re.compile(r'Respinge\s+cererea', re.I), "Respingere"),
    (re.compile(r'Admite\s+cererea', re.I), "Admitere"),
    (re.compile(r'Anulare', re.I), "Anulare"),
    (re.compile(r'Alte\s+solu[țţ]ii', re.I), "Alte soluții"),
    # Tranzacție
    (re.compile(r'Tranzac[țţ]ie', re.I), "Tranzacție"),
]


def parse_solutie(raw: str) -> tuple[str | None, str | None]:
    """Parse raw soluție string into (solutie_tip, solutie_detalii).

    Returns:
        Tuple of (tip, detalii) where tip is one of:
        Respingere, Admitere, Anulare, Alte soluții, Tranzacție, None
    """
    if not raw:
        return None, None

    for pattern, tip in SOLUTIE_RULES:
        m = pattern.search(raw)
        if m:
            detalii = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else None
            return tip, detalii

    return None, None


# ─── DATA PARSING ───────────────────────────────────────────────────────────

def parse_iso_date(date_str: str | None) -> datetime | None:
    """Parse ISO date string to naive datetime (asyncpg compatible)."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", ""))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def load_json_files(file_path: str | None, dir_path: str | None) -> list[tuple[str, list]]:
    """Load JSON records from file or directory.

    Returns list of (filename, records) tuples.
    """
    results = []

    if file_path:
        p = Path(file_path)
        if not p.exists():
            print(f"ERROR: File not found: {file_path}")
            sys.exit(1)
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
        records = data if isinstance(data, list) else [data]
        results.append((p.name, records))

    elif dir_path:
        d = Path(dir_path)
        if not d.exists():
            print(f"ERROR: Directory not found: {dir_path}")
            sys.exit(1)
        # Find all batch files, sorted by name
        files = sorted(d.glob("hotarari_batch_*.json"))
        if not files:
            # Fallback: try hotarari.json (legacy single-file format)
            legacy = d / "hotarari.json"
            if legacy.exists():
                files = [legacy]
        if not files:
            print(f"ERROR: No hotarari_batch_*.json or hotarari.json in {dir_path}")
            sys.exit(1)
        for fp in files:
            with open(fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            records = data if isinstance(data, list) else [data]
            results.append((fp.name, records))

    return results


# ─── MAIN ───────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="Import hotărâri judecătorești from rejust.ro JSON into DB"
    )
    parser.add_argument("--file", type=str, help="Path to a single JSON file")
    parser.add_argument("--dir", type=str, help="Path to directory with batch JSON files")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--force", action="store_true", help="Delete all and reimport")
    parser.add_argument("--limit", type=int, help="Limit number of records to import")
    args = parser.parse_args()

    if not args.file and not args.dir:
        parser.error("Specify --file or --dir")

    # Load JSON files
    print("Loading JSON files...")
    file_records = load_json_files(args.file, args.dir)

    total_records = sum(len(recs) for _, recs in file_records)
    print(f"  Files: {len(file_records)}")
    print(f"  Total records: {total_records}")

    # Flatten all records with their source filename
    all_records = []
    for filename, records in file_records:
        for rec in records:
            all_records.append((filename, rec))

    if args.limit:
        all_records = all_records[:args.limit]
        print(f"  Limited to: {len(all_records)}")

    # Parse soluții for stats
    stats_tip = Counter()
    stats_instanta = Counter()
    stats_stadiu = Counter()
    for _, rec in all_records:
        tip, _ = parse_solutie(rec.get("solutie", ""))
        stats_tip[tip or "N/A"] += 1
        stats_instanta[rec.get("denumire_instanta", "N/A")] += 1
        stats_stadiu[rec.get("stadiu_procesual", "N/A")] += 1

    if args.dry_run:
        print(f"\nDry run — {len(all_records)} records would be imported:")
        print(f"\n  Per soluție_tip:")
        for tip, cnt in stats_tip.most_common():
            print(f"    {tip}: {cnt}")
        print(f"\n  Per instanță (top 10):")
        for inst, cnt in stats_instanta.most_common(10):
            print(f"    {inst}: {cnt}")
        print(f"\n  Per stadiu:")
        for st, cnt in stats_stadiu.most_common():
            print(f"    {st}: {cnt}")
        return

    # Init DB
    db_ok = await init_db()
    if not db_ok:
        print("ERROR: Could not connect to database.")
        print("Make sure DATABASE_URL is exported:")
        print('  export DATABASE_URL="postgresql+asyncpg://..."')
        sys.exit(1)

    # Force mode: delete all existing
    if args.force:
        async with db_session.async_session_factory() as session:
            result = await session.execute(delete(HotarareJudecatoreasca))
            await session.commit()
            print(f"  Force mode: deleted {result.rowcount} existing records")

    # Pre-load existing cod_rj for idempotency (set-based dedup)
    async with db_session.async_session_factory() as session:
        result = await session.execute(
            select(HotarareJudecatoreasca.cod_rj)
        )
        existing_codes = set(result.scalars().all())
    print(f"  Existing in DB: {len(existing_codes)}")

    # Filter out already imported
    to_import = [
        (fn, rec) for fn, rec in all_records
        if rec.get("cod_rj") and rec["cod_rj"] not in existing_codes
    ]
    skipped_no_code = sum(1 for _, rec in all_records if not rec.get("cod_rj"))
    skipped_existing = len(all_records) - len(to_import) - skipped_no_code
    print(f"  New to import: {len(to_import)}")
    print(f"  Skipped (already in DB): {skipped_existing}")
    if skipped_no_code:
        print(f"  Skipped (no cod_rj): {skipped_no_code}")

    if not to_import:
        print("\nNothing to import — all records already in DB.")
        return

    start = time.time()
    total_inserted = 0
    errors = 0

    # Process in batches
    for batch_start in range(0, len(to_import), COMMIT_BATCH_SIZE):
        batch = to_import[batch_start:batch_start + COMMIT_BATCH_SIZE]
        batch_num = batch_start // COMMIT_BATCH_SIZE + 1
        total_batches = (len(to_import) + COMMIT_BATCH_SIZE - 1) // COMMIT_BATCH_SIZE
        print(f"\nBatch {batch_num}/{total_batches} ({len(batch)} records)...")

        async with db_session.async_session_factory() as session:
            for filename, rec in batch:
                try:
                    tip, detalii = parse_solutie(rec.get("solutie", ""))

                    hotarare = HotarareJudecatoreasca(
                        id=str(uuid4()),
                        cod_rj=rec["cod_rj"],
                        url=rec.get("url", f"https://rejust.ro/juris/{rec['cod_rj']}"),
                        referinta_citare=rec.get("referinta_citare", ""),
                        numar_document=rec.get("numar_document") or None,
                        data_document=parse_iso_date(rec.get("data_document")),
                        cod_ecli=rec.get("cod_ecli") or None,
                        dosar_nr=rec.get("dosar_nr") or None,
                        denumire_instanta=rec.get("denumire_instanta", ""),
                        denumire_materie=rec.get("denumire_materie") or None,
                        denumire_obiect=rec.get("denumire_obiect") or None,
                        denumire_categorie=rec.get("denumire_categorie") or None,
                        stadiu_procesual=rec.get("stadiu_procesual") or None,
                        solutie=rec.get("solutie") or None,
                        solutie_tip=tip,
                        solutie_detalii=detalii,
                        text_hotarare=rec.get("text_hotarare", ""),
                        documente_dosar=rec.get("documente_dosar", []),
                        batch_file=filename,
                    )
                    session.add(hotarare)
                except Exception as e:
                    errors += 1
                    print(f"  ERROR on {rec.get('cod_rj', '?')}: {e}")

            await session.commit()
            total_inserted += len(batch) - errors
            print(f"  Committed {len(batch)} records")

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s!")
    print(f"  Inserted: {total_inserted}")
    print(f"  Errors: {errors}")
    print(f"  Skipped (already in DB): {skipped_existing}")


if __name__ == "__main__":
    asyncio.run(main())
