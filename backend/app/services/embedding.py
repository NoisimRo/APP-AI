"""Embedding generation service for ExpertAP.

Handles embedding generation, storage, and retrieval for semantic search
over CNSC decisions. Uses ArgumentareCritica as the primary semantic chunk
(natural chunking by criticism).
"""

import asyncio
from typing import Optional

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.decision import ArgumentareCritica
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_embedding_provider

logger = get_logger(__name__)

# text-embedding-004 has ~8192 token limit; truncate long texts
MAX_EMBEDDING_TEXT_LENGTH = 8000


class EmbeddingService:
    """Service for generating and managing vector embeddings."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm = llm_provider or get_embedding_provider()

    # -------------------------------------------------------------------------
    # Text composition (chunking)
    # -------------------------------------------------------------------------

    @staticmethod
    def compose_text_for_argumentare(arg: ArgumentareCritica) -> str:
        """Compose embeddable text from an ArgumentareCritica row.

        ArgumentareCritica rows are natural semantic chunks of a decision,
        each covering a single criticism with full argumentation flow.
        Includes jurisprudence references for better semantic matching.
        """
        parts: list[str] = []

        if arg.cod_critica:
            parts.append(f"Critica: {arg.cod_critica}")

        if arg.argumente_contestator:
            parts.append(f"Argumente contestator: {arg.argumente_contestator}")

        if arg.jurisprudenta_contestator:
            parts.append(f"Jurisprudență contestator: {'; '.join(arg.jurisprudenta_contestator)}")

        if arg.argumente_ac:
            parts.append(f"Argumente autoritate contractantă: {arg.argumente_ac}")

        if arg.jurisprudenta_ac:
            parts.append(f"Jurisprudență AC: {'; '.join(arg.jurisprudenta_ac)}")

        if arg.argumente_intervenienti:
            for interv in arg.argumente_intervenienti:
                nr = interv.get("nr", "?")
                parts.append(f"Argumente intervenient #{nr}: {interv.get('argumente', '')}")
                jp = interv.get("jurisprudenta", [])
                if jp:
                    parts.append(f"Jurisprudență intervenient #{nr}: {'; '.join(jp)}")

        if arg.elemente_retinute_cnsc:
            parts.append(f"Elemente reținute CNSC: {arg.elemente_retinute_cnsc}")

        if arg.argumentatie_cnsc:
            parts.append(f"Argumentație CNSC: {arg.argumentatie_cnsc}")

        if arg.jurisprudenta_cnsc:
            parts.append(f"Jurisprudență CNSC: {'; '.join(arg.jurisprudenta_cnsc)}")

        if arg.castigator_critica and arg.castigator_critica != "unknown":
            parts.append(f"Câștigător: {arg.castigator_critica}")

        text = "\n".join(parts)
        return text[:MAX_EMBEDDING_TEXT_LENGTH] if len(text) > MAX_EMBEDDING_TEXT_LENGTH else text

    # -------------------------------------------------------------------------
    # Embedding generation
    # -------------------------------------------------------------------------

    async def embed_single(
        self,
        text: str,
        task_type: str = "retrieval_document",
    ) -> list[float]:
        """Generate embedding for a single text."""
        results = await self.llm.embed([text], task_type=task_type)
        return results[0]

    async def embed_query(self, text: str) -> list[float]:
        """Generate embedding for a search query (asymmetric retrieval)."""
        return await self.embed_single(text, task_type="retrieval_query")

    async def embed_batch(
        self,
        texts: list[str],
        batch_size: int = 20,
        task_type: str = "retrieval_document",
        rate_limit_delay: float = 1.0,
    ) -> list[list[float]]:
        """Generate embeddings in batches with rate limiting and retry.

        Args:
            texts: List of texts to embed.
            batch_size: Number of texts per API call (Gemini limit ~100).
            task_type: Embedding task type.
            rate_limit_delay: Seconds to wait between batches.

        Returns:
            List of embedding vectors in the same order as input texts.
        """
        all_embeddings: list[list[float]] = []
        max_retries = 3

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]

            for attempt in range(1, max_retries + 1):
                try:
                    embeddings = await self.llm.embed(batch, task_type=task_type)
                    all_embeddings.extend(embeddings)
                    break
                except Exception as e:
                    if attempt < max_retries:
                        delay = 2.0 ** attempt
                        logger.warning(
                            "embedding_batch_retry",
                            batch_start=i,
                            attempt=attempt,
                            delay=delay,
                            error=str(e),
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "embedding_batch_failed",
                            batch_start=i,
                            error=str(e),
                        )
                        raise

            # Rate limiting between batches
            if i + batch_size < len(texts):
                await asyncio.sleep(rate_limit_delay)

            logger.debug(
                "embedding_batch_completed",
                batch_start=i,
                batch_end=min(i + batch_size, len(texts)),
                total=len(texts),
            )

        return all_embeddings

    # -------------------------------------------------------------------------
    # Batch embedding generation for database tables
    # -------------------------------------------------------------------------

    async def generate_embeddings_for_argumentari(
        self,
        session: AsyncSession,
        force: bool = False,
        limit: Optional[int] = None,
    ) -> int:
        """Generate and store embeddings for ArgumentareCritica rows.

        Args:
            session: Database session.
            force: If True, regenerate all embeddings (not just missing ones).
            limit: Max number of rows to process (for testing).

        Returns:
            Number of embeddings generated.
        """
        # Query rows that need embeddings
        stmt = select(ArgumentareCritica)
        if not force:
            stmt = stmt.where(ArgumentareCritica.embedding.is_(None))
        if limit:
            stmt = stmt.limit(limit)

        result = await session.execute(stmt)
        rows = list(result.scalars().all())

        if not rows:
            logger.info("no_argumentari_need_embeddings")
            return 0

        logger.info("generating_argumentari_embeddings", count=len(rows))

        # Diagnostic: check actual column typmod from this connection
        diag = await session.execute(text(
            "SELECT a.atttypmod, format_type(a.atttypid, a.atttypmod) "
            "FROM pg_attribute a JOIN pg_class c ON a.attrelid = c.oid "
            "WHERE c.relname = 'argumentare_critica' AND a.attname = 'embedding'"
        ))
        typmod_row = diag.first()
        if typmod_row:
            raw_typmod, fmt = typmod_row
            logger.info(
                "embedding_column_diagnostic",
                raw_typmod=raw_typmod,
                format_type=fmt,
            )
            if raw_typmod != -1 and raw_typmod != 2000:
                logger.warning(
                    "embedding_column_wrong_dimension",
                    expected=2000,
                    actual=raw_typmod,
                    message="Fixing column dimension via DROP+ADD",
                )
                await session.execute(text("DROP INDEX IF EXISTS ix_arg_embedding_hnsw"))
                await session.execute(text("ALTER TABLE argumentare_critica DROP COLUMN embedding"))
                await session.execute(text("ALTER TABLE argumentare_critica ADD COLUMN embedding vector(2000)"))
                await session.execute(text(
                    "CREATE INDEX ix_arg_embedding_hnsw ON argumentare_critica "
                    "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
                ))
                await session.commit()
                logger.info("embedding_column_fixed", new_typmod=2000)
                # Re-query rows since column was recreated
                result = await session.execute(
                    select(ArgumentareCritica).where(ArgumentareCritica.embedding.is_(None))
                )
                rows = list(result.scalars().all())
                if not rows:
                    return 0

        # Compose texts
        texts = [self.compose_text_for_argumentare(row) for row in rows]

        # Filter out empty texts
        valid_pairs = [(row, text) for row, text in zip(rows, texts) if text.strip()]

        if not valid_pairs:
            logger.warning("all_argumentari_texts_empty")
            return 0

        valid_rows, valid_texts = zip(*valid_pairs)

        # Generate embeddings in batches
        embeddings = await self.embed_batch(list(valid_texts))

        # Store embeddings
        for row, embedding in zip(valid_rows, embeddings):
            row.embedding = embedding

        await session.flush()

        logger.info("argumentari_embeddings_generated", count=len(embeddings))
        return len(embeddings)

    async def get_embedding_stats(self, session: AsyncSession) -> dict:
        """Get statistics about embedding coverage."""
        arg_total = await session.scalar(
            select(func.count()).select_from(ArgumentareCritica)
        )
        arg_embedded = await session.scalar(
            select(func.count())
            .select_from(ArgumentareCritica)
            .where(ArgumentareCritica.embedding.isnot(None))
        )

        return {
            "argumentari": {"total": arg_total, "embedded": arg_embedded},
        }
