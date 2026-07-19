from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


CLEARANCE_RANK = {
    "public":        1,
    "internal":      2,
    "confidential":  3,
    "restricted":    4,
    "top_secret":    5,
}


"""
Position (job title) within an OU type.
E.g., OU=Department → Position: Dept Manager (clearance=4), Deputy (clearance=3), Employee (clearance=2)
Clearance is attached to Position — user inherits clearance from the position they hold at that OUI.
"""
class Position(Base, TimestampMixin):
    __tablename__ = "positions"

    id          = Column(String(36), primary_key=True, default=new_uuid)
    name        = Column(String(128), nullable=False)
    ou_id       = Column(String(36), ForeignKey("org_units.id"), nullable=False, index=True)
    clearance   = Column(Integer, nullable=False, default=1)  # 1–5

    ou              = relationship("OrgUnit", back_populates="positions")
    user_positions  = relationship("UserOuiPosition", back_populates="position")
