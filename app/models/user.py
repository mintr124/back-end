from sqlalchemy import Column, String
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id            = Column(String(36), primary_key=True, default=new_uuid)
    email         = Column(String(255), unique=True, nullable=False, index=True)
    name          = Column(String(255), nullable=False)
    status        = Column(String(32), nullable=False, default="active")
    password_hash = Column(String(255), nullable=True)

    oui_positions = relationship(
        "UserOuiPosition",
        back_populates="user",
        cascade="all, delete-orphan",
    )