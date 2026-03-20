from sqlalchemy import Column, String
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class Department(Base, TimestampMixin):
    __tablename__ = "departments"

    id = Column(String(36), primary_key=True, default=new_uuid)
    name = Column(String(255), nullable=False)

    users = relationship("User", back_populates="department")
    projects = relationship("Project", back_populates="department")
    documents = relationship("Document", back_populates="department")

    @property
    def department_id(self):
        return self.id
        
