"""widen cod_critica from varchar(10) to varchar(100)

Revision ID: 20260306_0004
Revises: 20260306_0003
Create Date: 2026-03-06

LLM analysis can return combined criticism codes like
'R2, R3, R4 (Tardivitate)' which exceed 10 characters.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '20260306_0004'
down_revision = '20260306_0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE argumentare_critica "
        "ALTER COLUMN cod_critica TYPE varchar(100)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE argumentare_critica "
        "ALTER COLUMN cod_critica TYPE varchar(10)"
    )
