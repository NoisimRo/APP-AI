"""Decision models for CNSC decisions."""

from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Decision(Base):
    """A CNSC decision."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)

    # Basic metadata
    case_number: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    bulletin: Mapped[Optional[str]] = mapped_column(String(50))
    year: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    # Classification
    ruling: Mapped[Optional[str]] = mapped_column(String(20), index=True)  # ADMIS, RESPINS, PARTIAL
    cpv_codes: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    criticism_codes: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    # Parties
    contestator: Mapped[Optional[str]] = mapped_column(String(500))
    authority: Mapped[Optional[str]] = mapped_column(String(500))
    intervenients: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    # Content
    title: Mapped[Optional[str]] = mapped_column(String(1000))
    full_text: Mapped[str] = mapped_column(Text)

    # Source
    source_file: Mapped[Optional[str]] = mapped_column(String(500))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    chunks: Mapped[list["DecisionChunk"]] = relationship(
        "DecisionChunk", back_populates="decision", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_decisions_cpv_codes", cpv_codes, postgresql_using="gin"),
        Index("ix_decisions_criticism_codes", criticism_codes, postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<Decision {self.external_id}: {self.ruling}>"


class DecisionChunk(Base):
    """A chunk of a decision for RAG retrieval."""

    __tablename__ = "decision_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("decisions.id", ondelete="CASCADE"), index=True
    )

    # Chunk content
    content: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer)  # Position in original document

    # Metadata
    section: Mapped[Optional[str]] = mapped_column(String(100))  # e.g., "parties", "facts", "reasoning"
    start_char: Mapped[Optional[int]] = mapped_column(Integer)
    end_char: Mapped[Optional[int]] = mapped_column(Integer)

    # Vector embedding (1024 dimensions for multilingual-e5-large)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(1024))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    decision: Mapped["Decision"] = relationship("Decision", back_populates="chunks")

    __table_args__ = (
        Index(
            "ix_decision_chunks_embedding",
            embedding,
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<DecisionChunk {self.decision_id}:{self.chunk_index}>"
