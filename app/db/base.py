"""
SQLAlchemy declarative base and shared mixins used by all ORM models.
"""
from sqlalchemy import Column, DateTime, func
from sqlalchemy.orm import declarative_base

# Shared declarative base; all models must inherit from this.
Base = declarative_base()


# Mixin that adds auto-managed created_at and updated_at timestamp columns.
class TimestampMixin:
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
