"""add system_settings table

Revision ID: 002_system_settings
Revises: 001_policy_tables
Create Date: 2026-06-23
"""
from alembic import op
import sqlalchemy as sa

revision = '002_system_settings'
down_revision = '001_policy_tables'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect
    existing = inspect(op.get_bind()).get_table_names()
    if 'system_settings' not in existing:
        op.create_table(
            'system_settings',
            sa.Column('key', sa.String(128), primary_key=True),
            sa.Column('value', sa.Text, nullable=False),
            sa.Column('updated_at', sa.DateTime, nullable=False,
                      server_default=sa.func.now(), onupdate=sa.func.now()),
        )


def downgrade():
    op.drop_table('system_settings')
