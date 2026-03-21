#!/usr/bin/env python3
"""Deduce CPV codes for decisions that don't have one.

Uses embedding similarity between the contract object description
(or introductory text) and the CPV nomenclator descriptions to find
the best matching CPV code.

Approach:
1. Load all CPV codes with descriptions from nomenclator_cpv
2. Generate embeddings for CPV descriptions (cached in memory)
3. For each decision with cod_cpv IS NULL:
   - Use obiect_contract if available, else first 3000 chars of text
   - Generate embedding for the contract description
   - Cosine similarity with all CPV embeddings
   - If top match > threshold → assign CPV code
4. Enrich with cpv_descriere, cpv_categorie, cpv_clasa from nomenclator

Features:
- Skips decisions that already have CPV (idempotent)
- Per-decision commit (crash-safe)
- Dry-run mode for review before applying
- Threshold-based assignment (default 0.70)

Usage:
    python scripts/deduce_cpv.py                    # Deduce for all without CPV
    python scripts/deduce_cpv.py --limit 10         # Test with 10 decisions
    python scripts/deduce_cpv.py --dry-run           # Show matches without applying
    python scripts/deduce_cpv.py --threshold 0.75    # Stricter matching
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

DEFAULT_THRESHOLD = 0.70
BATCH_SIZE = 20  # Embedding API batch size

# ---------------------------------------------------------------------------
# Smart text extraction for CPV matching
# ---------------------------------------------------------------------------

# Patterns that indicate where the contract object description starts
_OBIECT_PATTERNS = [
    re.compile(
        r"(?:având\s+ca\s+obiect|obiectul\s+(?:contractului|achiziției|acordului[\s-]+cadru)"
        r"|obiect\s+(?:al\s+)?(?:contractului|achiziției)"
        r"|privind\s+(?:achiziția|furnizarea|prestarea|execuția)"
        r"|în\s+vederea\s+(?:încheierii|atribuirii))",
        re.IGNORECASE,
    ),
]


def extract_relevant_text(text: str, max_len: int = 1500) -> str | None:
    """Extract the most relevant fragment from text_integral for CPV matching.

    Instead of blindly taking the first 3000 chars (which contain boilerplate),
    find the zone around 'obiectul contractului' / 'având ca obiect' and extract
    a window around it.  Falls back to a shorter prefix if no pattern matches.
    """
    if not text:
        return None

    for pat in _OBIECT_PATTERNS:
        m = pat.search(text[:8000])  # search in first 8000 chars
        if m:
            start = max(0, m.start() - 100)
            end = min(len(text), m.start() + max_len)
            return text[start:end].strip()

    # Fallback: skip first 500 chars (header/boilerplate) and take next chunk
    if len(text) > 600:
        return text[500:500 + max_len].strip()

    return text[:max_len].strip() if len(text) > 20 else None


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

        # Find decisions without CPV
        stmt = (
            select(DecizieCNSC)
            .where(DecizieCNSC.cod_cpv.is_(None))
            .order_by(DecizieCNSC.created_at.desc())
        )
        if args.limit:
            stmt = stmt.limit(args.limit)

        result = await session.execute(stmt)
        decisions = list(result.scalars().all())

        print(f"\n{'='*60}")
        print(f"Decisions without CPV: {len(decisions)}")
        print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
        print(f"Threshold: {args.threshold}")
        print(f"{'='*60}\n")

        stats = {"total": len(decisions), "assigned": 0, "ambiguous": 0, "no_text": 0}
        start_time = time.time()

        for i, decision in enumerate(decisions):
            # Get text for embedding — priority order:
            # 1. obiect_contract (most specific, regex-extracted)
            # 2. rezumat (LLM-generated summary)
            # 3. Smart extraction from text_integral (zone around "obiectul contractului")
            query_text = None
            text_source = None

            if decision.obiect_contract and len(decision.obiect_contract) >= 10:
                query_text = decision.obiect_contract
                text_source = "obiect_contract"
            elif decision.rezumat and len(decision.rezumat) >= 20:
                query_text = decision.rezumat
                text_source = "rezumat"
            elif decision.text_integral:
                query_text = extract_relevant_text(decision.text_integral)
                text_source = "text_integral (smart)"

            if not query_text or len(query_text) < 10:
                stats["no_text"] += 1
                continue

            try:
                # Generate embedding for query text
                query_embeddings = await embedding_service.embed_batch([query_text])
                query_embedding = query_embeddings[0]

                # Find best matches
                matches = find_best_cpv(query_embedding, cpv_list, args.top_k)
                best = matches[0]

                if best["similarity"] >= args.threshold:
                    print(f"  [{i+1}] {decision.external_id}: {best['cod_cpv']} "
                          f"({best['descriere'][:60]}) — sim={best['similarity']:.3f} "
                          f"[src: {text_source}]")
                    if args.dry_run:
                        print(f"         query: {query_text[:120]}...")

                    if not args.dry_run:
                        decision.cod_cpv = best["cod_cpv"]
                        decision.cpv_descriere = best["descriere"]
                        decision.cpv_categorie = best["categorie"]
                        decision.cpv_clasa = best["clasa"]
                        decision.cpv_source = "dedus"
                        await session.commit()

                    stats["assigned"] += 1
                else:
                    print(f"  [{i+1}] {decision.external_id}: AMBIGUOUS — "
                          f"best={best['cod_cpv']} ({best['descriere'][:40]}) sim={best['similarity']:.3f} "
                          f"[src: {text_source}]")
                    if args.dry_run:
                        print(f"         query: {query_text[:120]}...")
                    if args.top_k > 1:
                        for m in matches[1:]:
                            print(f"         alt: {m['cod_cpv']} ({m['descriere'][:40]}) sim={m['similarity']:.3f}")
                    stats["ambiguous"] += 1

            except Exception as e:
                logger.error("cpv_deduction_failed", external_id=decision.external_id, error=str(e))

            if i < len(decisions) - 1:
                await asyncio.sleep(args.rate_limit)

        elapsed = time.time() - start_time

        print(f"\n{'='*60}")
        print(f"CPV DEDUCTION COMPLETE")
        print(f"  Assigned:  {stats['assigned']}/{stats['total']}")
        print(f"  Ambiguous: {stats['ambiguous']}")
        print(f"  No text:   {stats['no_text']}")
        print(f"  Time:      {elapsed:.1f}s")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
