#!/usr/bin/env python3
"""Import Romanian procurement legislation from .md files into the database.

Reads .md files from a GCS bucket (default: date-expert-app/legislatie-ap)
and parses them at MAXIMUM GRANULARITY — each row represents the smallest
independent legal unit:
  - Literă (if the alineat has litere)
  - Alineat (if no litere)
  - Articol (if no alineats)

Supports exact citations like:
    "art. 2 alin. (2) lit. a) din Legea nr. 98/2016"

Schema:
  - acte_normative: master table for legislative acts (FK, not strings)
  - legislatie_fragmente: one row per fragment with embedding + tsvector

Expected .md format (from the actual legislative files):
    # LEGE nr. 98 din 19 mai 2016
    ## Capitolul I - Dispoziții generale
    ### Secțiunea 1 - Obiect, scop și principii
    #### Articolul 1
    Textul articolului...
    #### Articolul 2
    (1) Primul alineat...
    (2) Al doilea alineat:
    * a) prima literă;
    * b) a doua literă;

Features:
- Reads from GCS bucket (same approach as import_decisions_from_gcs.py)
- Litere are separate rows (not JSON) — each is an independent legal unit
- articol_complet field stores the full article text for RAG context
- tsvector keywords for full-text search
- Idempotent: skips entries already imported (by act_id + numar_articol + alineat + litera)
- Retry with exponential backoff on API errors

Usage:
    # Import all .md files from GCS (default bucket/folder)
    DATABASE_URL="..." python scripts/import_legislatie.py --dir legislatie-ap

    # Custom bucket and folder
    DATABASE_URL="..." python scripts/import_legislatie.py --bucket date-expert-app --dir legislatie-ap

    # Import a single file from GCS
    DATABASE_URL="..." python scripts/import_legislatie.py --file "LEGE nr. 98.md"

    # Force reimport (delete + reinsert for a specific act)
    DATABASE_URL="..." python scripts/import_legislatie.py --dir legislatie-ap --force

    # Dry run (parse only, no DB writes)
    DATABASE_URL="..." python scripts/import_legislatie.py --dir legislatie-ap --dry-run
"""

import asyncio
import argparse
import re
import sys
import time
from pathlib import Path
from typing import Optional

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from google.cloud import storage
from sqlalchemy import select, delete, text, func
from app.core.logging import get_logger
from app.db.session import init_db
from app.db import session as db_session
from app.models.decision import ActNormativ, LegislatieFragment
from app.services.embedding import EmbeddingService

logger = get_logger(__name__)

EMBED_BATCH_SIZE = 20

# Map from filename patterns to (tip_act, numar, an, titlu)
ACT_NORMATIV_MAP = {
    ("LEGE", "98"): ("Lege", 98, 2016, "Legea nr. 98/2016 privind achizițiile publice"),
    ("HG", "395"): ("HG", 395, 2016, "HG nr. 395/2016 - Normele metodologice de aplicare a Legii 98/2016"),
    ("LEGE", "99"): ("Lege", 99, 2016, "Legea nr. 99/2016 privind achizițiile sectoriale"),
    ("LEGE", "100"): ("Lege", 100, 2016, "Legea nr. 100/2016 privind concesiunile de lucrări și servicii"),
    ("LEGE", "101"): ("Lege", 101, 2016, "Legea nr. 101/2016 privind remediile și căile de atac"),
    ("HG", "394"): ("HG", 394, 2016, "HG nr. 394/2016 - Normele metodologice de aplicare a Legii 99/2016"),
}


def detect_act_info(filename: str) -> tuple[str, int, int, str]:
    """Detect legislative act info from filename.

    Returns:
        Tuple of (tip_act, numar, an, titlu).
    """
    name = filename.upper()

    for (act_type, act_num), (tip, numar, an, titlu) in ACT_NORMATIV_MAP.items():
        if act_type in name and act_num in name:
            return tip, numar, an, titlu

    # Fallback: try to parse from filename
    m = re.search(r'(LEGE|HG|OUG)\s*(?:nr\.?\s*)?(\d+)', name)
    if m:
        act_type = m.group(1)
        act_num = int(m.group(2))
        y = re.search(r'(\d{4})', name)
        year = int(y.group(1)) if y else 2016
        tip = "Lege" if act_type == "LEGE" else act_type
        return tip, act_num, year, f"{tip} {act_num}/{year}"

    raise ValueError(f"Cannot detect act normativ from filename: {filename}")


def parse_litere(text: str) -> list[dict]:
    """Extract litere (a, b, c...) from alineat text.

    Args:
        text: Text of an alineat that may contain litere.

    Returns:
        List of {"litera": "a", "text": "..."} dicts.
    """
    litere = []
    pattern = re.compile(r'^\s*(?:\*\s+)?([a-zăâîșț](?:\d+)?)\)\s*(.+)', re.MULTILINE)
    for m in pattern.finditer(text):
        litere.append({
            "litera": m.group(1),
            "text": m.group(2).rstrip(';.').strip(),
        })
    return litere


def parse_alineats(article_text: str) -> list[dict]:
    """Split article text into alineats with their litere.

    Args:
        article_text: Full text of one article (without the heading).

    Returns:
        List of alineat dicts with keys: alineat (int|None), text, litere.
    """
    alin_pattern = re.compile(r'^\((\d+)\)\s*', re.MULTILINE)
    alin_starts = list(alin_pattern.finditer(article_text))

    if not alin_starts:
        litere = parse_litere(article_text)
        return [{
            "alineat": None,
            "text": article_text.strip(),
            "litere": litere if litere else [],
        }]

    alineats = []
    for idx, match in enumerate(alin_starts):
        alin_num = int(match.group(1))
        start = match.start()
        end = alin_starts[idx + 1].start() if idx + 1 < len(alin_starts) else len(article_text)

        alin_text = article_text[start:end].strip()
        litere = parse_litere(alin_text)

        alineats.append({
            "alineat": alin_num,
            "text": alin_text,
            "litere": litere if litere else [],
        })

    # Text before first alineat (introductory text)
    if alin_starts[0].start() > 0:
        intro = article_text[:alin_starts[0].start()].strip()
        if intro and len(intro) > 10:
            alineats.insert(0, {
                "alineat": None,
                "text": intro,
                "litere": [],
            })

    return alineats


def parse_legislation(md_text: str) -> list[dict]:
    """Parse a legislative .md file into fragment-level records.

    Each record = smallest independent legal unit (literă > alineat > articol).

    Returns:
        List of dicts with keys: numar_articol, articol, alineat, alineat_text,
        litera, text_fragment, articol_complet, citare, capitol, sectiune.
    """
    lines = md_text.split('\n')
    records = []

    current_capitol = None
    current_sectiune = None
    current_art_num = None
    current_art_lines: list[str] = []

    # Regex patterns for structure
    capitol_re = re.compile(
        r'^##\s+(?:Capitolul|CAPITOLUL|CAP\.)\s+(.+)', re.IGNORECASE
    )
    sectiune_re = re.compile(
        r'^###\s+(?:Sec[tț]iunea|SECȚIUNEA|SEC[ȚT]IUNEA)\s+(.+)', re.IGNORECASE
    )
    articol_re = re.compile(
        r'^####\s+(?:Articolul|Art\.?)\s+(\d+)', re.IGNORECASE
    )

    def flush_article():
        """Process accumulated article lines into fragment records."""
        nonlocal current_art_lines, current_art_num
        if current_art_num is None or not current_art_lines:
            return

        article_text = '\n'.join(current_art_lines).strip()
        if not article_text:
            return

        alineats = parse_alineats(article_text)

        for alin_data in alineats:
            alin_num = alin_data["alineat"]
            alineat_text = f"alin. ({alin_num})" if alin_num is not None else None
            litere = alin_data["litere"]

            if litere:
                # Each litera becomes its own row
                for lit in litere:
                    if alin_num is not None:
                        citare = f"art. {current_art_num} alin. ({alin_num}) lit. {lit['litera']})"
                    else:
                        citare = f"art. {current_art_num} lit. {lit['litera']})"

                    records.append({
                        "numar_articol": current_art_num,
                        "articol": f"art. {current_art_num}",
                        "alineat": alin_num,
                        "alineat_text": alineat_text,
                        "litera": lit["litera"],
                        "text_fragment": lit["text"],
                        "articol_complet": article_text,
                        "citare": citare,
                        "capitol": current_capitol,
                        "sectiune": current_sectiune,
                    })
            else:
                # No litere — fragment is the alineat itself (or whole article)
                if alin_num is not None:
                    citare = f"art. {current_art_num} alin. ({alin_num})"
                else:
                    citare = f"art. {current_art_num}"

                records.append({
                    "numar_articol": current_art_num,
                    "articol": f"art. {current_art_num}",
                    "alineat": alin_num,
                    "alineat_text": alineat_text,
                    "litera": None,
                    "text_fragment": alin_data["text"],
                    "articol_complet": article_text,
                    "citare": citare,
                    "capitol": current_capitol,
                    "sectiune": current_sectiune,
                })

        current_art_lines = []

    for line in lines:
        stripped = line.strip()

        m = capitol_re.match(stripped)
        if m:
            flush_article()
            current_capitol = m.group(1).strip()
            current_sectiune = None
            continue

        m = sectiune_re.match(stripped)
        if m:
            flush_article()
            current_sectiune = m.group(1).strip()
            continue

        m = articol_re.match(stripped)
        if m:
            flush_article()
            current_art_num = int(m.group(1))
            current_art_lines = []
            continue

        if stripped.startswith('#'):
            continue

        if current_art_num is not None:
            current_art_lines.append(line)

    flush_article()

    return records


async def get_or_create_act(
    session,
    tip_act: str,
    numar: int,
    an: int,
    titlu: str,
) -> str:
    """Get existing act_id or create new ActNormativ record.

    Returns:
        UUID string of the act.
    """
    result = await session.execute(
        select(ActNormativ.id).where(
            ActNormativ.tip_act == tip_act,
            ActNormativ.numar == numar,
            ActNormativ.an == an,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    act = ActNormativ(tip_act=tip_act, numar=numar, an=an, titlu=titlu)
    session.add(act)
    await session.flush()
    return act.id


async def import_file(
    filename: str,
    md_text: str,
    embedding_service: EmbeddingService,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    """Import fragment-level records from a single legislative .md file.

    Args:
        filename: Name of the .md file.
        md_text: Content of the .md file.
        embedding_service: Service for generating embeddings.
        force: Delete existing records for this act and reimport.
        dry_run: Only parse and print, don't write to DB.

    Returns:
        Number of records imported.
    """
    tip_act, numar, an, titlu = detect_act_info(filename)
    act_label = f"{tip_act} {numar}/{an}"
    logger.info("importing_legislation", file=filename, act=act_label)
    logger.info("file_read", chars=len(md_text), lines=md_text.count('\n'))

    records = parse_legislation(md_text)
    logger.info("records_parsed", count=len(records), act=act_label)

    if not records:
        logger.warning("no_records_parsed", file=filename)
        return 0

    if dry_run:
        for rec in records:
            lit_str = f" lit. {rec['litera']})" if rec["litera"] else ""
            print(
                f"  {rec['citare']:>45s} | "
                f"{rec['capitol'] or '':>40s} | "
                f"{rec['text_fragment'][:60]}..."
            )
        print(f"\n  Total: {len(records)} fragment-level records from {act_label}")
        return len(records)

    # Database operations
    async with db_session.async_session_factory() as session:
        act_id = await get_or_create_act(session, tip_act, numar, an, titlu)
        await session.commit()

        if force:
            await session.execute(
                delete(LegislatieFragment).where(
                    LegislatieFragment.act_id == act_id
                )
            )
            await session.commit()
            logger.info("deleted_existing", act=act_label)

        # Check which records already exist (by unique constraint components)
        existing = await session.execute(
            select(
                LegislatieFragment.numar_articol,
                LegislatieFragment.alineat,
                LegislatieFragment.litera,
            ).where(
                LegislatieFragment.act_id == act_id
            )
        )
        existing_keys = {
            (row[0], row[1] or 0, row[2] or "")
            for row in existing
        }

        # Filter out records already in DB AND deduplicate parsed records
        # (parser may generate duplicates for same art/alin/litera)
        seen_keys = set(existing_keys)
        new_records = []
        duplicates_skipped = 0
        for r in records:
            key = (r["numar_articol"], r["alineat"] or 0, r["litera"] or "")
            if key in seen_keys:
                if key not in existing_keys:
                    duplicates_skipped += 1
                continue
            seen_keys.add(key)
            new_records.append(r)

        if duplicates_skipped > 0:
            logger.warning(
                "duplicates_in_parsed_records",
                count=duplicates_skipped,
                act=act_label,
            )

        if not new_records:
            logger.info("all_records_exist", act=act_label, total=len(records))
            return 0

        logger.info(
            "importing_new_records",
            new=len(new_records),
            existing=len(existing_keys),
            total=len(records),
        )

        # Generate embeddings in batches
        imported = 0
        for batch_start in range(0, len(new_records), EMBED_BATCH_SIZE):
            batch = new_records[batch_start:batch_start + EMBED_BATCH_SIZE]

            # Embedding text: citation + context for better semantic match
            embed_texts = []
            for r in batch:
                parts = [f"{act_label} {r['citare']}"]
                if r["capitol"]:
                    parts.append(f"Capitol: {r['capitol']}")
                if r["sectiune"]:
                    parts.append(f"Secțiune: {r['sectiune']}")
                parts.append(r["text_fragment"])
                embed_texts.append("\n".join(parts))

            # Generate embeddings with retry
            embeddings = None
            for attempt in range(3):
                try:
                    embeddings = await embedding_service.embed_batch(embed_texts)
                    break
                except Exception as e:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "embedding_retry",
                        attempt=attempt + 1,
                        error=str(e),
                        wait=wait,
                    )
                    if attempt < 2:
                        await asyncio.sleep(wait)
                    else:
                        raise

            if not embeddings:
                logger.error("embedding_failed", batch_start=batch_start)
                continue

            for rec_data, emb in zip(batch, embeddings):
                # Build tsvector from fragment text + citation
                keywords_text = f"{rec_data['citare']} {rec_data['text_fragment']}"

                record = LegislatieFragment(
                    act_id=act_id,
                    numar_articol=rec_data["numar_articol"],
                    articol=rec_data["articol"],
                    alineat=rec_data["alineat"],
                    alineat_text=rec_data["alineat_text"],
                    litera=rec_data["litera"],
                    text_fragment=rec_data["text_fragment"],
                    articol_complet=rec_data["articol_complet"],
                    citare=rec_data["citare"],
                    capitol=rec_data["capitol"],
                    sectiune=rec_data["sectiune"],
                    embedding=emb,
                )
                session.add(record)
                imported += 1

            await session.commit()

            # Update tsvector keywords for this batch using SQL
            # (tsvector generation needs to happen server-side for proper Romanian config)
            await session.execute(
                text("""
                    UPDATE legislatie_fragmente
                    SET keywords = to_tsvector('romanian', text_fragment || ' ' || citare)
                    WHERE act_id = :act_id AND keywords IS NULL
                """),
                {"act_id": act_id},
            )
            await session.commit()

            logger.info(
                "batch_committed",
                batch=batch_start // EMBED_BATCH_SIZE + 1,
                imported_so_far=imported,
            )

            if batch_start + EMBED_BATCH_SIZE < len(new_records):
                await asyncio.sleep(1.0)

    logger.info("import_complete", act=act_label, imported=imported)
    return imported


def connect_to_gcs(
    bucket_name: str,
    project_id: Optional[str] = None,
) -> storage.Bucket:
    """Connect to GCS bucket.

    Args:
        bucket_name: Name of the GCS bucket.
        project_id: GCP project ID (uses default credentials if None).

    Returns:
        GCS Bucket object.
    """
    logger.info("gcs_connecting", bucket=bucket_name, project=project_id)
    if project_id:
        client = storage.Client(project=project_id)
    else:
        client = storage.Client()
    bucket = client.bucket(bucket_name)
    logger.info("gcs_connected", bucket=bucket_name)
    return bucket


def list_md_files(bucket: storage.Bucket, folder: str) -> list[str]:
    """List all .md files in a GCS folder.

    Args:
        bucket: GCS Bucket object.
        folder: Folder prefix in the bucket.

    Returns:
        List of blob names.
    """
    prefix = f"{folder}/" if folder else ""
    blobs = bucket.list_blobs(prefix=prefix, timeout=300)
    files = [blob.name for blob in blobs if blob.name.endswith('.md')]
    logger.info("gcs_files_listed", count=len(files), prefix=prefix)
    return sorted(files)


def download_file(bucket: storage.Bucket, blob_name: str) -> str:
    """Download a file from GCS and return its content.

    Args:
        bucket: GCS Bucket object.
        blob_name: Name of the blob (file path in GCS).

    Returns:
        File content as string.
    """
    blob = bucket.blob(blob_name)
    try:
        content = blob.download_as_text(encoding='utf-8', timeout=120)
    except UnicodeDecodeError:
        content = blob.download_as_text(encoding='latin-1', timeout=120)
    return content


async def main():
    parser = argparse.ArgumentParser(
        description="Import Romanian procurement legislation from GCS into the database"
    )
    parser.add_argument(
        "--bucket",
        default="date-expert-app",
        help="GCS bucket name (default: date-expert-app)",
    )
    parser.add_argument(
        "--dir", type=str,
        default="legislatie-ap",
        help="Folder in GCS bucket containing .md files (default: legislatie-ap)",
    )
    parser.add_argument(
        "--file", type=str,
        help="Single .md filename to import from GCS folder",
    )
    parser.add_argument(
        "--project",
        default="gen-lang-client-0706147575",
        help="GCP project ID",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Delete existing records and reimport",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse only, print results without writing to DB",
    )
    args = parser.parse_args()

    # Connect to GCS
    try:
        bucket = connect_to_gcs(args.bucket, args.project)
    except Exception as e:
        print(f"ERROR: Could not connect to GCS: {e}")
        print("Make sure you have:")
        print("1. gcloud CLI installed and authenticated")
        print("2. Proper permissions to access the bucket")
        print("3. GOOGLE_APPLICATION_CREDENTIALS set (if using service account)")
        sys.exit(1)

    # List files from GCS
    if args.file:
        # Single file — construct the full blob path
        blob_name = f"{args.dir}/{args.file}" if args.dir else args.file
        blob_names = [blob_name]
    else:
        blob_names = list_md_files(bucket, args.dir)

    if not blob_names:
        print("No .md files found in GCS")
        sys.exit(1)

    print(f"Found {len(blob_names)} legislation file(s) in gs://{args.bucket}/{args.dir}/:")
    for b in blob_names:
        print(f"  - {Path(b).name}")
    print()

    if not args.dry_run:
        await init_db()

    from app.services.llm.gemini import GeminiProvider
    llm = GeminiProvider()
    embedding_service = EmbeddingService(llm_provider=llm)

    total_imported = 0
    start = time.time()

    for blob_name in blob_names:
        filename = Path(blob_name).name
        print(f"Downloading {filename} from GCS...")
        try:
            md_text = download_file(bucket, blob_name)
        except Exception as e:
            print(f"Warning: Failed to download {blob_name}: {e}, skipping")
            continue

        count = await import_file(
            filename, md_text, embedding_service,
            force=args.force, dry_run=args.dry_run,
        )
        total_imported += count

    elapsed = time.time() - start
    print(f"\nDone! Imported {total_imported} fragment-level records in {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
