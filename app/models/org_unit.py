from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


"""
Define the organizational unit type (OU type).
E.g., Corp., Department, Division, Branch, Project, Team, ...
Corp. is the only root (parent_id = None), and cannot be deleted.
The OU type hierarchy defines the structure — parent nodes can always view documents of child nodes.
"""
class OrgUnit(Base, TimestampMixin):
    __tablename__ = "org_units"

    id          = Column(String(36), primary_key=True, default=new_uuid)
    name        = Column(String(128), nullable=False, unique=True)
    parent_id   = Column(String(36), ForeignKey("org_units.id"), nullable=True, index=True)

    parent      = relationship("OrgUnit", remote_side="OrgUnit.id", back_populates="children")
    children    = relationship("OrgUnit", back_populates="parent")
    instances   = relationship("OrgUnitInstance", back_populates="ou", cascade="all, delete-orphan")
    positions   = relationship("Position", back_populates="ou", cascade="all, delete-orphan")
