#!/usr/bin/env python3
"""Import CPV codes from a pipe-delimited text file into the database.

Reads the CPV nomenclator file (UTF-8, pipe-delimited) and populates the
nomenclator_cpv table. Also enriches existing decisions with CPV descriptions.

Expected file format (pipe-delimited, UTF-8):
    code|denumire_ro|denumire_en|Categorie|Clasa produse standardizate
    03000000-1|Produse agricole...|Agricultural...|Furnizare|Produse agricole primare
    ...

9,454 CPV codes with full hierarchy (5 levels: Diviziune → Subcategorie).
Parent codes computed at import time from 8-digit prefix matching.

Usage:
    # Import from GCS (default: date-expert-app/00_Coduri-CPV.txt)
    DATABASE_URL="..." python scripts/import_cpv_xlsx.py

    # Import from GCS with enrichment
    DATABASE_URL="..." python scripts/import_cpv_xlsx.py --enrich

    # Import from local file
    DATABASE_URL="..." python scripts/import_cpv_xlsx.py --file /path/to/00_Coduri-CPV.txt

    # Dry run (parse + validate only, no DB writes)
    DATABASE_URL="..." python scripts/import_cpv_xlsx.py --dry-run

    # Only enrich decisions from existing nomenclator (no import)
    DATABASE_URL="..." python scripts/import_cpv_xlsx.py --enrich-only
"""

import asyncio
import argparse
import re
import sys
from pathlib import Path
from typing import Optional

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select, delete, update
from app.core.logging import get_logger
from app.db.session import init_db
from app.db import session as db_session
from app.models.decision import NomenclatorCPV, DecizieCNSC

logger = get_logger(__name__)

# Valid CPV code format: 8 digits + hyphen + 1 check digit
CPV_CODE_RE = re.compile(r'^\d{8}-\d$')

# Valid categories (from documentation: exactly 3 values)
VALID_CATEGORIES = {'Furnizare', 'Servicii', 'Lucrari', 'Lucrări'}


def compute_cpv_level(cod_cpv: str) -> int:
    """Compute hierarchical level of a CPV code.

    Level 1: XX000000-X (Diviziune)     — 45 codes
    Level 2: XXX00000-X (Grup)          — 272 codes
    Level 3: XXXX0000-X (Clasă)         — 1,002 codes
    Level 4: XXXXX000-X (Categorie)     — 2,379 codes
    Level 5: XXXXXX00-X+ (Subcategorie) — 5,756 codes
    """
    digits = cod_cpv[:8]
    level = 1
    for i in range(2, 8):
        if digits[i] != '0':
            level = i
    return min(level, 5)


def compute_cpv_parent(cod_cpv: str, all_codes: dict[str, str]) -> Optional[str]:
    """Compute parent CPV code by zeroing rightmost non-zero digits.

    Matches parent by 8-digit prefix in all_codes dict to get correct check digit.
    Codes are sorted, so parents are always imported before children.

    Args:
        cod_cpv: CPV code like "03111100-3".
        all_codes: Dict mapping 8-digit prefix → full code with check digit.

    Returns:
        Full parent code with correct check digit, or None for top-level.
    """
    digits = cod_cpv[:8]
    for i in range(7, 1, -1):
        if digits[i] != '0':
            parent_prefix = digits[:i] + '0' * (8 - i)
            if parent_prefix in all_codes:
                return all_codes[parent_prefix]
    return None


def parse_pipe_delimited(content: str) -> list[dict]:
    """Parse pipe-delimited CPV text file.

    Expected format:
        code|denumire_ro|denumire_en|Categorie|Clasa produse standardizate
        03000000-1|Produse agricole...|Agricultural...|Furnizare|Produse agricole primare

    Args:
        content: UTF-8 text content of the file.

    Returns:
        List of CPV record dicts with validation stats printed.
    """
    lines = content.strip().split('\n')
    records = []
    skipped = 0
    warnings = []

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue

        parts = line.split('|')
        if len(parts) < 5:
            # Could be header or malformed line
            if i == 1 and 'code' in line.lower():
                continue  # Skip header
            skipped += 1
            if skipped <= 5:
                warnings.append(f"  Row {i}: expected 5 columns, got {len(parts)}: {line[:80]}")
            continue

        code = parts[0].strip()
        if not CPV_CODE_RE.match(code):
            # Skip header row
            if i <= 2 or 'code' in code.lower():
                continue
            skipped += 1
            if skipped <= 5:
                warnings.append(f"  Row {i}: invalid code format: {code[:20]}")
            continue

        denumire_ro = parts[1].strip()
        denumire_en = parts[2].strip()
        categorie = parts[3].strip()
        clasa = parts[4].strip()

        # Validate non-empty (per documentation: no NULLs)
        if not denumire_ro:
            warnings.append(f"  Row {i} ({code}): empty denumire_ro")
        if not categorie:
            warnings.append(f"  Row {i} ({code}): empty categorie")

        # Normalize categorie: "Lucrari" → "Lucrări"
        if categorie.lower().startswith('lucr'):
            categorie = 'Lucrări'
        elif categorie.lower().startswith('serv'):
            categorie = 'Servicii'
        elif categorie.lower().startswith('furn'):
            categorie = 'Furnizare'

        records.append({
            'cod_cpv': code,
            'descriere': denumire_ro,
            'denumire_en': denumire_en,
            'categorie_achizitii': categorie,
            'clasa_produse': clasa,
        })

    if skipped:
        print(f"Skipped {skipped} lines (invalid format)")
    if warnings:
        print(f"Warnings:")
        for w in warnings[:10]:
            print(w)

    return records


def validate_records(records: list[dict]) -> dict:
    """Validate parsed CPV records against documentation rules.

    Returns validation statistics and warnings.
    """
    stats = {
        'total': len(records),
        'unique_codes': len(set(r['cod_cpv'] for r in records)),
        'duplicates': 0,
        'empty_descriere': 0,
        'empty_categorie': 0,
        'invalid_categorie': 0,
        'categories': {},
        'levels': {},
    }

    seen_codes = set()
    for rec in records:
        code = rec['cod_cpv']
        if code in seen_codes:
            stats['duplicates'] += 1
        seen_codes.add(code)

        if not rec['descriere']:
            stats['empty_descriere'] += 1
        if not rec['categorie_achizitii']:
            stats['empty_categorie'] += 1
        elif rec['categorie_achizitii'] not in ('Furnizare', 'Servicii', 'Lucrări'):
            stats['invalid_categorie'] += 1

        cat = rec['categorie_achizitii'] or 'N/A'
        stats['categories'][cat] = stats['categories'].get(cat, 0) + 1

        lvl = compute_cpv_level(rec['cod_cpv'])
        stats['levels'][lvl] = stats['levels'].get(lvl, 0) + 1

    return stats


async def import_records(records: list[dict]) -> dict:
    """Import CPV records: truncate + insert (full reload strategy).

    Computes parent codes and hierarchy levels at import time.
    Codes are sorted ascending, so parents exist before children.

    Returns:
        Import statistics.
    """
    stats = {
        'total_parsed': len(records),
        'imported': 0,
        'failed': 0,
        'errors': [],
    }

    # Build lookup for parent computation: 8-digit prefix → full code
    all_codes = {rec['cod_cpv'][:8]: rec['cod_cpv'] for rec in records}

    async with db_session.async_session_factory() as session:
        # Truncate existing records
        await session.execute(delete(NomenclatorCPV))
        await session.commit()
        print(f"Truncated nomenclator_cpv table.")

        # Batch insert for performance
        batch = []
        batch_size = 500

        for rec in records:
            try:
                parent = compute_cpv_parent(rec['cod_cpv'], all_codes)
                nivel = compute_cpv_level(rec['cod_cpv'])

                cpv = NomenclatorCPV(
                    cod_cpv=rec['cod_cpv'],
                    descriere=rec['descriere'],
                    denumire_en=rec.get('denumire_en'),
                    categorie_achizitii=rec['categorie_achizitii'],
                    clasa_produse=rec['clasa_produse'],
                    cod_parinte=parent,
                    nivel=nivel,
                )
                batch.append(cpv)
                stats['imported'] += 1

                if len(batch) >= batch_size:
                    session.add_all(batch)
                    await session.flush()
                    batch = []

            except Exception as e:
                stats['failed'] += 1
                stats['errors'].append(f"{rec['cod_cpv']}: {e}")
                logger.error("cpv_record_failed", cod=rec['cod_cpv'], error=str(e))

        # Flush remaining batch
        if batch:
            session.add_all(batch)
            await session.flush()

        await session.commit()
        print(f"Inserted {stats['imported']} CPV codes.")

    return stats


async def enrich_decisions() -> dict:
    """Enrich decisions with CPV descriptions from nomenclator.

    Updates cpv_descriere, cpv_categorie, cpv_clasa on decizii_cnsc
    by matching cod_cpv with nomenclator_cpv.
    Uses prefix matching (first 8 digits) as fallback for check digit mismatches.

    Returns:
        Enrichment statistics.
    """
    stats = {'updated': 0, 'no_match': 0, 'no_match_codes': [], 'total': 0}

    async with db_session.async_session_factory() as session:
        # Load nomenclator into memory (small table, ~9.5K rows)
        result = await session.execute(select(NomenclatorCPV))
        cpv_map = {
            cpv.cod_cpv: {
                'descriere': cpv.descriere,
                'categorie': cpv.categorie_achizitii,
                'clasa': cpv.clasa_produse,
            }
            for cpv in result.scalars().all()
        }
        # Prefix map for fuzzy matching (check digit mismatch)
        prefix_map = {code[:8]: code for code in cpv_map}

        print(f"Loaded {len(cpv_map)} CPV codes for enrichment.")

        # Get all decisions with CPV codes
        result = await session.execute(
            select(DecizieCNSC.id, DecizieCNSC.cod_cpv)
            .where(DecizieCNSC.cod_cpv.isnot(None))
        )
        decisions = result.all()
        stats['total'] = len(decisions)

        for dec_id, cod_cpv in decisions:
            info = cpv_map.get(cod_cpv)

            # Fallback: prefix match (ignore check digit)
            if not info and cod_cpv and len(cod_cpv) >= 8:
                prefix = cod_cpv[:8]
                matched_code = prefix_map.get(prefix)
                if matched_code:
                    info = cpv_map[matched_code]

            if info:
                await session.execute(
                    update(DecizieCNSC)
                    .where(DecizieCNSC.id == dec_id)
                    .values(
                        cpv_descriere=info['descriere'],
                        cpv_categorie=info['categorie'],
                        cpv_clasa=info['clasa'],
                    )
                )
                stats['updated'] += 1
            else:
                stats['no_match'] += 1
                if cod_cpv not in stats['no_match_codes']:
                    stats['no_match_codes'].append(cod_cpv)

        await session.commit()
        print(f"Enriched {stats['updated']}/{stats['total']} decisions.")
        if stats['no_match_codes']:
            print(f"No match for {stats['no_match']} decisions. Codes: {stats['no_match_codes'][:20]}")

    return stats


async def main():
    parser = argparse.ArgumentParser(
        description="Import CPV codes from pipe-delimited text file into the database"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--file", type=str,
        help="Path to local pipe-delimited .txt file",
    )
    source.add_argument(
        "--gcs", type=str,
        default="00_Coduri-CPV.txt",
        help="Filename in GCS bucket (default: 00_Coduri-CPV.txt)",
    )
    parser.add_argument(
        "--bucket", type=str, default="date-expert-app",
        help="GCS bucket name (default: date-expert-app)",
    )
    parser.add_argument(
        "--project", type=str, default="gen-lang-client-0706147575",
        help="GCP project ID (default: gen-lang-client-0706147575)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse + validate only, no DB writes",
    )
    parser.add_argument(
        "--enrich", action="store_true",
        help="Also enrich existing decisions with CPV descriptions after import",
    )
    parser.add_argument(
        "--enrich-only", action="store_true",
        help="Skip import, only enrich decisions from existing nomenclator",
    )
    args = parser.parse_args()

    # Initialize database
    await init_db()

    if args.enrich_only:
        stats = await enrich_decisions()
        print(f"\nEnrichment complete: {stats}")
        return

    # Get file content
    if args.file:
        print(f"Reading local file: {args.file}")
        filepath = Path(args.file)
        if not filepath.exists():
            print(f"ERROR: File not found: {args.file}")
            sys.exit(1)
        content = filepath.read_text(encoding='utf-8')
    else:
        # Download from GCS
        from google.cloud import storage as gcs_storage
        gcs_path = args.gcs
        print(f"Downloading from GCS: gs://{args.bucket}/{gcs_path}...")
        client = gcs_storage.Client(project=args.project)
        bucket = client.bucket(args.bucket)
        blob = bucket.blob(gcs_path)
        try:
            content = blob.download_as_text(encoding='utf-8', timeout=120)
        except Exception as e:
            print(f"ERROR: Failed to download: {e}")
            print(f"Check: gcloud storage ls gs://{args.bucket}/{gcs_path}")
            sys.exit(1)

    print(f"Downloaded {len(content):,} characters, {content.count(chr(10)):,} lines")

    # Parse
    records = parse_pipe_delimited(content)
    print(f"Parsed {len(records):,} CPV codes")

    if not records:
        print("ERROR: No CPV records found. Check file format (expected: pipe-delimited).")
        sys.exit(1)

    # Validate
    vstats = validate_records(records)
    print(f"\n{'='*60}")
    print(f"VALIDATION REPORT")
    print(f"{'='*60}")
    print(f"Total records:    {vstats['total']:,}")
    print(f"Unique codes:     {vstats['unique_codes']:,}")
    print(f"Duplicates:       {vstats['duplicates']}")
    print(f"Empty descrieri:  {vstats['empty_descriere']}")
    print(f"Empty categorii:  {vstats['empty_categorie']}")
    print(f"Invalid categorii:{vstats['invalid_categorie']}")
    print(f"Per categorie:    {vstats['categories']}")
    print(f"Per nivel:        {dict(sorted(vstats['levels'].items()))}")

    # Sample
    print(f"\n{'='*60}")
    print(f"SAMPLE (first 10 + last 5)")
    print(f"{'='*60}")
    all_codes = {r['cod_cpv'][:8]: r['cod_cpv'] for r in records}
    sample = records[:10] + records[-5:]
    print(f"{'Code':<14} {'Lvl':<4} {'Cat':<12} {'Părinte':<14} {'Descriere RO'}")
    print("-" * 120)
    for rec in sample:
        nivel = compute_cpv_level(rec['cod_cpv'])
        parent = compute_cpv_parent(rec['cod_cpv'], all_codes) or '-'
        desc = rec['descriere'][:70]
        cat = (rec['categorie_achizitii'] or '-')[:10]
        print(f"{rec['cod_cpv']:<14} {nivel:<4} {cat:<12} {parent:<14} {desc}")

    if args.dry_run:
        print(f"\n[DRY RUN] No database changes made.")
        return

    # Import
    print(f"\nImporting {len(records):,} CPV codes (truncate + insert)...")
    stats = await import_records(records)
    print(f"\nImport stats: imported={stats['imported']}, failed={stats['failed']}")

    if stats['errors']:
        print(f"\nFirst 10 errors:")
        for err in stats['errors'][:10]:
            print(f"  {err}")

    # Enrich decisions
    if args.enrich:
        print(f"\nEnriching decisions with CPV descriptions...")
        enrich_stats = await enrich_decisions()

    print(f"\n{'='*60}")
    print(f"DONE!")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
