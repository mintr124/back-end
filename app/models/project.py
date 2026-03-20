from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=new_uuid)
    name = Column(String(255), nullable=False)
    department_id = Column(String(36), ForeignKey("departments.id"), nullable=False, index=True)

    department = relationship("Department", back_populates="projects")
    documents = relationship("Document", back_populates="project")
