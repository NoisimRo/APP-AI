#!/usr/bin/env python3
"""Generate vector embeddings for CNSC decision data.

Populates the embedding column in ArgumentareCritica using the Gemini
embedding model. These embeddings enable semantic vector search in the
RAG pipeline.

Features:
- Skips rows that already have embeddings (idempotent)
- Per-batch commit (crash-safe, no progress lost on failure)
- Retry with exponential backoff on API errors
- Progress reporting

Usage:
    python scripts/generate_embeddings.py                    # Generate all missing embeddings
    python scripts/generate_embeddings.py --force            # Regenerate all embeddings
    python scripts/generate_embeddings.py --limit 10         # Process only 10 rows (testing)
    python scripts/generate_embeddings.py --batch-size 20    # API batch size (default: 20)
"""

import asyncio
import argparse
import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select, func
from app.core.logging import get_logger
from app.db.session import init_db
from app.db import session as db_session
from app.models.decision import ArgumentareCritica
from app.services.embedding import EmbeddingService
from app.services.llm.base import ResourceExhaustedError

logger = get_logger(__name__)

# Commit after this many rows are embedded (not API batch size)
COMMIT_BATCH_SIZE = 100


async def generate_embeddings_batched(
    embedding_service: EmbeddingService,
    force: bool = False,
    limit: int | None = None,
    api_batch_size: int = 20,
    rate_limit: float = 1.0,
) -> int:
    """Generate embeddings with per-batch commits for crash safety.

    Args:
        embedding_service: The embedding service instance.
        force: Regenerate all embeddings.
        limit: Max rows to process.
        api_batch_size: Texts per API call.
        rate_limit: Seconds between API batches.

    Returns:
        Total embeddings generated.
    """
    # Count total to process
    async with db_session.async_session_factory() as session:
        count_stmt = select(func.count()).select_from(ArgumentareCritica)
        if not force:
            count_stmt = count_stmt.where(ArgumentareCritica.embedding.is_(None))
        total_to_process = await session.scalar(count_stmt)

    if limit:
        total_to_process = min(total_to_process, limit)

    if total_to_process == 0:
        print("  No argumentari need embeddings.")
        return 0

    print(f"  Processing {total_to_process} argumentari...")

    total_generated = 0

    while total_generated < total_to_process:
        batch_target = min(COMMIT_BATCH_SIZE, total_to_process - total_generated)

        async with db_session.async_session_factory() as session:
            # Fetch next batch of rows needing embeddings
            stmt = select(ArgumentareCritica)
            if not force:
                stmt = stmt.where(ArgumentareCritica.embedding.is_(None))
            stmt = stmt.limit(batch_target)

            result = await session.execute(stmt)
            rows = list(result.scalars().all())

            if not rows:
                break

            # Compose texts
            valid_pairs = []
            for row in rows:
                text = EmbeddingService.compose_text_for_argumentare(row)
                if text.strip():
                    valid_pairs.append((row, text))

            if not valid_pairs:
                break

            valid_rows, valid_texts = zip(*valid_pairs)

            # Generate embeddings (retry is handled inside embed_batch)
            try:
                embeddings = await embedding_service.embed_batch(
                    list(valid_texts),
                    batch_size=api_batch_size,
                    rate_limit_delay=rate_limit,
                )
            except ResourceExhaustedError as e:
                print(f"\n  RESOURCE EXHAUSTED: {e}")
                print("  API quota or rate limit exceeded. Stopping immediately.")
                print(f"  Progress saved: {total_generated} embeddings committed so far.")
                print("  Wait for quota to reset and re-run to continue.")
                return total_generated
            except Exception as e:
                print(f"  ERROR: {e}")
                print(f"  Progress saved: {total_generated} embeddings committed so far.")
                break

            # Store embeddings
            for row, embedding in zip(valid_rows, embeddings):
                row.embedding = embedding

            # Commit this batch
            await session.commit()
            batch_count = len(embeddings)
            total_generated += batch_count

            print(
                f"  [{total_generated}/{total_to_process}] "
                f"Committed batch of {batch_count} embeddings"
            )

    return total_generated


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate embeddings for CNSC decision data"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate all embeddings, not just missing ones",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Max number of rows to process (for testing)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Number of texts per API call (default: 20)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=1.0,
        help="Seconds to wait between API batches (default: 1.0)",
    )

    args = parser.parse_args()

    # Initialize database
    print("Connecting to database...")
    db_initialized = await init_db()
    if not db_initialized:
        print("ERROR: Could not connect to database")
        print("Make sure DATABASE_URL is set and database is accessible")
        sys.exit(1)

    # Show current stats
    embedding_service = EmbeddingService()
    async with db_session.async_session_factory() as session:
        stats = await embedding_service.get_embedding_stats(session)

    print(f"\nCurrent embedding coverage:")
    print(f"  ArgumentareCritica: {stats['argumentari']['embedded']}/{stats['argumentari']['total']}")
    print()

    start_time = time.time()

    # Generate embeddings
    print("=== ArgumentareCritica ===")
    total_generated = await generate_embeddings_batched(
        embedding_service,
        force=args.force,
        limit=args.limit,
        api_batch_size=args.batch_size,
        rate_limit=args.rate_limit,
    )

    # Show updated stats
    async with db_session.async_session_factory() as session:
        stats = await embedding_service.get_embedding_stats(session)

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print("EMBEDDING SUMMARY")
    print(f"{'=' * 60}")
    print(f"Generated: {total_generated} embeddings in {elapsed:.1f}s ({elapsed/60:.1f}min)")
    print(f"\nUpdated coverage:")
    print(f"  ArgumentareCritica: {stats['argumentari']['embedded']}/{stats['argumentari']['total']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
