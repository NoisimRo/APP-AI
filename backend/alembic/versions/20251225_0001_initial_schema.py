"""initial schema

Revision ID: 20251225_0001
Revises:
Create Date: 2025-12-25

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = '20251225_0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable extensions
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')

    # Create nomenclator_cpv table
    op.create_table('nomenclator_cpv',
        sa.Column('cod_cpv', sa.String(length=20), nullable=False),
        sa.Column('descriere', sa.Text(), nullable=False),
        sa.Column('categorie_achizitii', sa.String(length=50), nullable=True),
        sa.Column('clasa_produse', sa.String(length=200), nullable=True),
        sa.Column('cod_parinte', sa.String(length=20), nullable=True),
        sa.Column('nivel', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('cod_cpv')
    )
    op.create_index('ix_cpv_categorie', 'nomenclator_cpv', ['categorie_achizitii'], unique=False)
    op.create_index('ix_cpv_clasa', 'nomenclator_cpv', ['clasa_produse'], unique=False)

    # Create decizii_cnsc table
    op.create_table('decizii_cnsc',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('numar_bo', sa.Integer(), nullable=False),
        sa.Column('an_bo', sa.Integer(), nullable=False),
        sa.Column('numar_decizie', sa.Integer(), nullable=True),
        sa.Column('complet', sa.String(length=5), nullable=True),
        sa.Column('data_decizie', sa.DateTime(), nullable=True),
        sa.Column('tip_contestatie', sa.String(length=20), nullable=False),
        sa.Column('coduri_critici', postgresql.ARRAY(sa.String(length=10)), nullable=False),
        sa.Column('cod_cpv', sa.String(length=20), nullable=True),
        sa.Column('cpv_descriere', sa.Text(), nullable=True),
        sa.Column('cpv_categorie', sa.String(length=50), nullable=True),
        sa.Column('cpv_clasa', sa.String(length=200), nullable=True),
        sa.Column('cpv_source', sa.String(length=20), nullable=True),
        sa.Column('solutie_filename', sa.String(length=1), nullable=True),
        sa.Column('solutie_contestatie', sa.String(length=20), nullable=True),
        sa.Column('motiv_respingere', sa.String(length=50), nullable=True),
        sa.Column('data_initiere_procedura', sa.DateTime(), nullable=True),
        sa.Column('data_raport_procedura', sa.DateTime(), nullable=True),
        sa.Column('numar_anunt_participare', sa.String(length=50), nullable=True),
        sa.Column('valoare_estimata', sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column('moneda', sa.String(length=3), nullable=False),
        sa.Column('criteriu_atribuire', sa.String(length=100), nullable=True),
        sa.Column('numar_oferte', sa.Integer(), nullable=True),
        sa.Column('contestator', sa.String(length=500), nullable=True),
        sa.Column('autoritate_contractanta', sa.String(length=500), nullable=True),
        sa.Column('intervenienti', sa.JSON(), nullable=True),
        sa.Column('text_integral', sa.Text(), nullable=False),
        sa.Column('parse_warnings', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_decizii_bo_unique', 'decizii_cnsc', ['an_bo', 'numar_bo'], unique=True)
    op.create_index('ix_decizii_complet', 'decizii_cnsc', ['complet'], unique=False)
    op.create_index('ix_decizii_cpv', 'decizii_cnsc', ['cod_cpv'], unique=False)
    op.create_index('ix_decizii_critici', 'decizii_cnsc', ['coduri_critici'], unique=False, postgresql_using='gin')
    op.create_index('ix_decizii_data', 'decizii_cnsc', ['data_decizie'], unique=False)
    op.create_index('ix_decizii_fulltext', 'decizii_cnsc', ['text_integral'], unique=False,
                    postgresql_using='gin', postgresql_ops={'text_integral': 'gin_trgm_ops'})
    op.create_index('ix_decizii_solutie', 'decizii_cnsc', ['solutie_contestatie'], unique=False)
    op.create_index('ix_decizii_tip', 'decizii_cnsc', ['tip_contestatie'], unique=False)
    op.create_index(op.f('ix_decizii_cnsc_filename'), 'decizii_cnsc', ['filename'], unique=True)

    # Create sectiuni_decizie table
    op.create_table('sectiuni_decizie',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('decizie_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('tip_sectiune', sa.String(length=50), nullable=False),
        sa.Column('ordine', sa.Integer(), nullable=False),
        sa.Column('numar_intervenient', sa.Integer(), nullable=True),
        sa.Column('text_sectiune', sa.Text(), nullable=False),
        sa.Column('embedding_id', postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['decizie_id'], ['decizii_cnsc.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sectiuni_decizie', 'sectiuni_decizie', ['decizie_id'], unique=False)
    op.create_index('ix_sectiuni_tip', 'sectiuni_decizie', ['tip_sectiune'], unique=False)

    # Create argumentare_critica table
    op.create_table('argumentare_critica',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('decizie_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('cod_critica', sa.String(length=10), nullable=False),
        sa.Column('ordine_in_decizie', sa.Integer(), nullable=True),
        sa.Column('argumente_contestator', sa.Text(), nullable=True),
        sa.Column('jurisprudenta_contestator', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column('argumente_ac', sa.Text(), nullable=True),
        sa.Column('jurisprudenta_ac', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column('argumente_intervenienti', sa.JSON(), nullable=True),
        sa.Column('elemente_retinute_cnsc', sa.Text(), nullable=True),
        sa.Column('argumentatie_cnsc', sa.Text(), nullable=True),
        sa.Column('jurisprudenta_cnsc', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column('castigator_critica', sa.String(length=20), nullable=False),
        sa.Column('embedding_id', postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column('embedding', Vector(768), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['decizie_id'], ['decizii_cnsc.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_arg_castigator', 'argumentare_critica', ['castigator_critica'], unique=False)
    op.create_index('ix_arg_critica', 'argumentare_critica', ['cod_critica'], unique=False)
    op.create_index('ix_arg_decizie', 'argumentare_critica', ['decizie_id'], unique=False)
    op.create_index('ix_arg_embedding', 'argumentare_critica', ['embedding'], unique=False,
                    postgresql_using='ivfflat', postgresql_ops={'embedding': 'vector_cosine_ops'})

    # Create citate_verbatim table
    op.create_table('citate_verbatim',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('decizie_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('sectiune_id', postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column('argumentare_id', postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column('text_verbatim', sa.Text(), nullable=False),
        sa.Column('pozitie_start', sa.Integer(), nullable=True),
        sa.Column('pozitie_end', sa.Integer(), nullable=True),
        sa.Column('tip_citat', sa.String(length=30), nullable=True),
        sa.Column('embedding_id', postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column('embedding', Vector(768), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['argumentare_id'], ['argumentare_critica.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['decizie_id'], ['decizii_cnsc.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sectiune_id'], ['sectiuni_decizie.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_citate_decizie', 'citate_verbatim', ['decizie_id'], unique=False)
    op.create_index('ix_citate_tip', 'citate_verbatim', ['tip_citat'], unique=False)

    # Create referinte_articole table
    op.create_table('referinte_articole',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('decizie_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('argumentare_id', postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column('act_normativ', sa.String(length=50), nullable=False),
        sa.Column('articol', sa.String(length=30), nullable=False),
        sa.Column('tip_referinta', sa.String(length=20), nullable=True),
        sa.Column('text_citat', sa.Text(), nullable=True),
        sa.Column('invocat_de', sa.String(length=20), nullable=True),
        sa.Column('argument_castigator', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['argumentare_id'], ['argumentare_critica.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['decizie_id'], ['decizii_cnsc.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ref_articol', 'referinte_articole', ['act_normativ', 'articol'], unique=False)
    op.create_index('ix_ref_castigator', 'referinte_articole', ['argument_castigator'], unique=False)
    op.create_index('ix_ref_decizie', 'referinte_articole', ['decizie_id'], unique=False)
    op.create_index('ix_ref_invocat', 'referinte_articole', ['invocat_de'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_ref_invocat', table_name='referinte_articole')
    op.drop_index('ix_ref_decizie', table_name='referinte_articole')
    op.drop_index('ix_ref_castigator', table_name='referinte_articole')
    op.drop_index('ix_ref_articol', table_name='referinte_articole')
    op.drop_table('referinte_articole')

    op.drop_index('ix_citate_tip', table_name='citate_verbatim')
    op.drop_index('ix_citate_decizie', table_name='citate_verbatim')
    op.drop_table('citate_verbatim')

    op.drop_index('ix_arg_embedding', table_name='argumentare_critica')
    op.drop_index('ix_arg_decizie', table_name='argumentare_critica')
    op.drop_index('ix_arg_critica', table_name='argumentare_critica')
    op.drop_index('ix_arg_castigator', table_name='argumentare_critica')
    op.drop_table('argumentare_critica')

    op.drop_index('ix_sectiuni_tip', table_name='sectiuni_decizie')
    op.drop_index('ix_sectiuni_decizie', table_name='sectiuni_decizie')
    op.drop_table('sectiuni_decizie')

    op.drop_index(op.f('ix_decizii_cnsc_filename'), table_name='decizii_cnsc')
    op.drop_index('ix_decizii_tip', table_name='decizii_cnsc')
    op.drop_index('ix_decizii_solutie', table_name='decizii_cnsc')
    op.drop_index('ix_decizii_fulltext', table_name='decizii_cnsc')
    op.drop_index('ix_decizii_data', table_name='decizii_cnsc')
    op.drop_index('ix_decizii_critici', table_name='decizii_cnsc')
    op.drop_index('ix_decizii_cpv', table_name='decizii_cnsc')
    op.drop_index('ix_decizii_complet', table_name='decizii_cnsc')
    op.drop_index('ix_decizii_bo_unique', table_name='decizii_cnsc')
    op.drop_table('decizii_cnsc')

    op.drop_index('ix_cpv_clasa', table_name='nomenclator_cpv')
    op.drop_index('ix_cpv_categorie', table_name='nomenclator_cpv')
    op.drop_table('nomenclator_cpv')

    op.execute('DROP EXTENSION IF EXISTS pg_trgm')
    op.execute('DROP EXTENSION IF EXISTS vector')
