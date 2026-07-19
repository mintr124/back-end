from sqlalchemy import Column, ForeignKey, String, Table
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.models.document import document_oui
from app.utils.ids import new_uuid


oui_parent = Table(
    "oui_parents",
    Base.metadata,
    Column("oui_id",        String(36), ForeignKey("org_unit_instances.id"), primary_key=True),
    Column("parent_oui_id", String(36), ForeignKey("org_unit_instances.id"), primary_key=True),
)


class OrgUnitInstance(Base, TimestampMixin):
    __tablename__ = "org_unit_instances"

    id    = Column(String(36), primary_key=True, default=new_uuid)
    name  = Column(String(128), nullable=False)
    ou_id = Column(String(36), ForeignKey("org_units.id"), nullable=False, index=True)

    ou = relationship("OrgUnit", back_populates="instances")

    parents = relationship(
        "OrgUnitInstance",
        secondary=oui_parent,
        primaryjoin=lambda: OrgUnitInstance.id == oui_parent.c.oui_id,
        secondaryjoin=lambda: OrgUnitInstance.id == oui_parent.c.parent_oui_id,
        back_populates="children",
    )
    children = relationship(
        "OrgUnitInstance",
        secondary=oui_parent,
        primaryjoin=lambda: OrgUnitInstance.id == oui_parent.c.parent_oui_id,
        secondaryjoin=lambda: OrgUnitInstance.id == oui_parent.c.oui_id,
        back_populates="parents",
    )

    user_positions = relationship("UserOuiPosition", back_populates="oui", cascade="all, delete-orphan")
    documents = relationship("Document", secondary=document_oui, back_populates="ouis")
