#!/usr/bin/env python3
"""Import CNSC decisions from GCS bucket to database.

This script:
1. Connects to GCS bucket (date-expert-app/decizii-cnsc)
2. Downloads and parses decision files
3. Saves to PostgreSQL database
4. Generates embeddings for semantic search

Usage:
    python scripts/import_decisions_from_gcs.py --bucket date-expert-app --folder decizii-cnsc
    python scripts/import_decisions_from_gcs.py --limit 100  # Import only first 100
    python scripts/import_decisions_from_gcs.py --skip-embeddings  # Skip embedding generation
"""

import asyncio
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from google.cloud import storage
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import init_db, Base
from app.db import session as db_session
from app.models.decision import DecizieCNSC, ArgumentareCritica
from app.services.analysis import DecisionAnalysisService
from app.services.embedding import EmbeddingService
from app.services.parser import parse_decision_text

logger = get_logger(__name__)


class DecisionImporter:
    """Import CNSC decisions from GCS to database."""

    def __init__(
        self,
        bucket_name: str,
        folder_name: str,
        project_id: Optional[str] = None,
        skip_embeddings: bool = False,
    ):
        self.bucket_name = bucket_name
        self.folder_name = folder_name
        self.project_id = project_id
        self.skip_embeddings = skip_embeddings
        self.storage_client = None
        self.bucket = None

    def connect_to_gcs(self) -> None:
        """Connect to GCS bucket."""
        logger.info("gcs_connecting", bucket=self.bucket_name, project=self.project_id)

        try:
            if self.project_id:
                self.storage_client = storage.Client(project=self.project_id)
            else:
                # Use default credentials
                self.storage_client = storage.Client()

            self.bucket = self.storage_client.bucket(self.bucket_name)
            logger.info("gcs_connected", bucket=self.bucket_name)

        except Exception as e:
            logger.error("gcs_connection_failed", error=str(e))
            raise

    def list_decision_files(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[str]:
        """List all decision files in the GCS folder.

        Args:
            limit: Maximum number of files to return (for testing/batching)
            offset: Number of files to skip from the beginning (for batching)

        Returns:
            List of blob names (file paths in GCS)
        """
        prefix = f"{self.folder_name}/" if self.folder_name else ""
        blobs = self.bucket.list_blobs(prefix=prefix, timeout=300)

        files = []
        skipped = 0
        for blob in blobs:
            # Only process .txt files
            if blob.name.endswith('.txt'):
                if skipped < offset:
                    skipped += 1
                    continue
                files.append(blob.name)
                if limit and len(files) >= limit:
                    break

        logger.info(
            "gcs_files_listed",
            count=len(files),
            offset=offset,
            prefix=prefix,
        )
        return files

    def download_file(self, blob_name: str) -> str:
        """Download a file from GCS and return its content.

        Args:
            blob_name: Name of the blob (file path in GCS)

        Returns:
            File content as string
        """
        blob = self.bucket.blob(blob_name)

        try:
            content = blob.download_as_text(encoding='utf-8', timeout=120)
        except UnicodeDecodeError:
            # Fallback to latin-1
            content = blob.download_as_text(encoding='latin-1', timeout=120)

        return content

    async def import_decision(
        self,
        session: AsyncSession,
        blob_name: str,
        content: str,
    ) -> tuple[str, Optional[str], Optional[str]]:
        """Parse and import a single decision.

        Args:
            session: Database session
            blob_name: Name of the file in GCS
            content: File content

        Returns:
            Tuple of (status, decision_id, error_message) where status is one of:
            "imported", "already_existed", "skipped", "failed"
        """
        filename = Path(blob_name).name

        try:
            # Parse decision
            parsed = parse_decision_text(content, source_file=filename)

            # Skip decisions with invalid parsing (an_bo=0 or numar_bo=0)
            # These would violate the unique constraint ix_decizii_bo_unique
            if parsed.an_bo == 0 or parsed.numar_bo == 0:
                reason = (
                    f"Invalid BO metadata: an_bo={parsed.an_bo}, numar_bo={parsed.numar_bo}"
                )
                if parsed.parse_warnings:
                    reason += f" ({parsed.parse_warnings[0]})"
                logger.warning(
                    "decision_skipped_invalid_parsing",
                    filename=filename,
                    an_bo=parsed.an_bo,
                    numar_bo=parsed.numar_bo,
                    reason=reason,
                )
                return ("skipped", None, f"{filename}: {reason}")

            # Check if already exists (by filename OR by BO number)
            result = await session.execute(
                select(DecizieCNSC).where(
                    (DecizieCNSC.filename == filename) |
                    ((DecizieCNSC.an_bo == parsed.an_bo) & (DecizieCNSC.numar_bo == parsed.numar_bo))
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                logger.info("decision_already_exists", filename=filename, id=existing.id)
                return ("already_existed", existing.id, None)

            # Create database record
            decision = DecizieCNSC(
                filename=parsed.filename,
                numar_bo=parsed.numar_bo,
                an_bo=parsed.an_bo,
                numar_decizie=parsed.numar_decizie,
                complet=parsed.complet,
                data_decizie=parsed.data_decizie,
                tip_contestatie=parsed.tip_contestatie.value,
                coduri_critici=parsed.coduri_critici,
                cod_cpv=parsed.cod_cpv,
                cpv_source=parsed.cpv_source,
                solutie_filename=parsed.solutie_filename.value,
                solutie_contestatie=parsed.solutie_contestatie.value if parsed.solutie_contestatie else None,
                motiv_respingere=parsed.motiv_respingere,
                contestator=parsed.contestator,
                autoritate_contractanta=parsed.autoritate_contractanta,
                intervenienti=parsed.intervenienti,
                text_integral=parsed.text_integral,
                parse_warnings=parsed.parse_warnings,
            )

            # Use savepoint so a single failure doesn't poison the batch session
            async with session.begin_nested():
                session.add(decision)
                await session.flush()

            logger.info(
                "decision_imported",
                filename=filename,
                id=decision.id,
                external_id=decision.external_id,
            )

            return ("imported", decision.id, None)

        except Exception as e:
            logger.error(
                "decision_import_failed",
                filename=filename,
                error=str(e),
            )
            return ("failed", None, f"{filename}: {str(e)}")

    async def _load_existing_filenames(self) -> set[str]:
        """Pre-load all existing filenames from DB for fast skip checks."""
        async with db_session.async_session_factory() as session:
            result = await session.execute(
                select(DecizieCNSC.filename)
            )
            return {row[0] for row in result.all()}

    async def import_all(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
        batch_size: int = 50,
    ) -> dict:
        """Import all decisions from GCS.

        Args:
            limit: Maximum number of files to import (for testing/batching)
            offset: Number of files to skip from the beginning (for batching)
            batch_size: Number of decisions to commit in each batch

        Returns:
            Dictionary with import statistics
        """
        stats = {
            "total_files": 0,
            "imported": 0,
            "already_existed": 0,
            "skipped": 0,
            "failed": 0,
            "errors": [],
            "skipped_files": [],
        }

        # Pre-load existing filenames to skip without downloading
        existing_filenames = await self._load_existing_filenames()
        logger.info("existing_filenames_loaded", count=len(existing_filenames))

        # Get list of files
        files = self.list_decision_files(limit=limit, offset=offset)
        stats["total_files"] = len(files)

        # Filter out already-imported files before downloading
        new_files = []
        for blob_name in files:
            fname = Path(blob_name).name
            if fname in existing_filenames:
                stats["already_existed"] += 1
            else:
                new_files.append(blob_name)

        logger.info(
            "import_starting",
            total_files=len(files),
            already_existed=stats["already_existed"],
            new_files=len(new_files),
        )

        # Process only new files in batches with parallel downloads
        loop = asyncio.get_event_loop()
        for i in range(0, len(new_files), batch_size):
            batch = new_files[i:i + batch_size]

            # Download all files in this batch concurrently (10 threads)
            with ThreadPoolExecutor(max_workers=10) as executor:
                download_futures = {
                    blob_name: loop.run_in_executor(executor, self.download_file, blob_name)
                    for blob_name in batch
                }
                downloaded = {}
                for blob_name, future in download_futures.items():
                    try:
                        downloaded[blob_name] = await future
                    except Exception as e:
                        stats["failed"] += 1
                        stats["errors"].append(f"{blob_name}: download failed: {e}")
                        logger.error("download_failed", file=blob_name, error=str(e))

            logger.info(
                "batch_downloaded",
                batch_num=i // batch_size + 1,
                downloaded=len(downloaded),
                failed=len(batch) - len(downloaded),
            )

            async with db_session.async_session_factory() as session:
                for blob_name, content in downloaded.items():
                    try:
                        # Import to database
                        status, decision_id, error_msg = await self.import_decision(
                            session, blob_name, content
                        )

                        if status == "imported":
                            stats["imported"] += 1
                        elif status == "already_existed":
                            stats["already_existed"] += 1
                        elif status == "skipped":
                            stats["skipped"] += 1
                            stats["skipped_files"].append(error_msg)
                        else:  # failed
                            stats["failed"] += 1
                            if error_msg:
                                stats["errors"].append(error_msg)

                    except Exception as e:
                        stats["failed"] += 1
                        error_msg = f"{blob_name}: {str(e)}"
                        stats["errors"].append(error_msg)
                        logger.error("file_processing_failed", file=blob_name, error=str(e))

                # Commit batch
                try:
                    await session.commit()
                    logger.info(
                        "batch_committed",
                        batch_num=i // batch_size + 1,
                        processed=min(i + batch_size, len(files)),
                        total=len(files),
                    )
                except Exception as e:
                    await session.rollback()
                    logger.error("batch_commit_failed", error=str(e))
                    stats["failed"] += len(batch)

        # Generate embeddings if not skipped
        if not self.skip_embeddings:
            logger.info("generating_embeddings_post_import")
            try:
                async with db_session.async_session_factory() as emb_session:
                    embedding_service = EmbeddingService()
                    emb_count = await embedding_service.generate_embeddings_for_argumentari(
                        emb_session
                    )
                    await emb_session.commit()
                    stats["embeddings_generated"] = emb_count
                    logger.info("embeddings_generated", count=emb_count)
            except Exception as e:
                logger.error("embedding_generation_failed", error=str(e))
                stats["embedding_error"] = str(e)

        logger.info("import_completed", **stats)
        return stats


async def create_tables():
    """Create database tables."""
    logger.info("creating_database_tables")

    if db_session.engine is None:
        raise RuntimeError("Database engine not initialized. Call init_db() first.")

    async with db_session.engine.begin() as conn:
        # Enable pgvector extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

        # Create all tables
        await conn.run_sync(Base.metadata.create_all)

    logger.info("database_tables_created")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Import CNSC decisions from GCS")

    parser.add_argument(
        "--bucket",
        default="date-expert-app",
        help="GCS bucket name (default: date-expert-app)",
    )
    parser.add_argument(
        "--folder",
        default="decizii-cnsc",
        help="Folder in bucket (default: decizii-cnsc)",
    )
    parser.add_argument(
        "--project",
        default="gen-lang-client-0706147575",
        help="GCP project ID",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of files to import (for testing/batching)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip first N files (for batching, e.g. --offset 100 --limit 100)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Batch size for commits (default: 50)",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Skip embedding generation",
    )
    parser.add_argument(
        "--create-tables",
        action="store_true",
        help="Create database tables before importing",
    )
    parser.add_argument(
        "--embeddings-only",
        action="store_true",
        help="Only generate embeddings (skip GCS import)",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="After import, analyze decisions with LLM to extract ArgumentareCritica",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Only analyze existing decisions (skip GCS import)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-analyze decisions that already have ArgumentareCritica records",
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

    # Create tables if requested
    if args.create_tables:
        await create_tables()

    # Analyze-only mode: extract ArgumentareCritica from existing decisions
    if args.analyze_only:
        logger.info("analyze_only_mode", overwrite=args.overwrite)
        analysis_service = DecisionAnalysisService()

        if args.overwrite:
            # Re-analyze ALL decisions (including already analyzed ones)
            async with db_session.async_session_factory() as session:
                stats = await analysis_service.analyze_all(
                    session, limit=args.limit, overwrite=True
                )
        else:
            async with db_session.async_session_factory() as session:
                stats = await analysis_service.analyze_all_unprocessed(
                    session, limit=args.limit
                )

        print("\n" + "=" * 60)
        print("ANALYSIS SUMMARY")
        print("=" * 60)
        print(f"Decisions to analyze: {stats['total']}")
        print(f"Successfully analyzed: {stats['analyzed']}")
        print(f"ArgumentareCritica created: {stats['argumentari_created']}")
        print(f"Failed: {stats['failed']}")

        if stats['errors']:
            print(f"\nErrors:")
            for error in stats['errors'][:10]:
                print(f"  - {error}")

        print("=" * 60)

        # Generate embeddings for new/updated argumentari
        if stats['argumentari_created'] > 0 and not args.skip_embeddings:
            print("\nGenerating embeddings for argumentari...")
            async with db_session.async_session_factory() as emb_session:
                embedding_service = EmbeddingService()
                emb_count = await embedding_service.generate_embeddings_for_argumentari(
                    emb_session, force=args.overwrite
                )
                await emb_session.commit()
                print(f"Embeddings generated: {emb_count}")

        return

    # Embeddings-only mode: skip GCS import, just generate embeddings
    if args.embeddings_only:
        logger.info("embeddings_only_mode")
        async with db_session.async_session_factory() as session:
            embedding_service = EmbeddingService()
            stats = await embedding_service.get_embedding_stats(session)
            print(f"\nCurrent coverage: {stats['argumentari']['embedded']}/{stats['argumentari']['total']} argumentari")

            count = await embedding_service.generate_embeddings_for_argumentari(
                session, limit=args.limit
            )
            await session.commit()

            stats = await embedding_service.get_embedding_stats(session)
            print(f"Generated {count} embeddings")
            print(f"Updated coverage: {stats['argumentari']['embedded']}/{stats['argumentari']['total']} argumentari")
        return

    # Initialize importer
    importer = DecisionImporter(
        bucket_name=args.bucket,
        folder_name=args.folder,
        project_id=args.project,
        skip_embeddings=args.skip_embeddings,
    )

    # Connect to GCS
    try:
        importer.connect_to_gcs()
    except Exception as e:
        logger.error("gcs_connection_failed", error=str(e))
        print(f"ERROR: Could not connect to GCS: {e}")
        print("Make sure you have:")
        print("1. gcloud CLI installed and authenticated")
        print("2. Proper permissions to access the bucket")
        print("3. GOOGLE_APPLICATION_CREDENTIALS set (if using service account)")
        sys.exit(1)

    # Import decisions
    stats = await importer.import_all(
        limit=args.limit,
        offset=args.offset,
        batch_size=args.batch_size,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("IMPORT SUMMARY")
    print("=" * 60)
    print(f"Total files found: {stats['total_files']}")
    print(f"Successfully imported: {stats['imported']}")
    print(f"Already existed: {stats['already_existed']}")
    print(f"Skipped (invalid filename/metadata): {stats.get('skipped', 0)}")
    print(f"Failed: {stats['failed']}")

    if stats.get('skipped_files'):
        print(f"\nSkipped files ({len(stats['skipped_files'])}):")
        for skipped in stats['skipped_files'][:10]:
            print(f"  - {skipped}")
        if len(stats['skipped_files']) > 10:
            print(f"  ... and {len(stats['skipped_files']) - 10} more")

    if stats['errors']:
        print(f"\nErrors ({len(stats['errors'])}):")
        for error in stats['errors'][:10]:  # Show first 10 errors
            print(f"  - {error}")
        if len(stats['errors']) > 10:
            print(f"  ... and {len(stats['errors']) - 10} more")

    if 'embeddings_generated' in stats:
        print(f"\nEmbeddings generated: {stats['embeddings_generated']}")
    if 'embedding_error' in stats:
        print(f"\nEmbedding error: {stats['embedding_error']}")

    print("=" * 60)

    # Analyze decisions with LLM if requested
    if args.analyze and stats['imported'] > 0:
        print("\n>>> Analyzing decisions with LLM to extract ArgumentareCritica...")
        analysis_service = DecisionAnalysisService()
        async with db_session.async_session_factory() as session:
            analysis_stats = await analysis_service.analyze_all_unprocessed(
                session, limit=args.limit
            )

        print(f"\nAnalysis: {analysis_stats['analyzed']} decisions analyzed, "
              f"{analysis_stats['argumentari_created']} argumentari created, "
              f"{analysis_stats['failed']} failed")

        if analysis_stats['errors']:
            for error in analysis_stats['errors'][:5]:
                print(f"  - {error}")

        # Regenerate embeddings for newly created argumentari
        if analysis_stats['argumentari_created'] > 0 and not args.skip_embeddings:
            print("\n>>> Generating embeddings for new argumentari...")
            async with db_session.async_session_factory() as emb_session:
                embedding_service = EmbeddingService()
                emb_count = await embedding_service.generate_embeddings_for_argumentari(
                    emb_session
                )
                await emb_session.commit()
                print(f"Embeddings generated: {emb_count}")

    # Exit with error code if there were hard failures (not skipped)
    if stats['failed'] > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
