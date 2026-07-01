"""add boolean_labels to domain_entity_types

Revision ID: 003_entity_type_boolean_labels
Revises: 002_system_settings
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa

revision = '003_entity_type_boolean_labels'
down_revision = '002_system_settings'
branch_labels = None
depends_on = None


def upgrade():
    # MySQL không cho phép DEFAULT trên JSON → thêm nullable trước
    op.add_column(
        'domain_entity_types',
        sa.Column('boolean_labels', sa.JSON, nullable=True),
    )
    # Gán [] cho tất cả rows hiện có
    op.execute("UPDATE domain_entity_types SET boolean_labels = '[]' WHERE boolean_labels IS NULL")
    # Chuyển sang NOT NULL
    op.alter_column(
        'domain_entity_types', 'boolean_labels',
        existing_type=sa.JSON,
        nullable=False,
    )


def downgrade():
    op.drop_column('domain_entity_types', 'boolean_labels')
