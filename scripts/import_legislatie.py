#!/usr/bin/env python3
"""Import Romanian procurement legislation from .md/.txt files into the database.

Reads legislation files from a GCS bucket (default: date-expert-app/legislatie-ap)
and parses them at MAXIMUM GRANULARITY — each row represents the smallest
independent legal unit:
  - Literă (if the alineat has litere)
  - Alineat (if no litere)
  - Articol (if no alineats)

Supports exact citations like:
    "art. 2 alin. (2) lit. a) din Legea nr. 98/2016"

Supports TWO input formats:

  1. Markdown format (old):
      ## Capitolul I - Dispoziții generale
      ### Secțiunea 1 - Obiect, scop și principii
      #### Articolul 1
      (1) Primul alineat...

  2. Plaintext format (new, from legislatie consolidată):
      Capitolul I
      Dispoziții generale
      Secțiunea 1
      Obiect, scop și principii
      Articolul 1
      (1)Primul alineat...
      a)prima literă;
      La data de ... (note de modificare — ignorate automat)

Features:
- Auto-detects format (markdown vs plaintext)
- Strips modification notes ("La data de...") and "Notă" blocks
- Handles superscript articles/alineats (e.g., Articolul 61^1, alin. (2^1))
- Handles Paragraful as sub-section structure
- Multi-character litere (aa, bb, eee) and superscript litere (ee^1)
- --update mode: smart upsert (insert new, update changed, remove obsolete)
- --force mode: delete all + reimport from scratch
- Idempotent default: skips entries already imported

Usage:
    # Import all files from GCS (default bucket/folder)
    DATABASE_URL="..." python scripts/import_legislatie.py --dir legislatie-ap

    # Import a single file from GCS
    DATABASE_URL="..." python scripts/import_legislatie.py --file "LEGE nr. 98 din 19 mai 2016.txt"

    # Smart update (only change modified fragments, remove obsolete)
    DATABASE_URL="..." python scripts/import_legislatie.py --dir legislatie-ap --update

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
from sqlalchemy import select, delete, update as sa_update, text, func
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
    ("NORME", "395"): ("HG", 395, 2016, "HG nr. 395/2016 - Normele metodologice de aplicare a Legii 98/2016"),
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
    m = re.search(r'(LEGE|HG|OUG|NORME)\s*(?:nr\.?\s*)?(\d+)', name)
    if m:
        act_type = m.group(1)
        act_num = int(m.group(2))
        y = re.search(r'(\d{4})', name)
        year = int(y.group(1)) if y else 2016
        tip = "Lege" if act_type == "LEGE" else act_type
        return tip, act_num, year, f"{tip} {act_num}/{year}"

    raise ValueError(f"Cannot detect act normativ from filename: {filename}")


# ---------------------------------------------------------------------------
# Text preprocessing — strip modification notes and Notă blocks
# ---------------------------------------------------------------------------

# Patterns that signal the end of a Notă block
_STRUCTURAL_RE = re.compile(
    r'^(?:#{1,4}\s+)?(?:Capitolul|Sec[tț]iunea|Articolul|Art\.\s*\d|Paragraful)\s',
    re.IGNORECASE,
)
_ALINEAT_START_RE = re.compile(r'^\(\d')


def preprocess_text(text: str) -> str:
    """Remove modification notes and Notă blocks from legislative text.

    Strips:
    - Lines starting with "La data de ..." (modification history)
    - "Notă" blocks (from "Notă" line until next structural element)
    """
    lines = text.split('\n')
    cleaned = []
    in_nota = False

    for line in lines:
        stripped = line.strip()

        # Skip modification history notes
        if re.match(r'^La data de \d', stripped):
            continue

        # Detect Notă block start
        if stripped.startswith('Notă'):
            in_nota = True
            continue

        # Check if we're exiting a Notă block
        if in_nota:
            if _STRUCTURAL_RE.match(stripped) or _ALINEAT_START_RE.match(stripped):
                in_nota = False
                # Fall through to include this line
            else:
                continue

        cleaned.append(line)

    return '\n'.join(cleaned)


# ---------------------------------------------------------------------------
# Superscript number handling (e.g., art. 61^1, alin. (2^1))
# ---------------------------------------------------------------------------

def parse_superscript_number(s: str) -> int:
    """Convert superscript notation to integer.

    '5' → 5, '61^1' → 6101, '2^1' → 201
    Uses *100+sub encoding (safe: no article has 100+ sub-articles).
    """
    if '^' in s:
        parts = s.split('^')
        return int(parts[0]) * 100 + int(parts[1])
    return int(s)


# ---------------------------------------------------------------------------
# Fragment parsing
# ---------------------------------------------------------------------------

def parse_litere(text: str) -> list[dict]:
    """Extract litere from alineat text, including multi-line content.

    Handles both old format ('* a) text') and new format ('a)text').
    Supports multi-character litere (aa, bb, eee) and superscript (ee^1).
    """
    litere = []
    pattern = re.compile(
        r'^\s*(?:\*\s+)?([a-zăâîșțşţ]{1,3}(?:\^\d+)?)\)\s*',
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))

    if not matches:
        return []

    for idx, m in enumerate(matches):
        content_start = m.end()
        content_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)

        lit_text = text[content_start:content_end].strip()
        lit_text = lit_text.rstrip(';.').strip()

        if lit_text:
            litere.append({
                "litera": m.group(1),
                "text": lit_text,
            })

    return litere


def parse_alineats(article_text: str) -> list[dict]:
    """Split article text into alineats with their litere.

    Handles superscript notation like (2^1).
    """
    alin_pattern = re.compile(r'^\((\d+(?:\^\d+)?)\)', re.MULTILINE)
    alin_starts = list(alin_pattern.finditer(article_text))

    if not alin_starts:
        litere = parse_litere(article_text)
        return [{
            "alineat": None,
            "alineat_raw": None,
            "text": article_text.strip(),
            "litere": litere if litere else [],
        }]

    alineats = []
    for idx, match in enumerate(alin_starts):
        alin_raw = match.group(1)
        alin_num = parse_superscript_number(alin_raw)
        start = match.start()
        end = alin_starts[idx + 1].start() if idx + 1 < len(alin_starts) else len(article_text)

        alin_text = article_text[start:end].strip()
        litere = parse_litere(alin_text)

        alineats.append({
            "alineat": alin_num,
            "alineat_raw": alin_raw,
            "text": alin_text,
            "litere": litere if litere else [],
        })

    # Text before first alineat (introductory text)
    if alin_starts[0].start() > 0:
        intro = article_text[:alin_starts[0].start()].strip()
        if intro and len(intro) > 10:
            alineats.insert(0, {
                "alineat": None,
                "alineat_raw": None,
                "text": intro,
                "litere": [],
            })

    return alineats


def parse_legislation(raw_text: str) -> list[dict]:
    """Parse a legislative .md/.txt file into fragment-level records.

    Auto-detects format (markdown with # headers vs plaintext).
    Each record = smallest independent legal unit (literă > alineat > articol).

    Returns:
        List of dicts with keys: numar_articol, articol, alineat, alineat_text,
        litera, text_fragment, articol_complet, citare, capitol, sectiune.
    """
    # Preprocess: remove modification notes and Notă blocks
    clean_text = preprocess_text(raw_text)
    lines = clean_text.split('\n')
    records = []

    current_capitol = None
    current_sectiune = None
    current_art_num = None
    current_art_raw = None  # raw string like "61^1"
    current_art_lines: list[str] = []
    pending_title = None  # 'capitol', 'sectiune', 'paragraf'
    pending_paragraf_num = None

    # Regex patterns — handle both markdown (with #) and plaintext formats
    capitol_re = re.compile(
        r'^(?:#{1,4}\s+)?(?:Capitolul|CAPITOLUL|CAP\.)\s+(.+)', re.IGNORECASE
    )
    sectiune_re = re.compile(
        r'^(?:#{1,4}\s+)?(?:Sec[tțţ]iunea|SECȚIUNEA|SEC[ȚTŢ]IUNEA)\s+(.+)',
        re.IGNORECASE,
    )
    articol_re = re.compile(
        r'^(?:#{1,4}\s+)?(?:Articolul|Art\.?)\s+(\d+(?:\^\d+)?)', re.IGNORECASE
    )
    paragraf_re = re.compile(
        r'^(?:#{1,4}\s+)?Paragraful\s+(\d+)', re.IGNORECASE
    )

    def flush_article():
        """Process accumulated article lines into fragment records."""
        nonlocal current_art_lines, current_art_num, current_art_raw
        if current_art_num is None or not current_art_lines:
            return

        article_text = '\n'.join(current_art_lines).strip()
        if not article_text:
            return

        art_label = f"art. {current_art_raw}"
        alineats = parse_alineats(article_text)

        for alin_data in alineats:
            alin_num = alin_data["alineat"]
            alin_raw = alin_data["alineat_raw"]
            alineat_text = f"alin. ({alin_raw})" if alin_raw is not None else None
            litere = alin_data["litere"]

            if litere:
                # Each litera becomes its own row
                for lit in litere:
                    if alin_raw is not None:
                        citare = f"art. {current_art_raw} alin. ({alin_raw}) lit. {lit['litera']})"
                    else:
                        citare = f"art. {current_art_raw} lit. {lit['litera']})"

                    records.append({
                        "numar_articol": current_art_num,
                        "articol": art_label,
                        "alineat": alin_num,
                        "alineat_text": alineat_text,
                        "litera": lit["litera"],
                        "text_fragment": lit["text"],
                        "articol_complet": article_text,
                        "citare": citare,
                        "capitol": current_capitol[:500] if current_capitol else None,
                        "sectiune": current_sectiune[:500] if current_sectiune else None,
                    })
            else:
                # No litere — fragment is the alineat itself (or whole article)
                if alin_raw is not None:
                    citare = f"art. {current_art_raw} alin. ({alin_raw})"
                else:
                    citare = f"art. {current_art_raw}"

                records.append({
                    "numar_articol": current_art_num,
                    "articol": art_label,
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

        # Handle pending title (new format: title on separate line)
        if pending_title and stripped:
            # Check if this line is actually a structural element (not a title)
            is_structural = (
                capitol_re.match(stripped)
                or sectiune_re.match(stripped)
                or articol_re.match(stripped)
                or paragraf_re.match(stripped)
            )
            if not is_structural and not stripped.startswith('#'):
                if pending_title == 'capitol':
                    current_capitol = f"{current_capitol} - {stripped}"
                elif pending_title == 'sectiune':
                    current_sectiune = f"{current_sectiune} - {stripped}"
                elif pending_title == 'paragraf':
                    if current_sectiune:
                        current_sectiune = (
                            f"{current_sectiune} / "
                            f"Paragraful {pending_paragraf_num} - {stripped}"
                        )
                    else:
                        current_sectiune = (
                            f"Paragraful {pending_paragraf_num} - {stripped}"
                        )
                pending_title = None
                continue
            else:
                # Title line is actually a structural element — previous
                # header had no separate title (e.g., abrogated chapter)
                pending_title = None
                # Fall through to process this line as structural

        # Skip empty lines when waiting for title
        if pending_title and not stripped:
            continue

        # Capitolul
        m = capitol_re.match(stripped)
        if m:
            flush_article()
            content = m.group(1).strip()
            if ' - ' in content or ' – ' in content:
                # Old format: "I - Dispoziții generale" on same line
                current_capitol = content
            else:
                # New format: just "I" or "II" — title on next line
                current_capitol = content
                pending_title = 'capitol'
            current_sectiune = None
            continue

        # Secțiunea
        m = sectiune_re.match(stripped)
        if m:
            flush_article()
            content = m.group(1).strip()
            if ' - ' in content or ' – ' in content:
                current_sectiune = content
            else:
                current_sectiune = content
                pending_title = 'sectiune'
            continue

        # Paragraful (sub-section, appended to current_sectiune)
        m = paragraf_re.match(stripped)
        if m:
            flush_article()
            pending_paragraf_num = m.group(1)
            pending_title = 'paragraf'
            continue

        # Articolul
        m = articol_re.match(stripped)
        if m:
            flush_article()
            current_art_raw = m.group(1)
            current_art_num = parse_superscript_number(current_art_raw)
            current_art_lines = []
            continue

        # Skip markdown headers not caught above
        if stripped.startswith('#'):
            continue

        # Collect article text
        if current_art_num is not None:
            current_art_lines.append(line)

    flush_article()

    return records


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

async def get_or_create_act(
    session,
    tip_act: str,
    numar: int,
    an: int,
    titlu: str,
) -> str:
    """Get existing act_id or create new ActNormativ record."""
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


def _build_embed_text(act_label: str, rec: dict) -> str:
    """Build the text used for embedding a fragment."""
    parts = [f"{act_label} {rec['citare']}"]
    if rec["capitol"]:
        parts.append(f"Capitol: {rec['capitol']}")
    if rec["sectiune"]:
        parts.append(f"Secțiune: {rec['sectiune']}")
    parts.append(rec["text_fragment"])
    return "\n".join(parts)


async def _generate_embeddings_with_retry(
    embedding_service: EmbeddingService,
    texts: list[str],
) -> list:
    """Generate embeddings with 3x retry and exponential backoff."""
    for attempt in range(3):
        try:
            return await embedding_service.embed_batch(texts)
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
    return []


# ---------------------------------------------------------------------------
# Import / Update logic
# ---------------------------------------------------------------------------

async def import_file(
    filename: str,
    file_text: str,
    embedding_service: EmbeddingService,
    force: bool = False,
    update: bool = False,
    dry_run: bool = False,
) -> dict:
    """Import or update fragment-level records from a legislative file.

    Args:
        filename: Name of the file.
        file_text: Content of the file.
        embedding_service: Service for generating embeddings.
        force: Delete existing records and reimport.
        update: Smart upsert — insert new, update changed, remove obsolete.
        dry_run: Only parse and print, don't write to DB.

    Returns:
        Dict with counts: inserted, updated, removed, unchanged.
    """
    tip_act, numar, an, titlu = detect_act_info(filename)
    act_label = f"{tip_act} {numar}/{an}"
    logger.info("importing_legislation", file=filename, act=act_label)
    logger.info("file_read", chars=len(file_text), lines=file_text.count('\n'))

    records = parse_legislation(file_text)
    logger.info("records_parsed", count=len(records), act=act_label)

    if not records:
        logger.warning("no_records_parsed", file=filename)
        return {"inserted": 0, "updated": 0, "removed": 0, "unchanged": 0}

    if dry_run:
        for rec in records:
            print(
                f"  {rec['citare']:>50s} | "
                f"{rec['capitol'] or '':>40s} | "
                f"{rec['text_fragment'][:60]}..."
            )
        print(f"\n  Total: {len(records)} fragment-level records from {act_label}")
        return {"inserted": len(records), "updated": 0, "removed": 0, "unchanged": 0}

    # Deduplicate parsed records (parser may generate dupes for same key)
    seen_keys = set()
    deduped_records = []
    for r in records:
        key = (r["numar_articol"], r["alineat"] or 0, r["litera"] or "")
        if key not in seen_keys:
            seen_keys.add(key)
            deduped_records.append(r)
    if len(deduped_records) < len(records):
        logger.warning(
            "duplicates_in_parsed_records",
            count=len(records) - len(deduped_records),
            act=act_label,
        )
    records = deduped_records

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

        # --update mode: smart upsert
        if update:
            return await _update_existing(
                session, act_id, act_label, records, embedding_service,
            )

        # Default mode: insert only new records
        return await _insert_new_only(
            session, act_id, act_label, records, embedding_service,
        )


async def _update_existing(
    session,
    act_id: str,
    act_label: str,
    records: list[dict],
    embedding_service: EmbeddingService,
) -> dict:
    """Smart upsert: insert new, update changed, remove obsolete fragments."""
    # Load existing records (only columns needed for comparison)
    result = await session.execute(
        select(
            LegislatieFragment.id,
            LegislatieFragment.numar_articol,
            LegislatieFragment.alineat,
            LegislatieFragment.litera,
            LegislatieFragment.text_fragment,
        ).where(LegislatieFragment.act_id == act_id)
    )
    existing_map = {}
    for row in result:
        key = (row[1], row[2] or 0, row[3] or "")
        existing_map[key] = {"id": row[0], "text_fragment": row[4]}

    # Build new records map
    new_map = {}
    for r in records:
        key = (r["numar_articol"], r["alineat"] or 0, r["litera"] or "")
        new_map[key] = r

    # Categorize
    to_insert = []
    to_update = []
    unchanged = 0

    for key, rec in new_map.items():
        if key in existing_map:
            existing = existing_map[key]
            if existing["text_fragment"] != rec["text_fragment"]:
                to_update.append((existing["id"], rec))
            else:
                unchanged += 1
        else:
            to_insert.append(rec)

    removed_keys = set(existing_map.keys()) - set(new_map.keys())

    logger.info(
        "update_summary",
        act=act_label,
        insert=len(to_insert),
        update=len(to_update),
        unchanged=unchanged,
        removed=len(removed_keys),
    )
    print(
        f"  {act_label}: {len(to_insert)} new, {len(to_update)} changed, "
        f"{unchanged} unchanged, {len(removed_keys)} removed"
    )

    # Process UPDATES — re-embed changed fragments
    if to_update:
        for batch_start in range(0, len(to_update), EMBED_BATCH_SIZE):
            batch = to_update[batch_start:batch_start + EMBED_BATCH_SIZE]
            embed_texts = [_build_embed_text(act_label, rec) for _, rec in batch]

            embeddings = await _generate_embeddings_with_retry(
                embedding_service, embed_texts,
            )
            if not embeddings:
                logger.error("embedding_failed_for_updates", batch_start=batch_start)
                continue

            for (frag_id, rec_data), emb in zip(batch, embeddings):
                await session.execute(
                    sa_update(LegislatieFragment)
                    .where(LegislatieFragment.id == frag_id)
                    .values(
                        text_fragment=rec_data["text_fragment"],
                        articol_complet=rec_data["articol_complet"],
                        citare=rec_data["citare"],
                        capitol=rec_data["capitol"],
                        sectiune=rec_data["sectiune"],
                        articol=rec_data["articol"],
                        alineat_text=rec_data["alineat_text"],
                        embedding=emb,
                        keywords=None,
                    )
                )

            await session.commit()
            logger.info("updates_committed", count=len(batch))

            if batch_start + EMBED_BATCH_SIZE < len(to_update):
                await asyncio.sleep(1.0)

    # Process INSERTS — new fragments
    if to_insert:
        await _embed_and_insert(
            session, act_id, act_label, to_insert, embedding_service,
        )

    # Process REMOVALS — delete obsolete fragments
    if removed_keys:
        removed_ids = [existing_map[key]["id"] for key in removed_keys]
        await session.execute(
            delete(LegislatieFragment).where(
                LegislatieFragment.id.in_(removed_ids)
            )
        )
        await session.commit()
        logger.info("removed_obsolete", count=len(removed_ids), act=act_label)

    # Regenerate tsvector for updated/inserted records
    await session.execute(
        text("""
            UPDATE legislatie_fragmente
            SET keywords = to_tsvector('romanian', text_fragment || ' ' || citare)
            WHERE act_id = :act_id AND keywords IS NULL
        """),
        {"act_id": act_id},
    )
    await session.commit()

    return {
        "inserted": len(to_insert),
        "updated": len(to_update),
        "removed": len(removed_keys),
        "unchanged": unchanged,
    }


async def _insert_new_only(
    session,
    act_id: str,
    act_label: str,
    records: list[dict],
    embedding_service: EmbeddingService,
) -> dict:
    """Insert only records that don't already exist in DB."""
    # Check which records already exist
    existing = await session.execute(
        select(
            LegislatieFragment.numar_articol,
            LegislatieFragment.alineat,
            LegislatieFragment.litera,
        ).where(LegislatieFragment.act_id == act_id)
    )
    existing_keys = {
        (row[0], row[1] or 0, row[2] or "")
        for row in existing
    }

    new_records = [
        r for r in records
        if (r["numar_articol"], r["alineat"] or 0, r["litera"] or "") not in existing_keys
    ]

    if not new_records:
        logger.info("all_records_exist", act=act_label, total=len(records))
        return {"inserted": 0, "updated": 0, "removed": 0, "unchanged": len(records)}

    logger.info(
        "importing_new_records",
        new=len(new_records),
        existing=len(existing_keys),
        total=len(records),
    )

    inserted = await _embed_and_insert(
        session, act_id, act_label, new_records, embedding_service,
    )

    return {
        "inserted": inserted,
        "updated": 0,
        "removed": 0,
        "unchanged": len(existing_keys),
    }


async def _embed_and_insert(
    session,
    act_id: str,
    act_label: str,
    records: list[dict],
    embedding_service: EmbeddingService,
) -> int:
    """Generate embeddings and insert fragment records in batches."""
    imported = 0

    for batch_start in range(0, len(records), EMBED_BATCH_SIZE):
        batch = records[batch_start:batch_start + EMBED_BATCH_SIZE]
        embed_texts = [_build_embed_text(act_label, r) for r in batch]

        embeddings = await _generate_embeddings_with_retry(
            embedding_service, embed_texts,
        )
        if not embeddings:
            logger.error("embedding_failed", batch_start=batch_start)
            continue

        for rec_data, emb in zip(batch, embeddings):
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

        # Update tsvector keywords for this batch
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

        if batch_start + EMBED_BATCH_SIZE < len(records):
            await asyncio.sleep(1.0)

    logger.info("insert_complete", act=act_label, imported=imported)
    return imported


# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------

def connect_to_gcs(
    bucket_name: str,
    project_id: Optional[str] = None,
) -> storage.Bucket:
    """Connect to GCS bucket."""
    logger.info("gcs_connecting", bucket=bucket_name, project=project_id)
    if project_id:
        client = storage.Client(project=project_id)
    else:
        client = storage.Client()
    bucket = client.bucket(bucket_name)
    logger.info("gcs_connected", bucket=bucket_name)
    return bucket


def list_legislation_files(bucket: storage.Bucket, folder: str) -> list[str]:
    """List all .md and .txt legislation files in a GCS folder."""
    prefix = f"{folder}/" if folder else ""
    blobs = bucket.list_blobs(prefix=prefix, timeout=300)
    files = [
        blob.name for blob in blobs
        if blob.name.endswith('.md') or blob.name.endswith('.txt')
    ]
    logger.info("gcs_files_listed", count=len(files), prefix=prefix)
    return sorted(files)


def download_file(bucket: storage.Bucket, blob_name: str) -> str:
    """Download a file from GCS and return its content."""
    blob = bucket.blob(blob_name)
    try:
        content = blob.download_as_text(encoding='utf-8', timeout=120)
    except UnicodeDecodeError:
        content = blob.download_as_text(encoding='latin-1', timeout=120)
    except (TypeError, Exception) as e:
        logger.warning(
            "download_as_text_failed_trying_bytes",
            blob=blob_name,
            error=str(e),
        )
        raw = blob.download_as_bytes(timeout=300)
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            content = raw.decode('latin-1')
    return content


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
        help="Folder in GCS bucket containing legislation files (default: legislatie-ap)",
    )
    parser.add_argument(
        "--file", type=str,
        help="Single filename to import from GCS folder",
    )
    parser.add_argument(
        "--project",
        default="gen-lang-client-0706147575",
        help="GCP project ID",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--force", action="store_true",
        help="Delete existing records and reimport from scratch",
    )
    mode_group.add_argument(
        "--update", action="store_true",
        help="Smart upsert: insert new, update changed, remove obsolete fragments",
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
        blob_name = f"{args.dir}/{args.file}" if args.dir else args.file
        blob_names = [blob_name]
    else:
        blob_names = list_legislation_files(bucket, args.dir)

    if not blob_names:
        print("No legislation files (.md/.txt) found in GCS")
        sys.exit(1)

    # Filter to only legislation files (skip README, etc.)
    legislation_blobs = []
    for b in blob_names:
        try:
            detect_act_info(Path(b).name)
            legislation_blobs.append(b)
        except ValueError:
            logger.info("skipping_non_legislation_file", file=Path(b).name)
    blob_names = legislation_blobs

    if not blob_names:
        print("No recognized legislation files found")
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

    totals = {"inserted": 0, "updated": 0, "removed": 0, "unchanged": 0}
    start = time.time()

    for blob_name in blob_names:
        filename = Path(blob_name).name
        print(f"Downloading {filename} from GCS...")
        try:
            file_text = download_file(bucket, blob_name)
        except Exception as e:
            print(f"Warning: Failed to download {blob_name}: {e}, skipping")
            continue

        result = await import_file(
            filename, file_text, embedding_service,
            force=args.force, update=args.update, dry_run=args.dry_run,
        )
        for k in totals:
            totals[k] += result[k]

    elapsed = time.time() - start
    print(
        f"\nDone in {elapsed:.1f}s! "
        f"Inserted: {totals['inserted']}, "
        f"Updated: {totals['updated']}, "
        f"Removed: {totals['removed']}, "
        f"Unchanged: {totals['unchanged']}"
    )


if __name__ == "__main__":
    asyncio.run(main())
