"""add policy domain tables

Revision ID: 001_policy_tables
Revises: 000_initial
Create Date: 2026-06-23
"""
from alembic import op
import sqlalchemy as sa

revision = '001_policy_tables'
down_revision = '000_initial'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'policy_domains',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('code', sa.String(64), nullable=False, unique=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('base_sensitivity', sa.Integer, nullable=False, server_default='2'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_policy_domains_code', 'policy_domains', ['code'])

    op.create_table(
        'domain_entity_types',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('domain_id', sa.String(36), sa.ForeignKey('policy_domains.id'), nullable=False),
        sa.Column('entity_type', sa.String(128), nullable=False),
        sa.Column('label_vi', sa.String(255), nullable=True),
        sa.Column('is_system_suggested', sa.Boolean, nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('domain_id', 'entity_type', name='uq_domain_entity'),
    )
    op.create_index('ix_domain_entity_types_domain_id', 'domain_entity_types', ['domain_id'])

    op.create_table(
        'domain_rules',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('domain_id', sa.String(36), sa.ForeignKey('policy_domains.id'), nullable=True),
        sa.Column('rule_code', sa.String(64), nullable=False, unique=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('priority', sa.Integer, nullable=False, server_default='50'),
        sa.Column('mandatory', sa.Boolean, nullable=False, server_default='0'),
        sa.Column('risk_level', sa.String(32), nullable=False, server_default='low'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='1'),
        sa.Column('audit_log', sa.Boolean, nullable=False, server_default='1'),
        sa.Column('conditions_json', sa.JSON, nullable=False),
        sa.Column('contract_json', sa.JSON, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_domain_rules_domain_id', 'domain_rules', ['domain_id'])
    op.create_index('ix_domain_rules_rule_code', 'domain_rules', ['rule_code'])


def downgrade():
    op.drop_table('domain_rules')
    op.drop_table('domain_entity_types')
    op.drop_table('policy_domains')
