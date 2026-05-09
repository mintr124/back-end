"""add_chunk_config_json_to_document_versions

Revision ID: 694b9a88557d
Revises: 
Create Date: 2026-04-15 13:38:40.628439
"""
from alembic import op
import sqlalchemy as sa

revision = '694b9a88557d'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('document_versions', sa.Column('chunk_config_json', sa.JSON(), nullable=True))

def downgrade():
    op.drop_column('document_versions', 'chunk_config_json')