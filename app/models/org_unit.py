from sqlalchemy import Column, String, ForeignKey
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class OrgUnit(Base, TimestampMixin):
    """
    Định nghĩa loại đơn vị tổ chức (OU type).
    VD: Corp., Department, Division, Branch, Project, Team, ...
    Corp. là root duy nhất (parent_id = None), không được xóa.
    Cây OU type xác định hierarchy — node cha luôn xem được doc của node con.
    """
    __tablename__ = "org_units"

    id          = Column(String(36), primary_key=True, default=new_uuid)
    name        = Column(String(128), nullable=False, unique=True)
    parent_id   = Column(String(36), ForeignKey("org_units.id"), nullable=True, index=True)

    parent      = relationship("OrgUnit", remote_side="OrgUnit.id", back_populates="children")
    children    = relationship("OrgUnit", back_populates="parent")
    instances   = relationship("OrgUnitInstance", back_populates="ou", cascade="all, delete-orphan")
    positions   = relationship("Position", back_populates="ou", cascade="all, delete-orphan")