from sqlalchemy import Column, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


"""
Junction: user belongs to an OUI with a specific Position.
UNIQUE(user_id, oui_id) — each user has only one position at any given OUI.

Conflict rule (enforce at the service layer):
- Does not allow a user to have two records where the oui_id values
  have an ancestor–descendant relationship in the OUI tree.
- Various OUIs belonging to different branches → OK.
"""
class UserOuiPosition(Base, TimestampMixin):
    __tablename__ = "user_oui_positions"
    __table_args__ = (
        UniqueConstraint("user_id", "oui_id", name="uq_user_oui"),
    )

    id          = Column(String(36), primary_key=True, default=new_uuid)
    user_id     = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    oui_id      = Column(String(36), ForeignKey("org_unit_instances.id"), nullable=False, index=True)
    position_id = Column(String(36), ForeignKey("positions.id"), nullable=False, index=True)

    user        = relationship("User", back_populates="oui_positions")
    oui         = relationship("OrgUnitInstance", back_populates="user_positions")
    position    = relationship("Position", back_populates="user_positions")
