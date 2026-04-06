from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.db.base import Base

class UserDepartment(Base):
    __tablename__ = "user_departments"
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    department_id = Column(String(36), ForeignKey("departments.id", ondelete="CASCADE"), primary_key=True)
    joined_at = Column(DateTime, server_default=func.now())