#!/usr/bin/env python3
"""Import Romanian procurement legislation from .md files into the database.

Parses legislative .md files at ALINEAT granularity — the atomic citation unit
in Romanian law. Each row in the DB represents one alineat (or a full article
if it has no alineats).

Supports exact citations like:
    "art. 2 alin. (2) lit. a) și b) din Legea nr. 98/2016"

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
- Parses articles, alineats, and litere from markdown
- Generates embeddings per alineat for precise vector search
- Idempotent: skips entries already imported (by act_normativ + citare)
- Retry with exponential backoff on API errors

Usage:
    # Import all .md files from the legislation directory
    DATABASE_URL="..." python scripts/import_legislatie.py --dir date-expert-app/legislatie-ap

    # Import a single file
    DATABASE_URL="..." python scripts/import_legislatie.py --file "path/to/LEGE nr. 98.md"

    # Force reimport (delete + reinsert)
    DATABASE_URL="..." python scripts/import_legislatie.py --dir ... --force

    # Dry run (parse only, no DB writes)
    DATABASE_URL="..." python scripts/import_legislatie.py --dir ... --dry-run
"""

import asyncio
import argparse
import re
import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select, delete
from app.core.logging import get_logger
from app.db.session import init_db
from app.db import session as db_session
from app.models.decision import ArticolLegislatie
from app.services.embedding import EmbeddingService

logger = get_logger(__name__)

EMBED_BATCH_SIZE = 20


def detect_act_normativ(filename: str) -> str:
    """Detect the legislative act from filename."""
    name = filename.upper()
    if "LEGE" in name and "98" in name:
        return "Legea 98/2016"
    elif "HG" in name and "395" in name:
        return "HG 395/2016"
    elif "LEGE" in name and "99" in name:
        return "Legea 99/2016"
    elif "LEGE" in name and "100" in name:
        return "Legea 100/2016"
    elif "LEGE" in name and "101" in name:
        return "Legea 101/2016"
    elif "HG" in name and "394" in name:
        return "HG 394/2016"
    else:
        m = re.search(r'(LEGE|HG|OUG)\s*(?:nr\.?\s*)?(\d+)', name)
        if m:
            act_type = m.group(1)
            act_num = m.group(2)
            y = re.search(r'(\d{4})', name)
            year = y.group(1) if y else "2016"
            if act_type == "LEGE":
                return f"Legea {act_num}/{year}"
            return f"{act_type} {act_num}/{year}"
        return filename


def parse_litere(text: str) -> list[dict]:
    """Extract litere (a, b, c...) from alineat text.

    Handles formats:
    - "* a) text;"
    - "a) text;"
    - Lines starting with letter followed by )

    Args:
        text: Text of an alineat that may contain litere.

    Returns:
        List of {"litera": "a", "text": "..."} dicts.
    """
    litere = []
    # Pattern: optional "* " then letter followed by ) then text
    pattern = re.compile(r'^\s*(?:\*\s+)?([a-zăâîșț](?:\d+)?)\)\s*(.+)', re.MULTILINE)
    for m in pattern.finditer(text):
        litere.append({
            "litera": m.group(1),
            "text": m.group(2).rstrip(';.').strip(),
        })
    return litere


def parse_alineats(article_text: str) -> list[dict]:
    """Split article text into alineats.

    Handles:
    - "(1) First alineat text..." — numbered alineats
    - Articles with no alineats (entire text = one entry)
    - Alineats containing litere (* a), * b), etc.)

    Args:
        article_text: Full text of one article (without the "Articolul N" heading).

    Returns:
        List of alineat dicts with keys: alineat (int|None), text, litere.
    """
    # Check if the text contains numbered alineats
    alin_pattern = re.compile(r'^\((\d+)\)\s*', re.MULTILINE)
    alin_starts = list(alin_pattern.finditer(article_text))

    if not alin_starts:
        # No alineats — entire article is one unit
        litere = parse_litere(article_text)
        return [{
            "alineat": None,
            "text": article_text.strip(),
            "litere": litere if litere else None,
        }]

    alineats = []
    for idx, match in enumerate(alin_starts):
        alin_num = int(match.group(1))
        start = match.start()

        # End = start of next alineat or end of text
        if idx + 1 < len(alin_starts):
            end = alin_starts[idx + 1].start()
        else:
            end = len(article_text)

        alin_text = article_text[start:end].strip()
        litere = parse_litere(alin_text)

        alineats.append({
            "alineat": alin_num,
            "text": alin_text,
            "litere": litere if litere else None,
        })

    # Check if there's text before the first alineat (introductory text)
    if alin_starts[0].start() > 0:
        intro = article_text[:alin_starts[0].start()].strip()
        if intro and len(intro) > 10:
            # This is unusual but handle it as alineat 0
            alineats.insert(0, {
                "alineat": None,
                "text": intro,
                "litere": None,
            })

    return alineats


def parse_legislation(text: str, act_normativ: str) -> list[dict]:
    """Parse a legislative .md file into alineat-level records.

    Processes the markdown structure:
    - ## Capitolul ... → capitol
    - ### Secțiunea ... → sectiune
    - #### Articolul N → article boundary
    - (N) ... → alineat
    - * a) ... → litera

    Args:
        text: Full text of the .md file.
        act_normativ: Normalized act name.

    Returns:
        List of dicts ready for DB insertion.
    """
    lines = text.split('\n')
    records = []

    current_capitol = None
    current_sectiune = None
    current_art_num = None
    current_art_lines: list[str] = []

    def flush_article():
        """Process accumulated article lines into records."""
        nonlocal current_art_lines, current_art_num
        if current_art_num is None or not current_art_lines:
            return

        article_text = '\n'.join(current_art_lines).strip()
        if not article_text:
            return

        alineats = parse_alineats(article_text)

        for alin_data in alineats:
            alin_num = alin_data["alineat"]

            if alin_num is not None:
                citare = f"art. {current_art_num} alin. ({alin_num})"
                alineat_text = f"alin. ({alin_num})"
            else:
                citare = f"art. {current_art_num}"
                alineat_text = None

            records.append({
                "act_normativ": act_normativ,
                "numar_articol": current_art_num,
                "articol": f"art. {current_art_num}",
                "alineat": alin_num,
                "alineat_text": alineat_text,
                "litere": alin_data["litere"],
                "text_integral": alin_data["text"],
                "citare": citare,
                "capitol": current_capitol,
                "sectiune": current_sectiune,
            })

        current_art_lines = []

    # Regex patterns for structure
    capitol_re = re.compile(
        r'^##\s+(?:Capitolul|CAPITOLUL|CAP\.)\s+(.+)',
        re.IGNORECASE
    )
    sectiune_re = re.compile(
        r'^###\s+(?:Sec[tț]iunea|SECȚIUNEA|SEC[ȚT]IUNEA)\s+(.+)',
        re.IGNORECASE
    )
    articol_re = re.compile(
        r'^####\s+(?:Articolul|Art\.?)\s+(\d+)',
        re.IGNORECASE
    )

    for line in lines:
        stripped = line.strip()

        # Check for chapter heading
        m = capitol_re.match(stripped)
        if m:
            flush_article()
            current_capitol = m.group(1).strip()
            current_sectiune = None  # reset section on new chapter
            continue

        # Check for section heading
        m = sectiune_re.match(stripped)
        if m:
            flush_article()
            current_sectiune = m.group(1).strip()
            continue

        # Check for article heading
        m = articol_re.match(stripped)
        if m:
            flush_article()
            current_art_num = int(m.group(1))
            current_art_lines = []
            continue

        # Skip top-level headings (# LEGE nr. ...)
        if stripped.startswith('#'):
            continue

        # Accumulate article content
        if current_art_num is not None:
            current_art_lines.append(line)

    # Don't forget the last article
    flush_article()

    return records


async def import_file(
    filepath: Path,
    embedding_service: EmbeddingService,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    """Import alineat-level records from a single legislative .md file.

    Args:
        filepath: Path to the .md file.
        embedding_service: Service for generating embeddings.
        force: Delete existing records for this act and reimport.
        dry_run: Only parse and print, don't write to DB.

    Returns:
        Number of records imported.
    """
    act_normativ = detect_act_normativ(filepath.name)
    logger.info("importing_legislation", file=filepath.name, act=act_normativ)

    text = filepath.read_text(encoding='utf-8')
    logger.info("file_read", chars=len(text), lines=text.count('\n'))

    records = parse_legislation(text, act_normativ)
    logger.info("records_parsed", count=len(records), act=act_normativ)

    if not records:
        logger.warning("no_records_parsed", file=filepath.name)
        return 0

    if dry_run:
        for rec in records:
            litere_str = ""
            if rec["litere"]:
                litere_str = f" [lit. {', '.join(l['litera'] for l in rec['litere'])}]"
            print(
                f"  {rec['citare']:>30s}{litere_str:>20s} | "
                f"{rec['capitol'] or '':>40s} | "
                f"{rec['text_integral'][:60]}..."
            )
        print(f"\n  Total: {len(records)} alineat-level records from {act_normativ}")
        return len(records)

    # Database operations
    async with db_session.async_session() as session:
        if force:
            await session.execute(
                delete(ArticolLegislatie).where(
                    ArticolLegislatie.act_normativ == act_normativ
                )
            )
            await session.commit()
            logger.info("deleted_existing", act=act_normativ)

        # Check which records already exist (by citare)
        existing = await session.execute(
            select(ArticolLegislatie.citare).where(
                ArticolLegislatie.act_normativ == act_normativ
            )
        )
        existing_citari = {row[0] for row in existing}

        new_records = [r for r in records if r["citare"] not in existing_citari]
        if not new_records:
            logger.info("all_records_exist", act=act_normativ, total=len(records))
            return 0

        logger.info(
            "importing_new_records",
            new=len(new_records),
            existing=len(existing_citari),
            total=len(records),
        )

        # Generate embeddings in batches
        imported = 0
        for batch_start in range(0, len(new_records), EMBED_BATCH_SIZE):
            batch = new_records[batch_start:batch_start + EMBED_BATCH_SIZE]

            # Embedding text: full citation context for better semantic match
            embed_texts = []
            for r in batch:
                parts = [f"{r['act_normativ']} {r['citare']}"]
                if r["capitol"]:
                    parts.append(f"Capitol: {r['capitol']}")
                if r["sectiune"]:
                    parts.append(f"Secțiune: {r['sectiune']}")
                parts.append(r["text_integral"])
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
                record = ArticolLegislatie(
                    act_normativ=rec_data["act_normativ"],
                    numar_articol=rec_data["numar_articol"],
                    articol=rec_data["articol"],
                    alineat=rec_data["alineat"],
                    alineat_text=rec_data["alineat_text"],
                    litere=rec_data["litere"],
                    text_integral=rec_data["text_integral"],
                    citare=rec_data["citare"],
                    capitol=rec_data["capitol"],
                    sectiune=rec_data["sectiune"],
                    embedding=emb,
                )
                session.add(record)
                imported += 1

            await session.commit()
            logger.info(
                "batch_committed",
                batch=batch_start // EMBED_BATCH_SIZE + 1,
                imported_so_far=imported,
            )

            if batch_start + EMBED_BATCH_SIZE < len(new_records):
                await asyncio.sleep(1.0)

    logger.info("import_complete", act=act_normativ, imported=imported)
    return imported


async def main():
    parser = argparse.ArgumentParser(
        description="Import Romanian procurement legislation into the database"
    )
    parser.add_argument(
        "--dir", type=str,
        help="Directory containing .md legislation files",
    )
    parser.add_argument(
        "--file", type=str,
        help="Single .md file to import",
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

    if not args.dir and not args.file:
        parser.error("Either --dir or --file is required")

    files: list[Path] = []
    if args.file:
        files.append(Path(args.file))
    if args.dir:
        dir_path = Path(args.dir)
        if not dir_path.exists():
            print(f"Error: Directory {dir_path} does not exist")
            sys.exit(1)
        files.extend(sorted(dir_path.glob("*.md")))

    if not files:
        print("No .md files found")
        sys.exit(1)

    print(f"Found {len(files)} legislation file(s):")
    for f in files:
        print(f"  - {f.name}")
    print()

    if not args.dry_run:
        await init_db()

    from app.services.llm.gemini import GeminiProvider
    llm = GeminiProvider()
    embedding_service = EmbeddingService(llm_provider=llm)

    total_imported = 0
    start = time.time()

    for filepath in files:
        if not filepath.exists():
            print(f"Warning: {filepath} does not exist, skipping")
            continue
        count = await import_file(
            filepath, embedding_service,
            force=args.force, dry_run=args.dry_run,
        )
        total_imported += count

    elapsed = time.time() - start
    print(f"\nDone! Imported {total_imported} alineat-level records in {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
