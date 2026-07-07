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
    from sqlalchemy import inspect
    cols = [c['name'] for c in inspect(op.get_bind()).get_columns('domain_entity_types')]
    if 'boolean_labels' not in cols:
        op.add_column(
            'domain_entity_types',
            sa.Column('boolean_labels', sa.JSON, nullable=True),
        )
        op.execute("UPDATE domain_entity_types SET boolean_labels = '[]' WHERE boolean_labels IS NULL")
        op.alter_column(
            'domain_entity_types', 'boolean_labels',
            existing_type=sa.JSON,
            nullable=False,
        )


def downgrade():
    op.drop_column('domain_entity_types', 'boolean_labels')
