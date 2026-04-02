#!/usr/bin/env python3
"""Import ANAP spete (cazuistică oficială) from JSON into the database.

Reads the JSON file from GCS (default: gs://date-expert-app/spete-anap/)
or from a local path. Filters out archived spete and category
"24. Spete care nu mai sunt valabile".

Each speță contains: question + official ANAP answer + category + tags.
Embeddings are generated for RAG vector search.

Usage:
    # Import from GCS (default)
    DATABASE_URL="..." python scripts/import_spete_anap.py

    # Import from local file
    DATABASE_URL="..." python scripts/import_spete_anap.py --local docs/biblioteca_spete_anap.json

    # Dry run (parse only, no DB writes)
    DATABASE_URL="..." python scripts/import_spete_anap.py --dry-run

    # Force reimport (delete all + reinsert)
    DATABASE_URL="..." python scripts/import_spete_anap.py --force

    # Limit number of spete to import
    DATABASE_URL="..." python scripts/import_spete_anap.py --limit 10
"""

import asyncio
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select, delete, text
from app.core.logging import get_logger
from app.db.session import init_db
from app.db import session as db_session
from app.models.decision import SpetaANAP
from app.services.embedding import EmbeddingService

logger = get_logger(__name__)

# GCS config
GCS_BUCKET = "date-expert-app"
GCS_FOLDER = "spete-anap"
GCS_FILENAME = "biblioteca_spete_anap.json"

# Category to exclude (no longer valid spete)
EXCLUDED_CATEGORY = "24. Spete care nu mai sunt valabile"

# Batch sizes
EMBED_BATCH_SIZE = 100
COMMIT_BATCH_SIZE = 100


def load_from_gcs() -> dict:
    """Download and parse JSON from GCS."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(f"{GCS_FOLDER}/{GCS_FILENAME}")
    content = blob.download_as_text()
    return json.loads(content)


def load_from_local(path: str) -> dict:
    """Load and parse JSON from local file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def filter_active_spete(data: dict) -> list[dict]:
    """Filter to active spete only, excluding archived and cat 24."""
    spete = data.get("spete", [])
    active = [
        s for s in spete
        if not s.get("arhivata", False)
        and s.get("categorie", "") != EXCLUDED_CATEGORY
    ]
    return active


def compose_embedding_text(speta: dict) -> str:
    """Compose text for embedding generation."""
    parts = [f"Categorie: {speta['categorie']}"]
    if speta.get("taguri"):
        parts.append(f"Taguri: {', '.join(speta['taguri'])}")
    parts.append(f"\nÎntrebare: {speta['intrebare']}")
    parts.append(f"\nRăspuns ANAP: {speta['raspuns']}")
    return "\n".join(parts)


async def main():
    parser = argparse.ArgumentParser(description="Import ANAP spete into database")
    parser.add_argument("--local", type=str, help="Path to local JSON file (skip GCS)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--force", action="store_true", help="Delete all and reimport")
    parser.add_argument("--limit", type=int, help="Limit number of spete to import")
    parser.add_argument("--skip-embeddings", action="store_true",
                        help="Import without generating embeddings")
    args = parser.parse_args()

    # Load JSON
    print("Loading spete JSON...")
    try:
        if args.local:
            data = load_from_local(args.local)
            print(f"  Loaded from local: {args.local}")
        else:
            data = load_from_gcs()
            print(f"  Loaded from GCS: gs://{GCS_BUCKET}/{GCS_FOLDER}/{GCS_FILENAME}")
    except Exception as e:
        print(f"ERROR: Could not load JSON: {e}")
        sys.exit(1)

    # Filter
    active_spete = filter_active_spete(data)
    total_in_file = len(data.get("spete", []))
    print(f"  Total in file: {total_in_file}")
    print(f"  Active (non-archived, non-cat24): {len(active_spete)}")

    if args.limit:
        active_spete = active_spete[:args.limit]
        print(f"  Limited to: {len(active_spete)}")

    if args.dry_run:
        # Show stats and exit
        categories = {}
        for s in active_spete:
            cat = s["categorie"]
            categories[cat] = categories.get(cat, 0) + 1
        print(f"\nDry run — {len(active_spete)} spete would be imported:")
        for cat, count in sorted(categories.items()):
            print(f"  {cat}: {count}")
        return

    # Init DB
    db_ok = await init_db()
    if not db_ok:
        print("ERROR: Could not connect to database.")
        print("Make sure DATABASE_URL is exported:")
        print('  export DATABASE_URL="postgresql+asyncpg://..."')
        sys.exit(1)

    # Force mode: delete all existing
    if args.force:
        async with db_session.async_session_factory() as session:
            result = await session.execute(delete(SpetaANAP))
            await session.commit()
            print(f"  Force mode: deleted {result.rowcount} existing spete")

    # Check existing numar_speta in DB for idempotency
    async with db_session.async_session_factory() as session:
        result = await session.execute(
            select(SpetaANAP.numar_speta)
        )
        existing_numere = set(result.scalars().all())
    print(f"  Existing in DB: {len(existing_numere)}")

    # Filter out already imported
    to_import = [
        s for s in active_spete
        if s["numarSpeta"] not in existing_numere
    ]
    print(f"  New to import: {len(to_import)}")

    if not to_import:
        print("\nNothing to import — all spete already in DB.")
        return

    # Init embedding service
    if not args.skip_embeddings:
        from app.services.llm.gemini import GeminiProvider
        llm = GeminiProvider()
        embedding_service = EmbeddingService(llm_provider=llm)

    start = time.time()
    total_inserted = 0
    total_embedded = 0

    # Process in batches
    for batch_start in range(0, len(to_import), COMMIT_BATCH_SIZE):
        batch = to_import[batch_start:batch_start + COMMIT_BATCH_SIZE]
        batch_num = batch_start // COMMIT_BATCH_SIZE + 1
        total_batches = (len(to_import) + COMMIT_BATCH_SIZE - 1) // COMMIT_BATCH_SIZE
        print(f"\nBatch {batch_num}/{total_batches} ({len(batch)} spete)...")

        # Generate embeddings for this batch
        embeddings = None
        if not args.skip_embeddings:
            texts = [compose_embedding_text(s) for s in batch]
            try:
                embeddings = await embedding_service.embed_batch(
                    texts,
                    batch_size=EMBED_BATCH_SIZE,
                    rate_limit_delay=1.0,
                )
                total_embedded += len(embeddings)
                print(f"  Generated {len(embeddings)} embeddings")
            except Exception as e:
                print(f"  WARNING: Embedding generation failed: {e}")
                print("  Inserting without embeddings (run generate_embeddings later)")
                embeddings = None

        # Insert into DB
        async with db_session.async_session_factory() as session:
            for i, speta_data in enumerate(batch):
                # Parse datetime
                dt_str = speta_data["dataPublicarii"]
                try:
                    dt = datetime.fromisoformat(dt_str)
                    # Make naive for asyncpg compatibility
                    if dt.tzinfo is not None:
                        dt = dt.replace(tzinfo=None)
                except (ValueError, TypeError):
                    dt = datetime.utcnow()

                speta = SpetaANAP(
                    id=str(uuid4()),
                    numar_speta=speta_data["numarSpeta"],
                    versiune=speta_data.get("versiune", 1),
                    data_publicarii=dt,
                    categorie=speta_data["categorie"],
                    intrebare=speta_data["intrebare"],
                    raspuns=speta_data["raspuns"],
                    taguri=speta_data.get("taguri", []),
                    embedding=embeddings[i] if embeddings and i < len(embeddings) else None,
                )
                session.add(speta)

            await session.commit()
            total_inserted += len(batch)
            print(f"  Committed {len(batch)} spete")

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s!")
    print(f"  Inserted: {total_inserted}")
    print(f"  Embeddings: {total_embedded}")
    print(f"  Skipped (already in DB): {len(active_spete) - len(to_import)}")


if __name__ == "__main__":
    asyncio.run(main())
