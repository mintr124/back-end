"""add document_access_requests table

Revision ID: 004_document_access_requests
Revises: 003_entity_type_boolean_labels
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa

revision = '004_document_access_requests'
down_revision = '003_entity_type_boolean_labels'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect
    existing = inspect(op.get_bind()).get_table_names()
    if 'document_access_requests' not in existing:
        op.create_table(
            'document_access_requests',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('document_id', sa.String(36),
                      sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.String(36),
                      sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
            sa.Column('expires_at', sa.DateTime, nullable=True),
            sa.Column('admin_id', sa.String(36),
                      sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('admin_note', sa.Text, nullable=True),
            sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
            sa.Column('resolved_at', sa.DateTime, nullable=True),
        )
        op.create_index('ix_dar_user_doc', 'document_access_requests', ['user_id', 'document_id'])
        op.create_index('ix_dar_status',   'document_access_requests', ['status'])

    # Seed default system setting for query scope
    op.execute(
        "INSERT IGNORE INTO system_settings (`key`, `value`, updated_at) "
        "VALUES ('query_scope_mode', 'full_db', NOW())"
    )


def downgrade():
    op.drop_index('ix_dar_status',   table_name='document_access_requests')
    op.drop_index('ix_dar_user_doc', table_name='document_access_requests')
    op.drop_table('document_access_requests')
