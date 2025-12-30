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

    def list_decision_files(self, limit: Optional[int] = None) -> list[str]:
        """List all decision files in the GCS folder.

        Args:
            limit: Maximum number of files to return (for testing)

        Returns:
            List of blob names (file paths in GCS)
        """
        prefix = f"{self.folder_name}/" if self.folder_name else ""
        blobs = self.bucket.list_blobs(prefix=prefix)

        files = []
        for blob in blobs:
            # Only process .txt files
            if blob.name.endswith('.txt'):
                files.append(blob.name)
                if limit and len(files) >= limit:
                    break

        logger.info("gcs_files_listed", count=len(files), prefix=prefix)
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
            content = blob.download_as_text(encoding='utf-8')
        except UnicodeDecodeError:
            # Fallback to latin-1
            content = blob.download_as_text(encoding='latin-1')

        return content

    async def import_decision(
        self,
        session: AsyncSession,
        blob_name: str,
        content: str,
    ) -> Optional[str]:
        """Parse and import a single decision.

        Args:
            session: Database session
            blob_name: Name of the file in GCS
            content: File content

        Returns:
            Decision ID if successful, None otherwise
        """
        filename = Path(blob_name).name

        try:
            # Parse decision
            parsed = parse_decision_text(content, source_file=filename)

            # Check if already exists
            result = await session.execute(
                select(DecizieCNSC).where(DecizieCNSC.filename == filename)
            )
            existing = result.scalar_one_or_none()

            if existing:
                logger.info("decision_already_exists", filename=filename, id=existing.id)
                return existing.id

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

            session.add(decision)
            await session.flush()

            logger.info(
                "decision_imported",
                filename=filename,
                id=decision.id,
                external_id=decision.external_id,
            )

            return decision.id

        except Exception as e:
            logger.error(
                "decision_import_failed",
                filename=filename,
                error=str(e),
            )
            return None

    async def import_all(
        self,
        limit: Optional[int] = None,
        batch_size: int = 50,
    ) -> dict:
        """Import all decisions from GCS.

        Args:
            limit: Maximum number of files to import (for testing)
            batch_size: Number of decisions to commit in each batch

        Returns:
            Dictionary with import statistics
        """
        stats = {
            "total_files": 0,
            "imported": 0,
            "already_existed": 0,
            "failed": 0,
            "errors": [],
        }

        # Get list of files
        files = self.list_decision_files(limit=limit)
        stats["total_files"] = len(files)

        logger.info("import_starting", total_files=len(files))

        # Process in batches
        for i in range(0, len(files), batch_size):
            batch = files[i:i + batch_size]

            async with db_session.async_session_factory() as session:
                for blob_name in batch:
                    try:
                        # Download file
                        content = self.download_file(blob_name)

                        # Import to database
                        decision_id = await self.import_decision(
                            session, blob_name, content
                        )

                        if decision_id:
                            stats["imported"] += 1
                        else:
                            stats["failed"] += 1

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
        help="Limit number of files to import (for testing)",
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
        batch_size=args.batch_size,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("IMPORT SUMMARY")
    print("=" * 60)
    print(f"Total files found: {stats['total_files']}")
    print(f"Successfully imported: {stats['imported']}")
    print(f"Already existed: {stats['already_existed']}")
    print(f"Failed: {stats['failed']}")

    if stats['errors']:
        print(f"\nErrors ({len(stats['errors'])}):")
        for error in stats['errors'][:10]:  # Show first 10 errors
            print(f"  - {error}")
        if len(stats['errors']) > 10:
            print(f"  ... and {len(stats['errors']) - 10} more")

    print("=" * 60)

    # Exit with error code if there were failures
    if stats['failed'] > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
