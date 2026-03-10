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
    JSON, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY, TSVECTOR
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
    argumentari: Mapped[list["ArgumentareCritica"]] = relationship(
        "ArgumentareCritica", back_populates="decizie", cascade="all, delete-orphan"
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

    # Vector embedding (2000 dimensions - max for pgvector HNSW index)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(2000))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    decizie: Mapped["DecizieCNSC"] = relationship(
        "DecizieCNSC", back_populates="argumentari"
    )

    __table_args__ = (
        Index("ix_arg_decizie", decizie_id),
        Index("ix_arg_critica", cod_critica),
        Index("ix_arg_castigator", castigator_critica),
        Index(
            "ix_arg_embedding_hnsw",
            embedding,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )

    def __repr__(self) -> str:
        return f"<ArgumentareCritica {self.cod_critica}: {self.castigator_critica}>"


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
    denumire_en: Mapped[Optional[str]] = mapped_column(String(200))
    categorie_achizitii: Mapped[Optional[str]] = mapped_column(String(50))
    # Furnizare, Servicii, Lucrări
    clasa_produse: Mapped[Optional[str]] = mapped_column(String(200))

    # CPV hierarchy
    cod_parinte: Mapped[Optional[str]] = mapped_column(String(20))
    nivel: Mapped[Optional[int]] = mapped_column(Integer)  # 1-5: Diviziune, Grup, Clasă, Categorie, Subcategorie

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
# LEGISLATIVE ACTS (master table)
# =============================================================================

class ActNormativ(Base):
    """Master table for legislative acts.

    Normalizes act references — instead of repeating "Legea 98/2016" as a
    string in every fragment row, we use an FK to this table.
    """

    __tablename__ = "acte_normative"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4())
    )

    tip_act: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # Lege, HG, OUG, OG
    numar: Mapped[int] = mapped_column(Integer, nullable=False)  # 98, 395
    an: Mapped[int] = mapped_column(Integer, nullable=False)  # 2016
    titlu: Mapped[Optional[str]] = mapped_column(Text)
    data_publicare: Mapped[Optional[datetime]] = mapped_column(Date)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    fragmente: Mapped[list["LegislatieFragment"]] = relationship(
        "LegislatieFragment", back_populates="act", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_acte_unique", tip_act, numar, an, unique=True),
    )

    @property
    def denumire(self) -> str:
        """Human-readable name: 'Legea 98/2016', 'HG 395/2016'."""
        return f"{self.tip_act} {self.numar}/{self.an}"

    def __repr__(self) -> str:
        return f"<ActNormativ {self.denumire}>"


# =============================================================================
# LEGISLATION FRAGMENTS (for Red Flags grounding + RAG)
# =============================================================================

class LegislatieFragment(Base):
    """Fragment of Romanian procurement legislation at maximum granularity.

    Each row = the smallest independent legal unit:
    - Literă (if the alineat has litere)
    - Alineat (if no litere)
    - Articol (if no alineats)

    This enables exact citations like:
        "art. 2 alin. (2) lit. a) din Legea nr. 98/2016"

    Embedding is on the fragment text, but `articol_complet` provides
    the full article context for RAG when the model needs neighboring alineats.

    Used for vector search grounding in the Red Flags Detector —
    ensures legal references are real, not hallucinated by the LLM.
    """

    __tablename__ = "legislatie_fragmente"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4())
    )

    # FK to legislative act (normalized, not string)
    act_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("acte_normative.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Article identification
    numar_articol: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # 2, 178 (numeric for sorting)
    articol: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # "art. 2", "art. 178"

    # Alineat (NULL = article has no alineats)
    alineat: Mapped[Optional[int]] = mapped_column(Integer)  # 1, 2, 3...
    alineat_text: Mapped[Optional[str]] = mapped_column(
        String(20)
    )  # "alin. (1)", "alin. (2)"

    # Literă (NULL = no litera, fragment is at alineat or article level)
    litera: Mapped[Optional[str]] = mapped_column(
        String(5)
    )  # "a", "b", "c"...

    # Text of THIS specific fragment
    text_fragment: Mapped[str] = mapped_column(Text, nullable=False)

    # Full article text (all alineats + litere) for RAG context
    articol_complet: Mapped[Optional[str]] = mapped_column(Text)

    # Canonical citation (without act name, that's via FK)
    # e.g. "art. 2 alin. (2) lit. a)", "art. 1", "art. 178 alin. (3)"
    citare: Mapped[str] = mapped_column(
        String(150), nullable=False
    )

    # Context in the law structure
    capitol: Mapped[Optional[str]] = mapped_column(String(500))
    sectiune: Mapped[Optional[str]] = mapped_column(String(500))

    # Full-text search for legal queries
    keywords: Mapped[Optional[str]] = mapped_column(TSVECTOR)

    # Vector embedding (2000 dimensions - max for pgvector HNSW index)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(2000))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    act: Mapped["ActNormativ"] = relationship(
        "ActNormativ", back_populates="fragmente"
    )

    __table_args__ = (
        Index("ix_frag_act", act_id),
        Index("ix_frag_lookup", act_id, numar_articol, alineat, litera),
        Index("ix_frag_citare", act_id, citare),
        Index(
            "ix_frag_embedding_hnsw",
            embedding,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )

    def __repr__(self) -> str:
        act_name = self.act.denumire if self.act else "?"
        return f"<LegislatieFragment {self.citare} ({act_name})>"


# =============================================================================
# LLM SETTINGS (single-row global config)
# =============================================================================

class LLMSettings(Base):
    """Global LLM provider settings — single-row table (id=1 always).

    Stores the active provider, model, and encrypted API keys.
    """

    __tablename__ = "llm_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active_provider: Mapped[str] = mapped_column(
        String(30), nullable=False, default="gemini"
    )  # "gemini", "anthropic", "openai", "groq", "openrouter"
    active_model: Mapped[Optional[str]] = mapped_column(String(100))
    gemini_api_key_enc: Mapped[Optional[str]] = mapped_column(Text)
    anthropic_api_key_enc: Mapped[Optional[str]] = mapped_column(Text)
    openai_api_key_enc: Mapped[Optional[str]] = mapped_column(Text)
    groq_api_key_enc: Mapped[Optional[str]] = mapped_column(Text)
    openrouter_api_key_enc: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<LLMSettings provider={self.active_provider} model={self.active_model}>"


# =============================================================================
# SEARCH SCOPES (saved filter presets for RAG pre-filtering)
# =============================================================================

class SearchScope(Base):
    """Saved search scope — a named set of filters for pre-filtering RAG search.

    Users create scopes from the Data Lake filter UI to narrow down
    which decisions the AI assistant searches through.
    """

    __tablename__ = "search_scopes"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # JSONB filters — mirrors Data Lake filter params
    # Example: {"ruling": "ADMIS", "tip_contestatie": "rezultat",
    #           "years": [2025, 2026], "coduri_critici": ["D3", "R2"],
    #           "cpv_codes": ["55520000-1"], "search": "catering"}
    filters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Cached count of matching decisions (updated on save)
    decision_count: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<SearchScope '{self.name}' ({self.decision_count} decizii)>"


# =============================================================================
# LEGACY COMPATIBILITY (if needed)
# =============================================================================

# Alias for backwards compatibility with existing code
Decision = DecizieCNSC
DecisionChunk = ArgumentareCritica  # Conceptually similar for RAG
# Legacy alias — old code may reference this
ArticolLegislatie = LegislatieFragment
