"""upgrade to hnsw indexes and add sectiuni embedding

Revision ID: 20260305_0002
Revises: 20251225_0001
Create Date: 2026-03-05

Upgrades vector indexes from IVFFlat to HNSW for better recall and
no-training-needed operation. Adds embedding column to sectiuni_decizie
and HNSW indexes to all vector-bearing tables.
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = '20260305_0002'
down_revision = '20251225_0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old IVFFlat index on argumentare_critica
    op.drop_index('ix_arg_embedding', table_name='argumentare_critica', if_exists=True)

    # Create HNSW index on argumentare_critica (better recall, no training)
    op.execute("""
        CREATE INDEX ix_arg_embedding_hnsw
        ON argumentare_critica
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # Add embedding column to sectiuni_decizie
    op.add_column(
        'sectiuni_decizie',
        sa.Column('embedding', Vector(768), nullable=True),
    )

    # Create HNSW index on sectiuni_decizie
    op.execute("""
        CREATE INDEX ix_sectiuni_embedding_hnsw
        ON sectiuni_decizie
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # Create HNSW index on citate_verbatim
    op.execute("""
        CREATE INDEX ix_citate_embedding_hnsw
        ON citate_verbatim
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    # Drop HNSW indexes
    op.drop_index('ix_citate_embedding_hnsw', table_name='citate_verbatim', if_exists=True)
    op.drop_index('ix_sectiuni_embedding_hnsw', table_name='sectiuni_decizie', if_exists=True)
    op.drop_column('sectiuni_decizie', 'embedding')
    op.drop_index('ix_arg_embedding_hnsw', table_name='argumentare_critica', if_exists=True)

    # Restore IVFFlat index on argumentare_critica
    op.execute("""
        CREATE INDEX ix_arg_embedding
        ON argumentare_critica
        USING ivfflat (embedding vector_cosine_ops)
    """)
