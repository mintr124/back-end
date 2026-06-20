# app/alembic/versions/xxx_fix_message_source_id_length.py
from alembic import op
import sqlalchemy as sa

revision = 'fix_message_source_id_length'
down_revision = 'd75c5bf28abf'  # revision cuối cùng của bạn
branch_labels = None
depends_on = None

def upgrade():
    op.alter_column('message_sources', 'document_id',
        existing_type=sa.String(36),
        type_=sa.String(255),
        existing_nullable=True)
    op.alter_column('message_sources', 'version_id',
        existing_type=sa.String(36),
        type_=sa.String(255),
        existing_nullable=True)

def downgrade():
    op.alter_column('message_sources', 'document_id',
        existing_type=sa.String(255),
        type_=sa.String(36),
        existing_nullable=True)
    op.alter_column('message_sources', 'version_id',
        existing_type=sa.String(255),
        type_=sa.String(36),
        existing_nullable=True)