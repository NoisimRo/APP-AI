#!/usr/bin/env python3
"""Deduce CPV codes for decisions that don't have one.

Uses embedding similarity between the obiect_contract field and the
CPV nomenclator descriptions to find the best matching CPV code.

IMPORTANT: Only processes decisions that have obiect_contract filled in.
Decisions without obiect_contract are skipped entirely — no fallback to
text_integral or rezumat (those produce unreliable CPV matches).

Quality filters (to avoid assigning wrong CPV codes):
- Text validation: min 3 real words, min 10 alphanumeric chars, max 70% junk
- Procedural text truncation: cuts "s-a solicitat", "s-au solicitat" etc.
- Similarity threshold: default 0.80 (raised from 0.70)
- Confidence gap: top1 - top2 must be >= 0.03, otherwise ambiguous

Usage:
    python scripts/deduce_cpv.py                    # Deduce for all without CPV
    python scripts/deduce_cpv.py --limit 10         # Test with 10 decisions
    python scripts/deduce_cpv.py --dry-run           # Show matches without applying
    python scripts/deduce_cpv.py --threshold 0.75    # Custom threshold
    python scripts/deduce_cpv.py --min-gap 0.05      # Stricter confidence gap
    python scripts/deduce_cpv.py --top-k 5           # Show top 5 candidates per decision
"""

import asyncio
import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select
from app.core.logging import get_logger
from app.db.session import init_db
from app.db import session as db_session
from app.models.decision import DecizieCNSC, NomenclatorCPV

logger = get_logger(__name__)

DEFAULT_THRESHOLD = 0.80
DEFAULT_MIN_GAP = 0.03
MIN_ALNUM_CHARS = 10
MIN_WORD_COUNT = 3
MAX_JUNK_RATIO = 0.70
BATCH_SIZE = 20  # Embedding API batch size

# ---------------------------------------------------------------------------
# Text quality validation & cleanup
# ---------------------------------------------------------------------------

# Procedural markers — everything after these is not part of the contract object
_PROCEDURAL_CUT_RE = re.compile(
    r",?\s*s-a(?:u)?\s+solicitat"
    r"|,?\s*următoarele\s*:"
    r"|,?\s*contestat(?:oarea|orul)?\s+a\s+solicitat"
    r"|,?\s*prin\s+care\s+se\s+solicit[ăa]"
    r"|,?\s*s-a\s+formulat",
    re.IGNORECASE,
)


def _clean_for_embedding(text: str) -> str | None:
    """Clean and validate obiect_contract text for CPV embedding.

    Returns cleaned text or None if text is too noisy to produce a reliable match.
    """
    if not text:
        return None

    # Truncate at procedural markers
    m = _PROCEDURAL_CUT_RE.search(text)
    if m:
        text = text[:m.start()].strip()

    # Strip trailing punctuation/whitespace
    text = text.strip().rstrip(".,;:-–—")

    # Count alphanumeric characters
    alnum_chars = sum(1 for c in text if c.isalnum())
    total_chars = len(text)

    # Filter 1: minimum alphanumeric characters
    if alnum_chars < MIN_ALNUM_CHARS:
        return None

    # Filter 2: junk ratio (dots, quotes, symbols vs real text)
    if total_chars > 0 and (total_chars - alnum_chars) / total_chars > MAX_JUNK_RATIO:
        return None

    # Filter 3: minimum word count (real words, not dots/symbols)
    words = [w for w in re.split(r'\s+', text) if len(w) >= 2 and any(c.isalpha() for c in w)]
    if len(words) < MIN_WORD_COUNT:
        return None

    return text


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a_arr = np.array(a)
    b_arr = np.array(b)
    dot = np.dot(a_arr, b_arr)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


async def load_cpv_nomenclator(session) -> list[dict]:
    """Load all CPV codes with descriptions and pre-computed embeddings from DB."""
    result = await session.execute(
        select(
            NomenclatorCPV.cod_cpv,
            NomenclatorCPV.descriere,
            NomenclatorCPV.categorie_achizitii,
            NomenclatorCPV.clasa_produse,
            NomenclatorCPV.embedding,
        )
    )
    cpv_list = []
    for cod, desc, cat, clasa, emb in result.all():
        if desc and len(desc) > 3:
            cpv_list.append({
                "cod_cpv": cod,
                "descriere": desc,
                "categorie": cat,
                "clasa": clasa,
                "embedding": list(emb) if emb is not None else None,
            })
    return cpv_list


async def generate_cpv_embeddings(embedding_service, cpv_list: list[dict]) -> list[dict]:
    """Generate embeddings for CPV descriptions missing them.

    Uses pre-computed embeddings from DB when available (populated by
    generate_embeddings.py). Only generates for entries without embeddings.
    """
    with_emb = [c for c in cpv_list if c["embedding"] is not None]
    without_emb = [c for c in cpv_list if c["embedding"] is None]

    print(f"CPV embeddings: {len(with_emb)} from DB, {len(without_emb)} need generation")

    if not without_emb:
        return cpv_list

    # Generate only for missing entries
    print(f"Generating embeddings for {len(without_emb)} CPV codes...")
    texts = [cpv["descriere"] for cpv in without_emb]

    all_embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        embeddings = await embedding_service.embed_batch(batch)
        all_embeddings.extend(embeddings)
        if (i + BATCH_SIZE) % 200 == 0:
            print(f"  Embedded {min(i + BATCH_SIZE, len(texts))}/{len(texts)} CPV codes")

    for j, cpv in enumerate(without_emb):
        cpv["embedding"] = all_embeddings[j]

    print(f"  Done: {len(without_emb)} new CPV embeddings generated")
    return cpv_list


def find_best_cpv(query_embedding: list[float], cpv_list: list[dict], top_k: int = 3) -> list[dict]:
    """Find the best matching CPV codes for a query embedding."""
    scores = []
    for cpv in cpv_list:
        sim = cosine_similarity(query_embedding, cpv["embedding"])
        scores.append({"cod_cpv": cpv["cod_cpv"], "descriere": cpv["descriere"],
                        "categorie": cpv["categorie"], "clasa": cpv["clasa"],
                        "similarity": sim})
    scores.sort(key=lambda x: x["similarity"], reverse=True)
    return scores[:top_k]


async def main():
    parser = argparse.ArgumentParser(description="Deduce CPV codes via embedding similarity")
    parser.add_argument("--limit", type=int, help="Max decisions to process")
    parser.add_argument("--dry-run", action="store_true", help="Show matches without applying")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help=f"Similarity threshold (default {DEFAULT_THRESHOLD})")
    parser.add_argument("--min-gap", type=float, default=DEFAULT_MIN_GAP, help=f"Min gap between top1 and top2 (default {DEFAULT_MIN_GAP})")
    parser.add_argument("--top-k", type=int, default=3, help="Show top K candidates per decision")
    parser.add_argument("--rate-limit", type=float, default=0.2, help="Delay between decisions")
    args = parser.parse_args()

    await init_db()

    from app.services.embedding import EmbeddingService
    embedding_service = EmbeddingService()

    async with db_session.async_session_factory() as session:
        # Load CPV nomenclator
        cpv_list = await load_cpv_nomenclator(session)
        print(f"Loaded {len(cpv_list)} CPV codes from nomenclator")

        if not cpv_list:
            print("ERROR: No CPV codes in nomenclator. Import them first.")
            return

        # Generate CPV embeddings
        cpv_list = await generate_cpv_embeddings(embedding_service, cpv_list)

        # Find decisions: has obiect_contract but no CPV
        stmt = (
            select(DecizieCNSC)
            .where(
                DecizieCNSC.cod_cpv.is_(None),
                DecizieCNSC.obiect_contract.isnot(None),
                DecizieCNSC.obiect_contract != "",
            )
            .order_by(DecizieCNSC.created_at.desc())
        )
        if args.limit:
            stmt = stmt.limit(args.limit)

        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        print(f"\n{'='*70}")
        print(f"Decisions without CPV: {len(decisions)}")
        print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
        print(f"Threshold: {args.threshold}  |  Min gap: {args.min_gap}")
        print(f"Text filters: min {MIN_ALNUM_CHARS} alnum chars, min {MIN_WORD_COUNT} words, max {MAX_JUNK_RATIO:.0%} junk")
        print(f"{'='*70}\n")

        stats = {
            "total": len(decisions),
            "assigned": 0,
            "below_threshold": 0,
            "no_gap": 0,
            "bad_text": 0,
        }
        start_time = time.time()

        for i, decision in enumerate(decisions):
            raw_text = decision.obiect_contract

            # Validate and clean text
            query_text = _clean_for_embedding(raw_text)
            if not query_text:
                stats["bad_text"] += 1
                if args.dry_run:
                    preview = (raw_text or "")[:80]
                    print(f"  [{i+1}] {decision.external_id}: SKIP (bad text) — {preview}")
                continue

            try:
                # Generate embedding for query text
                query_embeddings = await embedding_service.embed_batch([query_text])
                query_embedding = query_embeddings[0]

                # Find best matches (always need at least 2 for gap check)
                k = max(args.top_k, 2)
                matches = find_best_cpv(query_embedding, cpv_list, k)
                best = matches[0]
                second = matches[1] if len(matches) > 1 else None

                # Check threshold
                if best["similarity"] < args.threshold:
                    stats["below_threshold"] += 1
                    print(f"  [{i+1}] {decision.external_id}: LOW SIM — "
                          f"{best['cod_cpv']} ({best['descriere'][:40]}) sim={best['similarity']:.3f}")
                    if args.dry_run:
                        print(f"         obiect: {query_text[:120]}")
                        if args.top_k > 1:
                            for m in matches[1:args.top_k]:
                                print(f"         alt: {m['cod_cpv']} ({m['descriere'][:40]}) sim={m['similarity']:.3f}")
                    continue

                # Check confidence gap
                gap = best["similarity"] - second["similarity"] if second else 1.0
                if gap < args.min_gap:
                    stats["no_gap"] += 1
                    print(f"  [{i+1}] {decision.external_id}: NO GAP — "
                          f"{best['cod_cpv']} sim={best['similarity']:.3f} vs "
                          f"{second['cod_cpv']} sim={second['similarity']:.3f} "
                          f"(gap={gap:.3f} < {args.min_gap})")
                    if args.dry_run:
                        print(f"         obiect: {query_text[:120]}")
                        for m in matches[1:args.top_k]:
                            print(f"         alt: {m['cod_cpv']} ({m['descriere'][:40]}) sim={m['similarity']:.3f}")
                    continue

                # Match is good — assign
                print(f"  [{i+1}] {decision.external_id}: {best['cod_cpv']} "
                      f"({best['descriere'][:60]}) — sim={best['similarity']:.3f} gap={gap:.3f}")
                if args.dry_run:
                    print(f"         obiect: {query_text[:120]}")

                if not args.dry_run:
                    decision.cod_cpv = best["cod_cpv"]
                    decision.cpv_descriere = best["descriere"]
                    decision.cpv_categorie = best["categorie"]
                    decision.cpv_clasa = best["clasa"]
                    decision.cpv_source = "dedus"
                    await session.commit()

                stats["assigned"] += 1

            except Exception as e:
                logger.error("cpv_deduction_failed", external_id=decision.external_id, error=str(e))

            if i < len(decisions) - 1:
                await asyncio.sleep(args.rate_limit)

        elapsed = time.time() - start_time

        print(f"\n{'='*70}")
        print(f"CPV DEDUCTION COMPLETE")
        print(f"  Assigned:        {stats['assigned']}/{stats['total']}")
        print(f"  Below threshold: {stats['below_threshold']} (sim < {args.threshold})")
        print(f"  No gap:          {stats['no_gap']} (top1-top2 < {args.min_gap})")
        print(f"  Bad text:        {stats['bad_text']} (junk/too short/no words)")
        print(f"  Time:            {elapsed:.1f}s")
        print(f"{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
