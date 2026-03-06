"""upgrade vector columns from 768 to 2000 dimensions

Revision ID: 20260306_0003
Revises: 20260305_0002
Create Date: 2026-03-06

pgvector HNSW indexes support up to 2000 dimensions.
gemini-embedding-001 outputs 3072 natively, capped to 2000 via output_dimensionality.
Previous migrations created columns as vector(768) - this upgrades them to vector(2000).
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '20260306_0003'
down_revision = '20260305_0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop existing HNSW indexes (they reference the old vector size)
    op.execute("DROP INDEX IF EXISTS ix_arg_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_sectiuni_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_citate_embedding_hnsw")

    # Alter embedding columns from vector(768) to vector(2000)
    op.execute("ALTER TABLE argumentare_critica ALTER COLUMN embedding TYPE vector(2000)")
    op.execute("ALTER TABLE sectiuni_decizie ALTER COLUMN embedding TYPE vector(2000)")
    op.execute("ALTER TABLE citate_verbatim ALTER COLUMN embedding TYPE vector(2000)")

    # Recreate HNSW indexes on the new vector(2000) columns
    op.execute("""
        CREATE INDEX ix_arg_embedding_hnsw
        ON argumentare_critica
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)
    op.execute("""
        CREATE INDEX ix_sectiuni_embedding_hnsw
        ON sectiuni_decizie
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)
    op.execute("""
        CREATE INDEX ix_citate_embedding_hnsw
        ON citate_verbatim
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    # Drop HNSW indexes
    op.execute("DROP INDEX IF EXISTS ix_arg_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_sectiuni_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_citate_embedding_hnsw")

    # Revert to vector(768)
    op.execute("ALTER TABLE argumentare_critica ALTER COLUMN embedding TYPE vector(768)")
    op.execute("ALTER TABLE sectiuni_decizie ALTER COLUMN embedding TYPE vector(768)")
    op.execute("ALTER TABLE citate_verbatim ALTER COLUMN embedding TYPE vector(768)")

    # Recreate HNSW indexes with old size
    op.execute("""
        CREATE INDEX ix_arg_embedding_hnsw
        ON argumentare_critica
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)
    op.execute("""
        CREATE INDEX ix_sectiuni_embedding_hnsw
        ON sectiuni_decizie
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)
    op.execute("""
        CREATE INDEX ix_citate_embedding_hnsw
        ON citate_verbatim
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)
