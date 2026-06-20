from sqlalchemy import Column, String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class UserOuiPosition(Base, TimestampMixin):
    """
    Junction: user thuộc một OUI với một Position cụ thể.
    UNIQUE(user_id, oui_id) — mỗi user chỉ có 1 position tại 1 OUI.

    Conflict rule (enforce ở service layer):
    - Không cho phép user có 2 records mà oui_id của chúng
      có quan hệ ancestor–descendant trong cây OUI.
    - Các OUI thuộc nhánh khác nhau → OK.
    """
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