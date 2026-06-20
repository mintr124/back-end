"""initial schema - create all tables

Revision ID: 000_initial
Revises: 
Create Date: 2026-06-20
"""
from alembic import op
from app.db.base import Base
from app.models import *  # noqa

revision = '000_initial'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

def downgrade():
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)