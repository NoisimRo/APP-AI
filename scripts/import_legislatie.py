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
    m = re.search(r'(LEGE|HG|OUG|ORDIN|NORME)\s*(?:NR\.?\s*)?(\d+)', name)
    if m:
        act_type = m.group(1)
        act_num = int(m.group(2))
        y = re.search(r'(\d{4})', name)
        year = int(y.group(1)) if y else 2016
        tip_map = {"LEGE": "Lege", "ORDIN": "Ordin"}
        tip = tip_map.get(act_type, act_type)
        return tip, act_num, year, f"{tip} nr. {act_num}/{year}"

    # ANAP instructions/guidance documents
    m = re.search(r'INSTRUC[TȚ]IUN[EĂ]\s*(?:NR\.?\s*)?(\d+)', name, re.IGNORECASE)
    if not m:
        m = re.search(r'INSTRUC[TȚ]IUN[EĂ][_\s]+(\d+)', name, re.IGNORECASE)
    if m:
        act_num = int(m.group(1))
        y = re.search(r'(\d{4})', name)
        year = int(y.group(1)) if y else 2020
        return "Instrucțiune ANAP", act_num, year, f"Instrucțiune ANAP nr. {act_num}/{year}"

    # Anexa to guidance documents (must check BEFORE Îndrumare, since filename contains both)
    if 'ANEXA' in name and ('INDRUMARE' in name or 'ÎNDRUMARE' in name):
        y = re.search(r'(\d{4})', name)
        year = int(y.group(1)) if y else 2020
        return "Anexă Îndrumare ANAP", year, year, f"Anexă Îndrumare ANAP/{year}"

    # ANAP guidance documents (Îndrumare) — may not have a number
    if 'INDRUMARE' in name or 'ÎNDRUMARE' in name or 'INDRUMARE' in name.replace('Î', 'I'):
        y = re.search(r'(\d{4})', name)
        year = int(y.group(1)) if y else 2020
        # Use year as act_num since Îndrumare typically don't have numbers
        return "Îndrumare ANAP", year, year, f"Îndrumare ANAP/{year}"

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


def _parse_annex_sections(annex_text: str, annex_num: int, annex_label: str) -> list[dict]:
    """Parse an annex into logical sections for import as fragments.

    Splits annex content by:
    - Sub-annexes (e.g., "Anexa nr. 3A", "Anexa nr. 3B")
    - Lettered sections (e.g., a)Fructe, b)Cereale)
    - Numbered points (e.g., 1. Caracteristici, 2. Principii)
    - Table-like groups (consecutive data lines grouped with their headers)

    Returns fragment dicts compatible with the regular article fragments.
    """
    lines = annex_text.split('\n')
    sections: list[tuple[str, list[str]]] = []
    current_heading = annex_label
    current_lines: list[str] = []

    # Patterns for section boundaries within annexes
    sub_annex_re = re.compile(
        r'^Anexa\s+nr\.\s*(\d+[A-Z]?)\.\s*(.*)', re.IGNORECASE,
    )
    lettered_section_re = re.compile(
        r'^([a-z](?:\.\d+)?)\)(.*)', re.IGNORECASE,
    )
    numbered_point_re = re.compile(
        r'^(\d+(?:\^\d+)?)\.\s+(.*)',
    )
    # Table category headers that repeat in the food tables
    table_category_re = re.compile(
        r'^(Nr\.\s*crt\.|Grupa alimentar[ăa]|Grupa de v[âa]rst[ăa]|V[âa]rst[ăa])',
        re.IGNORECASE,
    )

    def flush_section():
        nonlocal current_lines
        text = '\n'.join(current_lines).strip()
        if text and len(text) > 5:
            sections.append((current_heading, current_lines[:]))
        current_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Sub-annex header (e.g., "Anexa nr. 3A. Necesarul zilnic...")
        m = sub_annex_re.match(stripped)
        if m:
            flush_section()
            sub_id = m.group(1).strip()
            sub_title = m.group(2).strip()
            current_heading = f"Anexa nr. {sub_id}" + (f" - {sub_title}" if sub_title else "")
            continue

        # Numbered point (e.g., "1. Caracteristici în alimentaţia copilului")
        # Check BEFORE lettered sections — numbered points take priority
        m = numbered_point_re.match(stripped)
        if m:
            point_num = m.group(1)
            point_text = m.group(2).strip()
            # Only split if the text after the number looks like a title (>10 chars, not pure data)
            if point_text and len(point_text) > 10 and not re.match(r'^[\d,.\-\s]+$', point_text):
                flush_section()
                current_heading = f"pct. {point_num} - {point_text}"
                continue

        # Lettered section (e.g., "a)Fructe și legume")
        # Only treat as section boundary if the text is heading-like:
        # short (≤60 chars) and doesn't end with a period (not a full sentence)
        m = lettered_section_re.match(stripped)
        if m:
            letter = m.group(1)
            rest = m.group(2).strip()
            is_heading = (
                rest
                and len(rest) > 3
                and len(rest) <= 60
                and not rest.endswith('.')
                and not rest.endswith(';')
            )
            if is_heading:
                flush_section()
                current_heading = f"lit. {letter}) {rest}"
                continue

        # Table category header restart (signals a new logical table)
        if table_category_re.match(stripped):
            flush_section()
            current_heading = current_heading.split(' / ')[0]  # keep base heading

        current_lines.append(stripped)

    flush_section()

    # Convert sections to fragment records
    records = []
    base_num = annex_num * 10000  # e.g., Annexa 1 → 10000, Annexa 3 → 30000
    annex_full_text = annex_text.strip()

    for idx, (heading, section_lines) in enumerate(sections, 1):
        section_text = '\n'.join(section_lines).strip()
        if not section_text:
            continue

        fragment_num = base_num + idx
        art_label = f"Anexa nr. {annex_num}"

        # Build citare
        if heading == annex_label:
            citare = f"Anexa nr. {annex_num}"
        else:
            citare = f"Anexa nr. {annex_num} - {heading}"

        records.append({
            "numar_articol": fragment_num,
            "articol": art_label,
            "alineat": idx,
            "alineat_text": heading,
            "litera": None,
            "text_fragment": section_text,
            "articol_complet": annex_full_text[:10000],  # cap to prevent huge texts
            "citare": citare[:150],
            "capitol": f"Anexa nr. {annex_num}",
            "sectiune": heading[:500] if heading != annex_label else None,
        })

    return records


def _detect_document_type(raw_text: str) -> str:
    """Detect the type of document for choosing the right parser.

    Returns:
        'standard' — regular law/HG with Capitolul/Articolul structure
        'numbered_sections' — ANAP guidance with numbered sections (1., 2., 3.)
        'modification_act' — Instrucțiune that modifies another act (Art. I/II + numbered points)
        'table_only' — document with no structural markers (pure table/text)
    """
    lines = raw_text.split('\n')
    clean_lines = [l.strip() for l in lines if l.strip()]

    # Check for standard structural articles (Articolul X or Art. X where X is a number)
    # Must be a standalone article header, NOT an inline reference like
    # "Art. 115 alin. (1) din Legea nr. 98/2016"
    standard_art_re = re.compile(
        r'^(?:#{1,4}\s+)?(?:Articolul|Art\.?)\s+(\d+(?:\^\d+)?)\s*$', re.IGNORECASE
    )
    # Also match "Art. 5 se modifică" or "Articolul 6se modifică" (article headers with trailing text)
    # but NOT "Art. 115 alin. (1) din Legea" (inline legal references)
    standard_art_with_text_re = re.compile(
        r'^(?:#{1,4}\s+)?(?:Articolul|Art\.?)\s+(\d+(?:\^\d+)?)\s*[-–(]', re.IGNORECASE
    )
    inline_ref_re = re.compile(
        r'Art\.\s+\d+\s+(?:alin|din|și|,|lit\.)', re.IGNORECASE
    )
    structural_article_count = 0
    for l in clean_lines:
        if standard_art_re.match(l) or standard_art_with_text_re.match(l):
            # Make sure it's not an inline reference
            if not inline_ref_re.match(l):
                structural_article_count += 1
    has_standard_articles = structural_article_count >= 2

    # Check for Roman numeral articles (Art. I., Art. II.) — modification acts
    roman_art_re = re.compile(
        r'^Art\.\s+([IVX]+)[\.\s\-]', re.IGNORECASE
    )
    has_roman_articles = any(roman_art_re.match(l) for l in clean_lines)

    # Check for numbered sections at start of line (e.g., "   1. Modificarea duratei")
    numbered_section_re = re.compile(r'^\s*(\d+)\.\s+[A-ZĂÂÎȘȚ]')
    numbered_sections = [l for l in clean_lines if numbered_section_re.match(l)]

    if has_standard_articles:
        return 'standard'
    elif has_roman_articles:
        return 'modification_act'
    elif len(numbered_sections) >= 2:
        return 'numbered_sections'
    else:
        return 'table_only'


def _parse_numbered_sections_document(raw_text: str) -> list[dict]:
    """Parse a document structured with numbered sections (1., 2., 3.).

    Used for ANAP guidance documents (Îndrumare) that don't have standard
    Articolul/Capitolul structure but use numbered sections.

    Also handles sub-sections with letters: a), b), c).
    """
    clean_text = preprocess_text(raw_text)
    lines = clean_text.split('\n')
    records = []

    # Find numbered sections: "   1. Title text" or "1. Title text"
    section_re = re.compile(r'^\s*(\d+)\.\s+(.*)')

    sections: list[tuple[int, str, list[str]]] = []
    current_num = None
    current_title = None
    current_lines: list[str] = []

    # Collect preamble (text before first numbered section)
    preamble_lines: list[str] = []
    found_first_section = False

    for line in lines:
        stripped = line.strip()

        m = section_re.match(stripped)
        if m:
            num = int(m.group(1))
            title = m.group(2).strip()

            # Only treat as section if the number follows sequence
            # (avoid matching "10 % din valoare" or similar)
            is_likely_section = (
                len(title) > 5
                and title[0].isupper()
                and (current_num is None or num == current_num + 1 or num == 1)
            )

            if is_likely_section:
                if not found_first_section:
                    found_first_section = True
                if current_num is not None:
                    sections.append((current_num, current_title, current_lines))
                current_num = num
                current_title = title
                current_lines = []
                continue

        if found_first_section and current_num is not None:
            current_lines.append(line)
        elif not found_first_section:
            preamble_lines.append(line)

    # Flush last section
    if current_num is not None:
        sections.append((current_num, current_title, current_lines))

    # Build preamble fragment
    preamble_text = '\n'.join(preamble_lines).strip()
    full_doc_text = clean_text.strip()

    if preamble_text and len(preamble_text) > 20:
        records.append({
            "numar_articol": 0,
            "articol": "Preambul",
            "alineat": None,
            "alineat_text": None,
            "litera": None,
            "text_fragment": preamble_text,
            "articol_complet": preamble_text,
            "citare": "Preambul",
            "capitol": None,
            "sectiune": None,
        })

    # Build section fragments
    for sec_num, sec_title, sec_lines in sections:
        section_text = '\n'.join(sec_lines).strip()
        full_section = f"{sec_num}. {sec_title}\n{section_text}".strip()

        # Try to split into sub-sections by letter (a), b), c))
        sub_sections = _split_section_by_letters(full_section)

        if sub_sections and len(sub_sections) > 1:
            for lit, lit_text in sub_sections:
                citare = f"pct. {sec_num} lit. {lit})"
                records.append({
                    "numar_articol": sec_num,
                    "articol": f"pct. {sec_num}",
                    "alineat": None,
                    "alineat_text": f"pct. {sec_num} - {sec_title[:80]}",
                    "litera": lit,
                    "text_fragment": lit_text,
                    "articol_complet": full_section,
                    "citare": citare,
                    "capitol": None,
                    "sectiune": f"pct. {sec_num} - {sec_title[:80]}",
                })
        else:
            # Single fragment for the whole section
            records.append({
                "numar_articol": sec_num,
                "articol": f"pct. {sec_num}",
                "alineat": None,
                "alineat_text": None,
                "litera": None,
                "text_fragment": full_section,
                "articol_complet": full_section,
                "citare": f"pct. {sec_num}",
                "capitol": None,
                "sectiune": f"pct. {sec_num} - {sec_title[:80]}",
            })

    return records


def _split_section_by_letters(text: str) -> list[tuple[str, str]]:
    """Split a section into lettered sub-items a), b), c) etc.

    Only splits if the letters appear at line starts and are heading-like.
    Returns list of (letter, text) tuples, or empty list if no split.
    """
    pattern = re.compile(r'^\s*([a-z])\)\s+', re.MULTILINE)
    matches = list(pattern.finditer(text))

    if len(matches) < 2:
        return []

    result = []
    for idx, m in enumerate(matches):
        letter = m.group(1)
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            result.append((letter, chunk))

    return result


def _parse_modification_act(raw_text: str) -> list[dict]:
    """Parse a modification act (e.g., Instrucțiune that modifies another Instrucțiune).

    These documents use Roman numeral articles (Art. I., Art. II.) as top-level structure,
    with numbered modification points (1., 2., ..., 8.) inside.
    """
    clean_text = preprocess_text(raw_text)
    lines = clean_text.split('\n')
    records = []

    roman_art_re = re.compile(r'^Art\.\s+([IVX]+)[\.\s\-]+(.*)', re.IGNORECASE)
    numbered_point_re = re.compile(r'^(\d+)\.\s+(.*)')

    current_roman = None
    current_roman_text = None
    current_point_num = None
    current_point_lines: list[str] = []
    preamble_lines: list[str] = []
    found_first = False
    art_counter = 0

    def flush_point():
        nonlocal current_point_lines, current_point_num
        if current_point_num is None or not current_point_lines:
            return
        point_text = '\n'.join(current_point_lines).strip()
        if not point_text:
            return
        art_counter_local = len(records) + 1
        citare = f"Art. {current_roman} pct. {current_point_num}"
        records.append({
            "numar_articol": art_counter_local,
            "articol": f"Art. {current_roman}",
            "alineat": current_point_num,
            "alineat_text": f"pct. {current_point_num}",
            "litera": None,
            "text_fragment": point_text,
            "articol_complet": point_text,
            "citare": citare,
            "capitol": f"Art. {current_roman}",
            "sectiune": f"Art. {current_roman}" + (f" - {current_roman_text[:80]}" if current_roman_text else ""),
        })
        current_point_lines = []
        current_point_num = None

    for line in lines:
        stripped = line.strip()

        m = roman_art_re.match(stripped)
        if m:
            flush_point()
            found_first = True
            current_roman = m.group(1)
            current_roman_text = m.group(2).strip().rstrip('.-').strip()
            current_point_num = None
            current_point_lines = []
            # If there's substantial text after "Art. I. - ...", start collecting
            if current_roman_text and len(current_roman_text) > 20:
                current_point_num = 0
                current_point_lines = [current_roman_text]
            continue

        if not found_first:
            preamble_lines.append(line)
            continue

        m = numbered_point_re.match(stripped)
        if m and current_roman:
            flush_point()
            current_point_num = int(m.group(1))
            rest = m.group(2).strip()
            current_point_lines = [f"{current_point_num}. {rest}"] if rest else []
            continue

        if current_roman:
            current_point_lines.append(line)

    flush_point()

    # If the preamble has the "having in view" section, add it
    preamble_text = '\n'.join(preamble_lines).strip()
    if preamble_text and len(preamble_text) > 50:
        records.insert(0, {
            "numar_articol": 0,
            "articol": "Preambul",
            "alineat": None,
            "alineat_text": None,
            "litera": None,
            "text_fragment": preamble_text,
            "articol_complet": preamble_text,
            "citare": "Preambul",
            "capitol": None,
            "sectiune": None,
        })

    return records


def _parse_table_document(raw_text: str) -> list[dict]:
    """Parse a document that is essentially a table/unstructured text.

    Imports the entire content as a single fragment, or splits by major
    visual separators if present (e.g., form-feed characters).
    """
    clean_text = preprocess_text(raw_text)

    # Split by form-feed (page breaks) if present
    pages = [p.strip() for p in clean_text.split('\f') if p.strip()]

    if len(pages) <= 1:
        # Single fragment for whole document
        return [{
            "numar_articol": 1,
            "articol": "Conținut",
            "alineat": None,
            "alineat_text": None,
            "litera": None,
            "text_fragment": clean_text.strip(),
            "articol_complet": clean_text.strip(),
            "citare": "Conținut",
            "capitol": None,
            "sectiune": None,
        }]

    # Multiple pages — each becomes a fragment
    records = []
    for idx, page in enumerate(pages, 1):
        if len(page) < 10:
            continue
        # Try to extract a heading from the first line
        first_line = page.split('\n')[0].strip()[:80]
        records.append({
            "numar_articol": idx,
            "articol": f"Secțiunea {idx}",
            "alineat": None,
            "alineat_text": None,
            "litera": None,
            "text_fragment": page,
            "articol_complet": page,
            "citare": f"Secțiunea {idx}",
            "capitol": None,
            "sectiune": first_line if first_line else None,
        })

    return records


def parse_legislation(raw_text: str) -> list[dict]:
    """Parse a legislative .md/.txt file into fragment-level records.

    Auto-detects format (markdown with # headers vs plaintext).
    Each record = smallest independent legal unit (literă > alineat > articol).

    Supports:
    - Standard law structure: Capitolul → Secțiunea → Articolul → Alineat → Literă
    - Annex structure: Anexa nr. X → sub-sections, tables, numbered points
    - Non-standard ANAP documents: numbered sections, modification acts, tables

    Returns:
        List of dicts with keys: numar_articol, articol, alineat, alineat_text,
        litera, text_fragment, articol_complet, citare, capitol, sectiune.
    """
    doc_type = _detect_document_type(raw_text)

    # Route to specialized parser for non-standard documents
    if doc_type == 'numbered_sections':
        return _parse_numbered_sections_document(raw_text)
    elif doc_type == 'modification_act':
        return _parse_modification_act(raw_text)
    elif doc_type == 'table_only':
        return _parse_table_document(raw_text)
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

    # Annex accumulation state
    current_annex_num = None  # int: which annex we're in
    current_annex_label = None  # str: "Anexa nr. 1 - LISTA alimentelor..."
    current_annex_lines: list[str] = []

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
    anexa_re = re.compile(
        r'^(?:#{1,4}\s+)?Anexa\s+nr\.?\s*(\d+)\s*$', re.IGNORECASE,
    )

    def flush_annex():
        """Process accumulated annex lines into fragment records."""
        nonlocal current_annex_lines, current_annex_num, current_annex_label
        if current_annex_num is None or not current_annex_lines:
            return
        annex_text = '\n'.join(current_annex_lines).strip()
        if annex_text:
            annex_records = _parse_annex_sections(
                annex_text, current_annex_num, current_annex_label or f"Anexa nr. {current_annex_num}",
            )
            records.extend(annex_records)
        current_annex_lines = []
        current_annex_num = None
        current_annex_label = None

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
                    "capitol": current_capitol[:500] if current_capitol else None,
                    "sectiune": current_sectiune[:500] if current_sectiune else None,
                })

        current_art_lines = []

    for line in lines:
        stripped = line.strip()

        # --- Annex detection (before other structural checks) ---
        m = anexa_re.match(stripped)
        if m:
            # Flush any pending article/annex
            flush_article()
            flush_annex()
            current_art_num = None
            current_art_raw = None
            current_annex_num = int(m.group(1))
            current_annex_label = f"Anexa nr. {current_annex_num}"
            current_annex_lines = []
            current_capitol = f"Anexa nr. {current_annex_num}"
            current_sectiune = None
            pending_title = 'annex'  # next line(s) may be the annex title
            continue

        # If we're collecting annex content, accumulate lines
        if current_annex_num is not None:
            # Handle pending annex title (first non-empty line after "Anexa nr. X")
            if pending_title == 'annex' and stripped:
                # Check if it's a structural element instead of a title
                is_next_annex = anexa_re.match(stripped)
                is_articol = articol_re.match(stripped)
                if not is_next_annex and not is_articol:
                    current_annex_label = f"Anexa nr. {current_annex_num} - {stripped}"
                    pending_title = None
                    continue
                else:
                    pending_title = None
                    # Fall through to process as structural
            elif pending_title == 'annex' and not stripped:
                continue

            # Check if an Articolul appears inside annex context
            # (some documents have articles after annexes)
            m_art = articol_re.match(stripped)
            if m_art:
                flush_annex()
                current_art_raw = m_art.group(1)
                current_art_num = parse_superscript_number(current_art_raw)
                current_art_lines = []
                continue

            current_annex_lines.append(line)
            continue

        # Handle pending title (new format: title on separate line)
        if pending_title and stripped:
            # Check if this line is actually a structural element (not a title)
            is_structural = (
                capitol_re.match(stripped)
                or sectiune_re.match(stripped)
                or articol_re.match(stripped)
                or paragraf_re.match(stripped)
                or anexa_re.match(stripped)
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
    flush_annex()

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
                        capitol=(rec_data["capitol"] or "")[:500] or None,
                        sectiune=(rec_data["sectiune"] or "")[:500] or None,
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
                capitol=(rec_data["capitol"] or "")[:500] or None,
                sectiune=(rec_data["sectiune"] or "")[:500] or None,
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
        db_ok = await init_db()
        if not db_ok:
            print("ERROR: Could not connect to database.")
            print("Make sure DATABASE_URL is exported:")
            print('  export DATABASE_URL="postgresql+asyncpg://..."')
            print("Or use inline syntax:")
            print('  DATABASE_URL="..." python scripts/import_legislatie.py')
            sys.exit(1)

    from app.services.llm.gemini import GeminiProvider
    llm = GeminiProvider()
    embedding_service = EmbeddingService(llm_provider=llm)

    totals = {"inserted": 0, "updated": 0, "removed": 0, "unchanged": 0}
    failed_files: list[tuple[str, str]] = []  # (filename, error_message)
    start = time.time()

    for blob_name in blob_names:
        filename = Path(blob_name).name
        print(f"Downloading {filename} from GCS...")
        try:
            file_text = download_file(bucket, blob_name)
        except Exception as e:
            msg = f"Download failed: {e}"
            print(f"  ⚠ {msg}, skipping")
            failed_files.append((filename, msg))
            continue

        try:
            result = await import_file(
                filename, file_text, embedding_service,
                force=args.force, update=args.update, dry_run=args.dry_run,
            )
            for k in totals:
                totals[k] += result[k]
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"  ⚠ Import failed for {filename}: {msg}, skipping")
            logger.error("import_file_failed", file=filename, error=msg)
            failed_files.append((filename, msg))
            continue

    elapsed = time.time() - start
    print(
        f"\nDone in {elapsed:.1f}s! "
        f"Inserted: {totals['inserted']}, "
        f"Updated: {totals['updated']}, "
        f"Removed: {totals['removed']}, "
        f"Unchanged: {totals['unchanged']}"
    )

    if failed_files:
        print(f"\n⚠ {len(failed_files)} file(s) could not be imported:")
        for fname, err in failed_files:
            print(f"  - {fname}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
