#!/usr/bin/env python3
"""Import CPV (Common Procurement Vocabulary) codes from GCS into the database.

Reads a fixed-width text file from GCS bucket and populates the nomenclator_cpv table.

The source file has repeated header lines and columns:
  Cod_CPV | Cod_CPV_descriere | Categorie achizitii | Clasa produse standardizate

Multi-line descriptions (wrapped with %%) are joined into single lines.

Usage:
    # Import from GCS (default bucket/file)
    DATABASE_URL="..." python scripts/import_cpv.py

    # Custom bucket/file
    DATABASE_URL="..." python scripts/import_cpv.py --bucket date-expert-app --file coduri-cpv/nomenclator_cpv.txt

    # Dry run (parse only, no DB writes)
    DATABASE_URL="..." python scripts/import_cpv.py --dry-run

    # Force reimport (delete all + reinsert)
    DATABASE_URL="..." python scripts/import_cpv.py --force
"""

import asyncio
import argparse
import re
import sys
from pathlib import Path
from typing import Optional

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from google.cloud import storage
from sqlalchemy import select, delete, text
from app.core.logging import get_logger
from app.db.session import init_db
from app.db import session as db_session
from app.models.decision import NomenclatorCPV

logger = get_logger(__name__)

# CPV code pattern: 8 digits + check digit (e.g., 03000000-1)
CPV_CODE_RE = re.compile(r'^(\d{8}-\d)\s+')


def compute_cpv_parent(cod_cpv: str) -> Optional[str]:
    """Compute parent CPV code by zeroing the last non-zero pair.

    CPV hierarchy:
        03000000-1  (division, level 1)
        03100000-2  (group, level 2)
        03110000-5  (class, level 3)
        03111000-2  (category, level 4)
        03111100-3  (subcategory, level 5)

    Args:
        cod_cpv: CPV code like "03111100-3".

    Returns:
        Parent CPV code or None for top-level codes.
    """
    digits = cod_cpv[:8]  # Without check digit
    # Find the rightmost non-zero pair (going from right to left in positions 2-7)
    for i in range(7, 1, -1):
        if digits[i] != '0':
            # Zero out from this position to the right
            parent_digits = digits[:i] + '0' * (8 - i)
            # We don't know the check digit for the parent, use X as placeholder
            return f"{parent_digits}-X"
    return None


def compute_cpv_level(cod_cpv: str) -> int:
    """Compute the hierarchical level of a CPV code.

    Level is determined by how many significant digit pairs there are:
        03000000 → level 1 (division)
        03100000 → level 2 (group)
        03110000 → level 3 (class)
        03111000 → level 4 (category)
        03111100 → level 5 (subcategory)
        03111110 → level 6
        03111111 → level 7

    Args:
        cod_cpv: CPV code like "03111100-3".

    Returns:
        Level (1-7).
    """
    digits = cod_cpv[:8]
    level = 1
    for i in range(2, 8):
        if digits[i] != '0':
            level = i
    return level


def parse_cpv_file(text_content: str) -> list[dict]:
    """Parse the fixed-width CPV text file.

    Handles:
    - Repeated header lines (skipped)
    - Multi-line descriptions joined with spaces
    - %% as line continuation markers within descriptions

    Args:
        text_content: Raw text content of the CPV file.

    Returns:
        List of dicts with keys: cod_cpv, descriere, categorie_achizitii, clasa_produse.
    """
    lines = text_content.split('\n')
    records = []

    # Current record being built (for multi-line descriptions)
    current_code = None
    current_descriere_parts = []
    current_categorie = None
    current_clasa = None

    def flush_record():
        """Save the current record if we have one."""
        nonlocal current_code, current_descriere_parts, current_categorie, current_clasa
        if current_code and current_descriere_parts:
            descriere = ' '.join(current_descriere_parts)
            # Clean up %% artifacts
            descriere = descriere.replace('%%', ',')
            descriere = re.sub(r'\s+', ' ', descriere).strip()
            # Remove trailing (Rev.2) or similar
            descriere = re.sub(r'\s*\(Rev\.\d+\)\s*$', '', descriere).strip()

            records.append({
                'cod_cpv': current_code,
                'descriere': descriere,
                'categorie_achizitii': current_categorie,
                'clasa_produse': current_clasa,
            })

        current_code = None
        current_descriere_parts = []
        current_categorie = None
        current_clasa = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Skip header lines
        if stripped.startswith('Cod_CPV') or stripped.startswith('achizitii'):
            flush_record()
            continue

        # Try to match a line starting with a CPV code
        m = CPV_CODE_RE.match(stripped)
        if m:
            # Flush previous record
            flush_record()

            current_code = m.group(1)
            rest = stripped[m.end():].strip()

            # Parse the rest of the line — it contains descriere, categorie, clasa
            # The challenge is these are fixed-width but widths vary
            # Strategy: categorie is a short word (Furnizare/Servicii/Lucrari)
            # and clasa is at the end
            parts = _parse_rest_columns(rest)
            current_descriere_parts = [parts['descriere']]
            current_categorie = parts.get('categorie')
            current_clasa = parts.get('clasa')
        else:
            # Continuation line (multi-line description or category/class)
            if current_code is not None:
                # This is a continuation of the description
                parts = _parse_rest_columns(stripped)
                if parts['descriere']:
                    current_descriere_parts.append(parts['descriere'])
                if parts.get('categorie') and not current_categorie:
                    current_categorie = parts['categorie']
                if parts.get('clasa') and not current_clasa:
                    current_clasa = parts['clasa']

    flush_record()
    return records


def _parse_rest_columns(text: str) -> dict:
    """Parse the non-code portion of a CPV line into descriere, categorie, clasa.

    Uses the known category values (Furnizare, Servicii, Lucrari) as anchors.

    Args:
        text: The text after the CPV code.

    Returns:
        Dict with keys: descriere, categorie, clasa.
    """
    result = {'descriere': '', 'categorie': None, 'clasa': None}

    # Known categories
    cat_pattern = re.compile(
        r'\b(Furnizare|Servicii|Lucr[aă]ri)\b',
        re.IGNORECASE,
    )

    m = cat_pattern.search(text)
    if m:
        result['descriere'] = text[:m.start()].strip()
        result['categorie'] = m.group(1).capitalize()
        # Normalize "Lucrari" → "Lucrări"
        if result['categorie'].lower().startswith('lucr'):
            result['categorie'] = 'Lucrări'

        clasa = text[m.end():].strip()
        if clasa:
            result['clasa'] = clasa
    else:
        result['descriere'] = text.strip()

    return result


async def import_cpv_records(
    records: list[dict],
    force: bool = False,
) -> dict:
    """Import parsed CPV records into the database.

    Args:
        records: List of CPV record dicts.
        force: Delete all existing records before importing.

    Returns:
        Dict with import statistics.
    """
    stats = {
        'total_parsed': len(records),
        'imported': 0,
        'already_existed': 0,
        'updated': 0,
        'failed': 0,
        'errors': [],
    }

    async with db_session.async_session_factory() as session:
        if force:
            await session.execute(delete(NomenclatorCPV))
            await session.commit()
            logger.info("deleted_all_cpv_records")

        # Load existing codes
        result = await session.execute(select(NomenclatorCPV.cod_cpv))
        existing_codes = {row[0] for row in result.all()}
        logger.info("existing_cpv_codes", count=len(existing_codes))

        for rec in records:
            try:
                cod = rec['cod_cpv']
                parent = compute_cpv_parent(cod)
                nivel = compute_cpv_level(cod)

                if cod in existing_codes and not force:
                    stats['already_existed'] += 1
                    continue

                cpv = NomenclatorCPV(
                    cod_cpv=cod,
                    descriere=rec['descriere'],
                    categorie_achizitii=rec['categorie_achizitii'],
                    clasa_produse=rec['clasa_produse'],
                    cod_parinte=parent,
                    nivel=nivel,
                )

                async with session.begin_nested():
                    await session.merge(cpv)

                stats['imported'] += 1

            except Exception as e:
                stats['failed'] += 1
                stats['errors'].append(f"{rec.get('cod_cpv', '?')}: {e}")
                logger.error("cpv_import_failed", cod=rec.get('cod_cpv'), error=str(e))

        await session.commit()
        logger.info("cpv_import_committed", imported=stats['imported'])

    return stats


async def main():
    parser = argparse.ArgumentParser(
        description="Import CPV codes from GCS into the database"
    )
    parser.add_argument(
        "--bucket",
        default="date-expert-app",
        help="GCS bucket name (default: date-expert-app)",
    )
    parser.add_argument(
        "--file", type=str,
        default="00_Coduri-CPV.txt",
        help="Path to CPV file in GCS bucket (default: 00_Coduri-CPV.txt)",
    )
    parser.add_argument(
        "--project",
        default="gen-lang-client-0706147575",
        help="GCP project ID",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Delete all existing CPV records and reimport",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse only, print results without writing to DB",
    )
    parser.add_argument(
        "--list-files", action="store_true",
        help="List all files in the GCS bucket/folder and exit (useful to find the CPV file)",
    )
    args = parser.parse_args()

    # Connect to GCS
    logger.info("gcs_connecting", bucket=args.bucket, project=args.project)
    try:
        if args.project:
            client = storage.Client(project=args.project)
        else:
            client = storage.Client()
        bucket = client.bucket(args.bucket)
        logger.info("gcs_connected", bucket=args.bucket)
    except Exception as e:
        print(f"ERROR: Could not connect to GCS: {e}")
        print("Make sure you have:")
        print("1. gcloud CLI installed and authenticated")
        print("2. Proper permissions to access the bucket")
        print("3. GOOGLE_APPLICATION_CREDENTIALS set (if using service account)")
        sys.exit(1)

    # List files mode
    if args.list_files:
        print(f"Files in gs://{args.bucket}/:")
        blobs = bucket.list_blobs(timeout=300)
        for blob in blobs:
            print(f"  {blob.name} ({blob.size} bytes)")
        return

    # Download CPV file
    print(f"Downloading gs://{args.bucket}/{args.file}...")
    blob = bucket.blob(args.file)
    try:
        content = blob.download_as_text(encoding='utf-8', timeout=120)
    except UnicodeDecodeError:
        content = blob.download_as_text(encoding='latin-1', timeout=120)
    except Exception as e:
        print(f"ERROR: Failed to download {args.file}: {e}")
        print(f"\nUse --list-files to see available files in the bucket.")
        print(f"Or specify the correct path with --file <path>")
        sys.exit(1)

    print(f"Downloaded {len(content)} characters")

    # Parse
    records = parse_cpv_file(content)
    print(f"Parsed {len(records)} CPV codes")

    if not records:
        print("No CPV records found. Check the file format.")
        sys.exit(1)

    if args.dry_run:
        # Print sample
        print(f"\nSample records (first 20):")
        print(f"{'Cod CPV':<15} {'Categorie':<12} {'Descriere':<50} {'Clasa'}")
        print("-" * 120)
        for rec in records[:20]:
            print(
                f"{rec['cod_cpv']:<15} "
                f"{(rec['categorie_achizitii'] or '-'):<12} "
                f"{rec['descriere'][:50]:<50} "
                f"{rec['clasa_produse'] or '-'}"
            )
        if len(records) > 20:
            print(f"  ... and {len(records) - 20} more")

        # Stats
        categories = {}
        for rec in records:
            cat = rec['categorie_achizitii'] or 'N/A'
            categories[cat] = categories.get(cat, 0) + 1
        print(f"\nBy category:")
        for cat, count in sorted(categories.items()):
            print(f"  {cat}: {count}")
        return

    # Initialize DB
    await init_db()

    # Import
    stats = await import_cpv_records(records, force=args.force)

    # Print summary
    print("\n" + "=" * 60)
    print("CPV IMPORT SUMMARY")
    print("=" * 60)
    print(f"Total parsed:     {stats['total_parsed']}")
    print(f"Imported:         {stats['imported']}")
    print(f"Already existed:  {stats['already_existed']}")
    print(f"Failed:           {stats['failed']}")

    if stats['errors']:
        print(f"\nErrors ({len(stats['errors'])}):")
        for error in stats['errors'][:10]:
            print(f"  - {error}")
        if len(stats['errors']) > 10:
            print(f"  ... and {len(stats['errors']) - 10} more")

    print("=" * 60)

    if stats['failed'] > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
