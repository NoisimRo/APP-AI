#!/usr/bin/env python3
"""Import Romanian procurement legislation from .md files into the database.

Parses legislative .md files (Legea 98/2016, HG 395/2016, etc.) article by
article and stores them in the `articole_legislatie` table with embeddings.
This enables vector search grounding in the Red Flags Detector.

Features:
- Parses articles from markdown files using regex patterns
- Tracks chapter/section context for each article
- Generates embeddings per article for vector search
- Idempotent: skips articles already imported (by act_normativ + numar_articol)
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

# Embedding batch size (texts per API call)
EMBED_BATCH_SIZE = 20


def detect_act_normativ(filename: str) -> str:
    """Detect the legislative act from filename.

    Args:
        filename: Name of the .md file.

    Returns:
        Normalized act name like "Legea 98/2016" or "HG 395/2016".
    """
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
        # Try to extract from filename
        m = re.search(r'(LEGE|HG|OUG)\s*(?:nr\.?\s*)?(\d+)', name)
        if m:
            act_type = m.group(1)
            act_num = m.group(2)
            # Try to find year
            y = re.search(r'(\d{4})', name)
            year = y.group(1) if y else "2016"
            if act_type == "LEGE":
                return f"Legea {act_num}/{year}"
            else:
                return f"{act_type} {act_num}/{year}"
        return filename


def parse_articles(text: str, act_normativ: str) -> list[dict]:
    """Parse a legislative text into individual articles.

    Handles patterns like:
    - "Art. 178" / "Art. 178." / "ART. 178"
    - "Articolul 178"
    - "Art. 178 -" (with dash separator)
    - Multi-line articles with alineaturi

    Args:
        text: Full text of the legislative act.
        act_normativ: Name of the act (e.g., "Legea 98/2016").

    Returns:
        List of parsed article dicts.
    """
    articles = []
    current_capitol = None
    current_sectiune = None

    # Track chapters and sections
    # Patterns: "CAPITOLUL I", "CAPITOLUL II - Titlu", "Secțiunea 1", "SECȚIUNEA a 2-a"
    capitol_pattern = re.compile(
        r'^#+\s*(?:CAPITOLUL|CAP\.)\s+(.+?)$|'
        r'^(?:CAPITOLUL|CAP\.)\s+(.+?)$',
        re.MULTILINE | re.IGNORECASE
    )
    sectiune_pattern = re.compile(
        r'^#+\s*(?:SECȚIUNEA|SEC[ȚT]IUNEA|Secțiunea|Sectiunea)\s+(.+?)$|'
        r'^(?:SECȚIUNEA|SEC[ȚT]IUNEA|Secțiunea|Sectiunea)\s+(.+?)$',
        re.MULTILINE | re.IGNORECASE
    )

    # Split text into lines for processing
    lines = text.split('\n')

    # First pass: find all article start positions
    # Pattern matches: "Art. 123", "ART. 123", "Articolul 123"
    art_pattern = re.compile(
        r'^(?:#+\s*)?'  # optional markdown heading
        r'(?:Art(?:icolul)?\.?\s*)'  # "Art." or "Articolul"
        r'(\d+)'  # article number
        r'(?:\^(\d+))?'  # optional superscript like Art. 2^1
        r'\s*'
        r'(.*)',  # rest of line (may contain title after dash)
        re.IGNORECASE
    )

    # Build a list of (line_index, article_number, title_hint)
    article_starts = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = art_pattern.match(stripped)
        if m:
            art_num = int(m.group(1))
            superscript = m.group(2)
            rest = m.group(3).strip()

            # Extract title if present (after dash)
            title = None
            if rest.startswith('-') or rest.startswith('–') or rest.startswith('—'):
                title = rest.lstrip('-–— ').strip()
                # Remove trailing period
                if title.endswith('.'):
                    title = title[:-1]

            art_label = f"art. {art_num}"
            if superscript:
                art_label = f"art. {art_num}^{superscript}"
                art_num = art_num * 100 + int(superscript)  # for sorting

            article_starts.append((i, art_num, art_label, title))

    if not article_starts:
        logger.warning("no_articles_found", act=act_normativ)
        return []

    # Second pass: extract text and track chapters/sections
    for idx, (start_line, art_num, art_label, title) in enumerate(article_starts):
        # Determine end line (start of next article or end of file)
        if idx + 1 < len(article_starts):
            end_line = article_starts[idx + 1][0]
        else:
            end_line = len(lines)

        # Update chapter/section context by scanning lines before this article
        # (look back from current article to previous article or start)
        lookback_start = article_starts[idx - 1][0] if idx > 0 else 0
        for j in range(lookback_start, start_line):
            line = lines[j].strip()
            cm = capitol_pattern.match(line)
            if cm:
                current_capitol = (cm.group(1) or cm.group(2)).strip()
            sm = sectiune_pattern.match(line)
            if sm:
                current_sectiune = (sm.group(1) or sm.group(2)).strip()

        # Extract article text (from the Art. line to the next article)
        article_lines = lines[start_line:end_line]
        article_text = '\n'.join(article_lines).strip()

        # Clean up: remove trailing empty lines and horizontal rules
        article_text = re.sub(r'\n-{3,}\s*$', '', article_text).strip()

        if len(article_text) < 10:
            continue

        articles.append({
            'act_normativ': act_normativ,
            'numar_articol': art_num,
            'articol': art_label,
            'titlu_articol': title,
            'text_integral': article_text,
            'capitol': current_capitol,
            'sectiune': current_sectiune,
        })

    return articles


async def import_file(
    filepath: Path,
    embedding_service: EmbeddingService,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    """Import articles from a single legislative .md file.

    Args:
        filepath: Path to the .md file.
        embedding_service: Service for generating embeddings.
        force: If True, delete existing articles for this act and reimport.
        dry_run: If True, only parse and print, don't write to DB.

    Returns:
        Number of articles imported.
    """
    act_normativ = detect_act_normativ(filepath.name)
    logger.info("importing_legislation", file=filepath.name, act=act_normativ)

    # Read file
    text = filepath.read_text(encoding='utf-8')
    logger.info("file_read", chars=len(text), lines=text.count('\n'))

    # Parse articles
    articles = parse_articles(text, act_normativ)
    logger.info("articles_parsed", count=len(articles), act=act_normativ)

    if not articles:
        logger.warning("no_articles_parsed", file=filepath.name)
        return 0

    if dry_run:
        for art in articles:
            print(f"  {art['articol']:>12s} | {art['capitol'] or '':>40s} | "
                  f"{art['text_integral'][:80]}...")
        print(f"\n  Total: {len(articles)} articles from {act_normativ}")
        return len(articles)

    # Database operations
    async with db_session.async_session() as session:
        if force:
            # Delete existing articles for this act
            await session.execute(
                delete(ArticolLegislatie).where(
                    ArticolLegislatie.act_normativ == act_normativ
                )
            )
            await session.commit()
            logger.info("deleted_existing", act=act_normativ)

        # Check which articles already exist
        existing = await session.execute(
            select(ArticolLegislatie.numar_articol).where(
                ArticolLegislatie.act_normativ == act_normativ
            )
        )
        existing_nums = {row[0] for row in existing}

        # Filter to new articles only
        new_articles = [a for a in articles if a['numar_articol'] not in existing_nums]
        if not new_articles:
            logger.info("all_articles_exist", act=act_normativ, total=len(articles))
            return 0

        logger.info(
            "importing_new_articles",
            new=len(new_articles),
            existing=len(existing_nums),
            total=len(articles),
        )

        # Generate embeddings in batches
        imported = 0
        for batch_start in range(0, len(new_articles), EMBED_BATCH_SIZE):
            batch = new_articles[batch_start:batch_start + EMBED_BATCH_SIZE]

            # Prepare texts for embedding: article label + text
            embed_texts = [
                f"{a['act_normativ']} {a['articol']}: {a['text_integral']}"
                for a in batch
            ]

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

            # Create DB records
            for art_data, emb in zip(batch, embeddings):
                record = ArticolLegislatie(
                    act_normativ=art_data['act_normativ'],
                    numar_articol=art_data['numar_articol'],
                    articol=art_data['articol'],
                    titlu_articol=art_data['titlu_articol'],
                    text_integral=art_data['text_integral'],
                    capitol=art_data['capitol'],
                    sectiune=art_data['sectiune'],
                    embedding=emb,
                )
                session.add(record)
                imported += 1

            # Commit per batch
            await session.commit()
            logger.info(
                "batch_committed",
                batch=batch_start // EMBED_BATCH_SIZE + 1,
                imported_so_far=imported,
            )

            # Rate limiting between batches
            if batch_start + EMBED_BATCH_SIZE < len(new_articles):
                await asyncio.sleep(1.0)

    logger.info("import_complete", act=act_normativ, imported=imported)
    return imported


async def main():
    parser = argparse.ArgumentParser(
        description="Import Romanian procurement legislation into the database"
    )
    parser.add_argument(
        "--dir",
        type=str,
        help="Directory containing .md legislation files",
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Single .md file to import",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing articles and reimport",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse only, print results without writing to DB",
    )
    args = parser.parse_args()

    if not args.dir and not args.file:
        parser.error("Either --dir or --file is required")

    # Collect files
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

    # Initialize DB and embedding service
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
            filepath,
            embedding_service,
            force=args.force,
            dry_run=args.dry_run,
        )
        total_imported += count

    elapsed = time.time() - start
    print(f"\nDone! Imported {total_imported} articles in {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
