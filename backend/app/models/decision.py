"""Decision models for CNSC decisions.

Schema follows the established pipeline specification for Romanian
public procurement decisions (CNSC - Consiliul Național de Soluționare
a Contestațiilor).
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func,
    Boolean, Enum as SQLEnum, CheckConstraint
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


# =============================================================================
# MAIN DECISION TABLE
# =============================================================================

class DecizieCNSC(Base):
    """A CNSC decision - main table.

    Based on the established schema:
    - Filename convention: BO{AN}_{NR_BO}_{COD_CRITICI}_CPV_{COD_CPV}_{SOLUTIE}.txt
    - Contest types: 'documentatie' (D*) or 'rezultat' (R*)
    """

    __tablename__ = "decizii_cnsc"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4())
    )

    # Identifiers from filename
    filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    numar_bo: Mapped[int] = mapped_column(Integer, nullable=False)
    an_bo: Mapped[int] = mapped_column(Integer, nullable=False)

    # Metadata from text
    numar_decizie: Mapped[Optional[int]] = mapped_column(Integer)
    complet: Mapped[Optional[str]] = mapped_column(String(5))  # C1-C20
    data_decizie: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Contest type (deduced from criticism codes)
    tip_contestatie: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="documentatie"
    )  # 'documentatie' or 'rezultat'

    # Classification - criticism codes
    coduri_critici: Mapped[list] = mapped_column(
        ARRAY(String(10)),
        nullable=False,
        default=list
    )  # ['R2'], ['D1', 'D4']

    # CPV
    cod_cpv: Mapped[Optional[str]] = mapped_column(String(20))
    cpv_descriere: Mapped[Optional[str]] = mapped_column(Text)
    cpv_categorie: Mapped[Optional[str]] = mapped_column(String(50))  # Furnizare, Servicii, Lucrări
    cpv_clasa: Mapped[Optional[str]] = mapped_column(String(200))
    cpv_source: Mapped[Optional[str]] = mapped_column(String(20))  # 'filename', 'text_explicit', 'dedus'

    # Solution from filename
    solutie_filename: Mapped[Optional[str]] = mapped_column(String(1))  # A, R, X

    # Solution from text (detailed)
    solutie_contestatie: Mapped[Optional[str]] = mapped_column(
        String(20),
        index=True
    )  # ADMIS, ADMIS_PARTIAL, RESPINS
    motiv_respingere: Mapped[Optional[str]] = mapped_column(String(50))  # nefondată, tardivă, etc.

    # Procedural data (optional, extracted from text)
    data_initiere_procedura: Mapped[Optional[datetime]] = mapped_column(DateTime)
    data_raport_procedura: Mapped[Optional[datetime]] = mapped_column(DateTime)
    numar_anunt_participare: Mapped[Optional[str]] = mapped_column(String(50))
    valoare_estimata: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    moneda: Mapped[str] = mapped_column(String(3), default="RON")
    criteriu_atribuire: Mapped[Optional[str]] = mapped_column(String(100))
    numar_oferte: Mapped[Optional[int]] = mapped_column(Integer)

    # Parties
    contestator: Mapped[Optional[str]] = mapped_column(String(500))
    autoritate_contractanta: Mapped[Optional[str]] = mapped_column(String(500))
    intervenienti: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    # Full content
    text_integral: Mapped[str] = mapped_column(Text, nullable=False)

    # Parse metadata
    parse_warnings: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    sectiuni: Mapped[list["SectiuneDecizie"]] = relationship(
        "SectiuneDecizie", back_populates="decizie", cascade="all, delete-orphan"
    )
    argumentari: Mapped[list["ArgumentareCritica"]] = relationship(
        "ArgumentareCritica", back_populates="decizie", cascade="all, delete-orphan"
    )
    citate: Mapped[list["CitatVerbatim"]] = relationship(
        "CitatVerbatim", back_populates="decizie", cascade="all, delete-orphan"
    )
    referinte_articole: Mapped[list["ReferintaArticol"]] = relationship(
        "ReferintaArticol", back_populates="decizie", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_decizii_tip", tip_contestatie),
        Index("ix_decizii_critici", coduri_critici, postgresql_using="gin"),
        Index("ix_decizii_cpv", cod_cpv),
        Index("ix_decizii_solutie", solutie_contestatie),
        Index("ix_decizii_data", data_decizie),
        Index("ix_decizii_complet", complet),
        Index("ix_decizii_fulltext", text_integral, postgresql_using="gin",
              postgresql_ops={"text_integral": "gin_trgm_ops"}),
        Index("ix_decizii_bo_unique", an_bo, numar_bo, unique=True),
    )

    @property
    def external_id(self) -> str:
        """Generate external ID from BO year and number."""
        return f"BO{self.an_bo}_{self.numar_bo}"

    def __repr__(self) -> str:
        return f"<DecizieCNSC {self.external_id}: {self.solutie_contestatie}>"


# =============================================================================
# DECISION SECTIONS
# =============================================================================

class SectiuneDecizie(Base):
    """A logical section of a decision.

    Section types:
    - antet
    - solicitari_contestator
    - istoric
    - punct_vedere_ac
    - interventie
    - analiza_cnsc
    - dispozitiv
    """

    __tablename__ = "sectiuni_decizie"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4())
    )
    decizie_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("decizii_cnsc.id", ondelete="CASCADE"),
        index=True
    )

    # Section type
    tip_sectiune: Mapped[str] = mapped_column(String(50), nullable=False)
    ordine: Mapped[int] = mapped_column(Integer, nullable=False)

    # For multiple intervenients
    numar_intervenient: Mapped[Optional[int]] = mapped_column(Integer)

    # Content
    text_sectiune: Mapped[str] = mapped_column(Text, nullable=False)

    # Embedding for RAG
    embedding_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    decizie: Mapped["DecizieCNSC"] = relationship(
        "DecizieCNSC", back_populates="sectiuni"
    )

    __table_args__ = (
        Index("ix_sectiuni_decizie", decizie_id),
        Index("ix_sectiuni_tip", tip_sectiune),
    )

    def __repr__(self) -> str:
        return f"<SectiuneDecizie {self.decizie_id}:{self.tip_sectiune}>"


# =============================================================================
# ARGUMENTATION PER CRITICISM
# =============================================================================

class ArgumentareCritica(Base):
    """Argumentation flow for a specific criticism code.

    This is the KEY table for RAG - contains the complete argumentation
    for each criticism including who won.

    Structure per criticism:
    1. Argumente contestator + jurisprudență invocată
    2. Argumente AC + jurisprudență invocată
    3. Argumente intervenienți + jurisprudență
    4. ★ Elemente reținute CNSC + argumentație proprie (CRUCIAL!)
    5. ★ Câștigător per critică
    """

    __tablename__ = "argumentare_critica"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4())
    )
    decizie_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("decizii_cnsc.id", ondelete="CASCADE"),
        index=True
    )

    # Criticism being analyzed
    cod_critica: Mapped[str] = mapped_column(String(10), nullable=False)  # D1, R2, etc.
    ordine_in_decizie: Mapped[Optional[int]] = mapped_column(Integer)

    # Argumentation flow
    argumente_contestator: Mapped[Optional[str]] = mapped_column(Text)
    jurisprudenta_contestator: Mapped[Optional[list]] = mapped_column(
        ARRAY(Text), default=list
    )

    argumente_ac: Mapped[Optional[str]] = mapped_column(Text)
    jurisprudenta_ac: Mapped[Optional[list]] = mapped_column(
        ARRAY(Text), default=list
    )

    # Intervenients (can be multiple)
    # Format: [{"nr": 1, "argumente": "...", "jurisprudenta": ["..."]}]
    argumente_intervenienti: Mapped[Optional[dict]] = mapped_column(JSON)

    # ★ CRUCIAL: CNSC Analysis
    elemente_retinute_cnsc: Mapped[Optional[str]] = mapped_column(Text)
    argumentatie_cnsc: Mapped[Optional[str]] = mapped_column(Text)
    jurisprudenta_cnsc: Mapped[Optional[list]] = mapped_column(
        ARRAY(Text), default=list
    )

    # ★ Who won this criticism
    castigator_critica: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="unknown"
    )  # 'contestator', 'autoritate', 'partial', 'unknown'

    # Embedding for RAG semantic search
    embedding_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False))

    # Vector embedding (768 dimensions for text-embedding-004)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(768))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    decizie: Mapped["DecizieCNSC"] = relationship(
        "DecizieCNSC", back_populates="argumentari"
    )
    referinte: Mapped[list["ReferintaArticol"]] = relationship(
        "ReferintaArticol", back_populates="argumentare"
    )

    __table_args__ = (
        Index("ix_arg_decizie", decizie_id),
        Index("ix_arg_critica", cod_critica),
        Index("ix_arg_castigator", castigator_critica),
        Index(
            "ix_arg_embedding",
            embedding,
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<ArgumentareCritica {self.cod_critica}: {self.castigator_critica}>"


# =============================================================================
# VERBATIM QUOTES
# =============================================================================

class CitatVerbatim(Base):
    """Verbatim quotes from decisions for insertion in generated content.

    These are EXACT quotes that can be safely inserted in generated
    complaints/responses with [VERIFIED] tags.
    """

    __tablename__ = "citate_verbatim"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4())
    )

    # Source
    decizie_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("decizii_cnsc.id", ondelete="CASCADE"),
        index=True
    )
    sectiune_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sectiuni_decizie.id", ondelete="SET NULL")
    )
    argumentare_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("argumentare_critica.id", ondelete="SET NULL")
    )

    # EXACT text for insertion
    text_verbatim: Mapped[str] = mapped_column(Text, nullable=False)

    # Position in original text (for verification)
    pozitie_start: Mapped[Optional[int]] = mapped_column(Integer)
    pozitie_end: Mapped[Optional[int]] = mapped_column(Integer)

    # Context
    tip_citat: Mapped[Optional[str]] = mapped_column(String(30))
    # Types: 'argumentatie_cnsc', 'dispozitiv', 'referinta_legala'

    # Embedding for search
    embedding_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False))
    embedding: Mapped[Optional[list]] = mapped_column(Vector(768))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    decizie: Mapped["DecizieCNSC"] = relationship(
        "DecizieCNSC", back_populates="citate"
    )

    __table_args__ = (
        Index("ix_citate_decizie", decizie_id),
        Index("ix_citate_tip", tip_citat),
    )

    def __repr__(self) -> str:
        return f"<CitatVerbatim {self.decizie_id[:8]}...>"


# =============================================================================
# LEGAL ARTICLE REFERENCES
# =============================================================================

class ReferintaArticol(Base):
    """References to legal articles in decisions.

    Tracks which articles are invoked, by whom, and whether
    the argument was successful.
    """

    __tablename__ = "referinte_articole"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4())
    )
    decizie_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("decizii_cnsc.id", ondelete="CASCADE"),
        index=True
    )
    argumentare_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("argumentare_critica.id", ondelete="SET NULL")
    )

    # Article identification
    act_normativ: Mapped[str] = mapped_column(String(50), nullable=False)
    # e.g., "L98/2016", "HG395/2016", "L101/2016"
    articol: Mapped[str] = mapped_column(String(30), nullable=False)
    # e.g., "art. 210", "art. 196 alin. (2)"

    # How it appears
    tip_referinta: Mapped[Optional[str]] = mapped_column(String(20))
    # 'trimitere', 'citat_partial', 'citat_integral'
    text_citat: Mapped[Optional[str]] = mapped_column(Text)

    # Who invokes and in what context
    invocat_de: Mapped[Optional[str]] = mapped_column(String(20))
    # 'contestator', 'ac', 'intervenient', 'cnsc'

    # ★ Result: Was this argument successful?
    argument_castigator: Mapped[Optional[bool]] = mapped_column(Boolean)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    decizie: Mapped["DecizieCNSC"] = relationship(
        "DecizieCNSC", back_populates="referinte_articole"
    )
    argumentare: Mapped[Optional["ArgumentareCritica"]] = relationship(
        "ArgumentareCritica", back_populates="referinte"
    )

    __table_args__ = (
        Index("ix_ref_decizie", decizie_id),
        Index("ix_ref_articol", act_normativ, articol),
        Index("ix_ref_invocat", invocat_de),
        Index("ix_ref_castigator", argument_castigator),
    )

    def __repr__(self) -> str:
        return f"<ReferintaArticol {self.articol} ({self.act_normativ})>"


# =============================================================================
# CPV NOMENCLATOR
# =============================================================================

class NomenclatorCPV(Base):
    """CPV codes nomenclator for enrichment.

    Used to enrich decisions with CPV descriptions and
    enable filtering by category/class.
    """

    __tablename__ = "nomenclator_cpv"

    cod_cpv: Mapped[str] = mapped_column(String(20), primary_key=True)
    descriere: Mapped[str] = mapped_column(Text, nullable=False)
    categorie_achizitii: Mapped[Optional[str]] = mapped_column(String(50))
    # Furnizare, Servicii, Lucrări
    clasa_produse: Mapped[Optional[str]] = mapped_column(String(200))

    # CPV hierarchy
    cod_parinte: Mapped[Optional[str]] = mapped_column(String(20))
    nivel: Mapped[Optional[int]] = mapped_column(Integer)  # 1-8 granularity

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_cpv_categorie", categorie_achizitii),
        Index("ix_cpv_clasa", clasa_produse),
    )

    def __repr__(self) -> str:
        return f"<NomenclatorCPV {self.cod_cpv}>"


# =============================================================================
# LEGACY COMPATIBILITY (if needed)
# =============================================================================

# Alias for backwards compatibility with existing code
Decision = DecizieCNSC
DecisionChunk = ArgumentareCritica  # Conceptually similar for RAG
