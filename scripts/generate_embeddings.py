#!/usr/bin/env python3
"""Generate vector embeddings for CNSC decision data.

Populates the embedding columns in ArgumentareCritica and CitatVerbatim
tables using the Gemini text-embedding-004 model. These embeddings enable
semantic vector search in the RAG pipeline.

Usage:
    python scripts/generate_embeddings.py                    # Generate all missing embeddings
    python scripts/generate_embeddings.py --force            # Regenerate all embeddings
    python scripts/generate_embeddings.py --limit 10         # Process only 10 rows (testing)
    python scripts/generate_embeddings.py --table argumentari  # Only ArgumentareCritica
    python scripts/generate_embeddings.py --table citate       # Only CitatVerbatim
"""

import asyncio
import argparse
import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.core.logging import get_logger
from app.db.session import init_db
from app.db import session as db_session
from app.services.embedding import EmbeddingService

logger = get_logger(__name__)


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Generate embeddings for CNSC decision data")

    parser.add_argument(
        "--table",
        choices=["argumentari", "citate", "all"],
        default="all",
        help="Which table to generate embeddings for (default: all)",
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

    args = parser.parse_args()

    # Initialize database
    logger.info("initializing_database")
    db_initialized = await init_db()

    if not db_initialized:
        logger.error("database_initialization_failed")
        print("ERROR: Could not connect to database")
        print("Make sure DATABASE_URL is set and database is accessible")
        sys.exit(1)

    # Initialize embedding service
    embedding_service = EmbeddingService()

    start_time = time.time()
    total_generated = 0

    async with db_session.async_session_factory() as session:
        # Show current stats
        stats = await embedding_service.get_embedding_stats(session)
        print("\nCurrent embedding coverage:")
        print(f"  ArgumentareCritica: {stats['argumentari']['embedded']}/{stats['argumentari']['total']}")
        print(f"  CitatVerbatim:      {stats['citate']['embedded']}/{stats['citate']['total']}")
        print()

        # Generate embeddings
        if args.table in ("argumentari", "all"):
            print("Generating embeddings for ArgumentareCritica...")
            count = await embedding_service.generate_embeddings_for_argumentari(
                session, force=args.force, limit=args.limit
            )
            total_generated += count
            print(f"  Generated {count} embeddings")

        if args.table in ("citate", "all"):
            print("Generating embeddings for CitatVerbatim...")
            count = await embedding_service.generate_embeddings_for_citate(
                session, force=args.force, limit=args.limit
            )
            total_generated += count
            print(f"  Generated {count} embeddings")

        # Commit all changes
        await session.commit()

        # Show updated stats
        stats = await embedding_service.get_embedding_stats(session)
        print("\nUpdated embedding coverage:")
        print(f"  ArgumentareCritica: {stats['argumentari']['embedded']}/{stats['argumentari']['total']}")
        print(f"  CitatVerbatim:      {stats['citate']['embedded']}/{stats['citate']['total']}")

    elapsed = time.time() - start_time
    print(f"\nDone. Generated {total_generated} embeddings in {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
